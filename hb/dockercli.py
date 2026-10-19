"""A thin wrapper around the Docker CLI, plus a clock.

Every command is an argument list run without a shell. Container profiles are
built by hb/profile.py; this module only runs commands, copies tar streams in
and out of containers and reads container state. Every call has a timeout. A
command that fails or times out raises DockerError, which fails the task job.

The orchestrator takes a Docker and a Clock as parameters, so its logic is
unit-tested with fakes (tests/test_orchestrator.py) and its Docker semantics
with a real daemon (tests/test_run_task_docker.py).
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
from pathlib import Path

DEFAULT_TIMEOUT_S = 120.0
PULL_TIMEOUT_S = 900.0
_CHUNK = 1 << 16


class DockerError(Exception):
    """A Docker command failed or timed out. Fails the task job."""


class Clock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000


def _describe(args: list[str]) -> str:
    return " ".join(args[:4]) + (" ..." if len(args) > 4 else "")


def _stderr_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()[-2000:]


class Docker:
    """Runs `docker` commands. Arguments start with "docker", as hb/profile.py builds them."""

    def __init__(self, binary: str = "docker"):
        self.binary = binary

    def _argv(self, args: list[str]) -> list[str]:
        if not args or args[0] != "docker":
            raise ValueError("docker commands must start with 'docker'")
        return [self.binary, *args[1:]]

    # ------------------------------------------------------------- primitives

    def run(self, args: list[str], *, stdin: bytes | Path | None = None,
            timeout: float = DEFAULT_TIMEOUT_S) -> subprocess.CompletedProcess:
        """Run a command and return it whatever its exit status. Raises DockerError on timeout.

        stdin is the command's input: bytes, or the path of a file to stream.
        """
        try:
            if isinstance(stdin, Path):
                with open(stdin, "rb") as fh:
                    return subprocess.run(self._argv(args), stdin=fh, capture_output=True,
                                          timeout=timeout, check=False)
            return subprocess.run(self._argv(args), input=stdin if stdin is not None else b"",
                                  capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise DockerError(f"timed out after {timeout:.0f}s: {_describe(args)}") from None
        except OSError as exc:
            raise DockerError(f"cannot run docker: {exc}") from None

    def check(self, args: list[str], *, stdin: bytes | Path | None = None,
              timeout: float = DEFAULT_TIMEOUT_S) -> bytes:
        """Run a command; return its stdout. Raises DockerError unless it exits 0."""
        proc = self.run(args, stdin=stdin, timeout=timeout)
        if proc.returncode != 0:
            raise DockerError(f"{_describe(args)} exited {proc.returncode}: {_stderr_text(proc.stderr)}")
        return proc.stdout

    def capture(self, args: list[str], *, timeout: float, max_stdout: int) -> tuple[int | None, bytes]:
        """Run a command, keeping at most max_stdout + 1 bytes of its stdout.

        Returns (exit status, stdout), with None as the status if the command was
        still running at the timeout or produced more output than max_stdout; it
        is then killed. Stderr is discarded.
        """
        proc = subprocess.Popen(self._argv(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        chunks, size = [], 0
        overflow = threading.Event()

        def reader():
            nonlocal size
            while size <= max_stdout:
                chunk = proc.stdout.read1(_CHUNK)
                if not chunk:
                    return
                chunks.append(chunk)
                size += len(chunk)
            overflow.set()

        deadline = time.monotonic() + timeout
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout)
        status = None
        if not thread.is_alive() and not overflow.is_set():
            try:  # stdout is closed; the command should be exiting
                status = proc.wait(max(1.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                status = None
        if status is None:
            proc.kill()
            proc.wait()
            thread.join(5)
        proc.stdout.close()
        return status, b"".join(chunks)

    @contextlib.contextmanager
    def stream_out(self, args: list[str], *, timeout: float):
        """Run a command and yield its stdout as a binary stream.

        Raises DockerError if the command fails or is still running at the
        timeout (it is then killed). The stream is valid inside the block only.
        """
        proc = subprocess.Popen(self._argv(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        timed_out = threading.Event()

        def expire():
            timed_out.set()
            proc.kill()

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        stderr = []
        drain = threading.Thread(target=lambda: stderr.append(proc.stderr.read()), daemon=True)
        drain.start()
        try:
            yield proc.stdout
            while proc.stdout.read(_CHUNK):  # let the command finish writing
                pass
        except BaseException:
            proc.kill()
            raise
        finally:
            status = proc.wait()
            timer.cancel()
            drain.join(5)
            proc.stdout.close()
        if status != 0 and timed_out.is_set():
            raise DockerError(f"timed out after {timeout:.0f}s: {_describe(args)}")
        if status != 0:
            raise DockerError(f"{_describe(args)} exited {status}: {_stderr_text(b''.join(stderr))}")

    # ------------------------------------------------------- common commands

    def copy_in(self, container: str, path: str, tar_path: Path, *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        """Extract a tar archive into a directory of a container, keeping its owners (--archive)."""
        self.check(["docker", "cp", "--archive", "-", f"{container}:{path}"], stdin=Path(tar_path), timeout=timeout)

    def copy_out(self, container: str, path: str, *, timeout: float = DEFAULT_TIMEOUT_S):
        """Context manager yielding a tar stream of a path in a container (running or not)."""
        return self.stream_out(["docker", "cp", f"{container}:{path}", "-"], timeout=timeout)

    def states(self, names: list[str]) -> dict:
        """{name: {"running": bool, "exit_code": int, "error": str}} for existing containers."""
        out = self.check(["docker", "container", "inspect", "--format", "{{json .State}}", *names])
        lines = out.decode("utf-8").splitlines()
        if len(lines) != len(names):
            raise DockerError(f"unexpected docker inspect output for {names}")
        result = {}
        for name, line in zip(names, lines, strict=True):
            state = json.loads(line)
            result[name] = {"running": bool(state["Running"]), "exit_code": int(state["ExitCode"]),
                            "error": str(state.get("Error") or "")}
        return result

    def kill(self, name: str) -> bool:
        """SIGKILL a container. Returns False if it had already stopped (not an error)."""
        proc = self.run(["docker", "kill", name])
        if proc.returncode == 0:
            return True
        if self.states([name])[name]["running"]:
            raise DockerError(f"docker kill {name} exited {proc.returncode}: {_stderr_text(proc.stderr)}")
        return False

    def stop(self, name: str, grace_s: int) -> None:
        """SIGTERM, then SIGKILL after grace_s seconds."""
        self.check(["docker", "stop", "--time", str(grace_s), name], timeout=grace_s + 60)

    def logs(self, name: str, max_bytes: int) -> bytes:
        """Stdout and stderr of a container, interleaved as the CLI writes them, capped."""
        argv = self._argv(["docker", "logs", name])
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=DEFAULT_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            raise DockerError(f"timed out reading the logs of {name}") from None
        if proc.returncode != 0:
            raise DockerError(f"docker logs {name} exited {proc.returncode}: {_stderr_text(proc.stdout)}")
        return proc.stdout[-max_bytes:] if len(proc.stdout) > max_bytes else proc.stdout

    def image_present(self, ref: str) -> bool:
        return self.run(["docker", "image", "inspect", "--format", "{{.Id}}", ref]).returncode == 0

    def pull(self, ref: str) -> None:
        self.check(["docker", "pull", "--quiet", ref], timeout=PULL_TIMEOUT_S)

    def remove(self, *, containers=(), networks=(), volumes=()) -> list[str]:
        """Best-effort removal; returns the errors (missing objects are not errors)."""
        errors = []
        for kind, names, argv in (("container", containers, ["docker", "rm", "--force", "--volumes"]),
                                  ("network", networks, ["docker", "network", "rm"]),
                                  ("volume", volumes, ["docker", "volume", "rm", "--force"])):
            for name in names:
                try:
                    proc = self.run([*argv, name])
                except DockerError as exc:
                    errors.append(str(exc))
                    continue
                text = _stderr_text(proc.stderr)
                if proc.returncode != 0 and "no such" not in text.lower() and "not found" not in text.lower():
                    errors.append(f"removing {kind} {name}: {text}")
        return errors
