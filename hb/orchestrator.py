"""Running one task: every rep in fresh containers, then the task bundle.

For each rep attempt (container arguments come from hb/profile.py):

1. Create the internal network and the workspace, task and gateway-log volumes.
2. Create the helper container and copy in the pristine repository, owned by
   the harness user, and the instruction.
3. Start the gateway, wait until it listens, attach it to the internal network
   under the alias `gateway`.
4. Create and start the harness, then watch it until it exits or is killed:
   at the wall-clock limit (termination `wall_timeout`); a grace period after
   the gateway first refuses a request for budget (`budget_exhausted`); or as
   soon as the gateway log shows a void (`upstream_failed`).
5. Stop the gateway with a timeout longer than its drain period. A non-zero
   exit status fails the job: it means records could not be written, or the
   gateway was killed, and a full disk or an exhausted memory limit can be
   caused by the harness, so it must never become a re-roll.
6. Copy out gateway.jsonl. Any `upstream_failed` record makes the attempt void:
   it is discarded and the rep runs again in fresh containers, up to the
   suite's `max_reruns_per_rep` times; after that the job fails.
7. Copy out the harness's output and its workspace, and diff the workspace
   against pristine (hb/transfer.py, hb/patch.py).
8. Remove the attempt's containers, network and volumes, whatever happened.

Each rep is then graded (advisory; the aggregator re-grades) with its probe in
the sandbox container, and the bundle is written: five files per rep, the
manifest, and a deterministic archive.

Everything that fails here raises, and the job fails, so the run cannot be
sealed. Nothing is retried except a void attempt.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import plan, profile
from .dockercli import DEFAULT_TIMEOUT_S, Clock, Docker, DockerError
from .grade import MAX_OBSERVATION_BYTES, grade_rep
from .identity import cohort_from_suite
from .manifest import MANIFEST_NAME, build_task_manifest, pack_bundle, tree_digest
from .patch import workspace_diff
from .transfer import TransferError, extract_snapshot, read_single_file, tree_entries, write_tar
from .tree import materialize

LIFECYCLE_SCHEMA = "hb/lifecycle/1"
GATEWAY_LOG = "/log/gateway.jsonl"
LISTENING = b"hb-gateway listening on "
OUTCOMES = frozenset({"ok", "upstream_rejected", "budget_exhausted", "bad_request", "usage_unknown",
                      "upstream_failed"})

# What the toolkit accepts from one attempt. More than this fails the job.
MAX_WORKSPACE_BYTES = 1 << 30
MAX_WORKSPACE_ENTRIES = 200_000
MAX_GATEWAY_LOG_BYTES = 256 << 20
MAX_HARNESS_LOG_BYTES = 32 << 20
PROBE_START_SLACK_S = 10
LOG_READ_TIMEOUT_S = 30  # one read of the gateway log while the harness runs


class RunTaskError(Exception):
    """The task job cannot produce a bundle. Fails the job, so the run cannot be sealed."""


class GatewayFailed(RunTaskError):
    pass


class RerunsExhausted(RunTaskError):
    pass


@dataclass(frozen=True)
class Timing:
    poll_s: float = 2.0
    budget_grace_s: float = plan.BUDGET_GRACE_SECONDS
    ready_timeout_s: float = 30.0
    stop_timeout_s: float = 30.0          # waiting for a killed container to stop
    gateway_stop_grace_s: int = plan.GATEWAY_DRAIN_SECONDS + 30
    snapshot_timeout_s: float = 600.0


DEFAULT_TIMING = Timing()


@dataclass(frozen=True)
class TaskJob:
    suite: dict
    suite_dir: Path
    task_id: str
    harness: dict        # registry entry: image, entrypoint
    toolkit_image: str
    run: dict            # {"repository", "run_id", "run_attempt"}


@dataclass
class Attempt:
    void: bool
    started_at_ms: int = 0
    ended_at_ms: int = 0
    termination: str = ""
    exit_code: int = 0
    gateway_log: bytes = b""
    harness_log: bytes = b""
    patch: bytes = b""


def _stderr(message: str) -> None:
    print(f"hb run-task: {message}", file=sys.stderr, flush=True)


def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


# ----------------------------------------------------------- gateway records


class RecordTail:
    """Follows a growing gateway log during an attempt.

    Only complete lines are read, and each only once. This is for reacting
    early; the decision on a void is made from the final log after the
    gateway has stopped (parse_records).
    """

    def __init__(self):
        self.offset = 0
        self.void = False
        self.first_refusal_ms = None  # when the gateway answered its first budget refusal

    def feed(self, data: bytes) -> None:
        end = data.rfind(b"\n") + 1
        if end <= self.offset:
            return
        for line in data[self.offset:end].splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            outcome = record.get("outcome") if isinstance(record, dict) else None
            self.void |= outcome == "upstream_failed"
            if outcome == "budget_exhausted":
                started, duration = record.get("started_at_ms"), record.get("duration_ms")
                if not (type(started) is int and type(duration) is int):
                    continue  # malformed; the final parse of the log decides
                answered = started + duration
                if self.first_refusal_ms is None or answered < self.first_refusal_ms:
                    self.first_refusal_ms = answered
        self.offset = end


def parse_records(data: bytes) -> list[dict]:
    """Parse a complete gateway log. Anything malformed fails the job: the gateway wrote it."""
    if data and not data.endswith(b"\n"):
        raise RunTaskError("the gateway log ends with an incomplete record")
    records = []
    for number, line in enumerate(data.splitlines(), start=1):
        try:
            record = json.loads(line)
        except ValueError:
            raise RunTaskError(f"gateway log line {number} is not JSON") from None
        if not isinstance(record, dict) or record.get("outcome") not in OUTCOMES:
            raise RunTaskError(f"gateway log line {number} is not a gateway record")
        records.append(record)
    return records


def _read_gateway_log(docker: Docker, gateway: str, timeout: float = DEFAULT_TIMEOUT_S) -> bytes:
    with docker.copy_out(gateway, GATEWAY_LOG, timeout=timeout) as stream:
        return read_single_file(stream, max_bytes=MAX_GATEWAY_LOG_BYTES)


def _poll_records(docker: Docker, gateway: str, tail: RecordTail, timeout: float) -> None:
    try:
        tail.feed(_read_gateway_log(docker, gateway, timeout))
    except (DockerError, TransferError):
        pass  # a copy can fail while the log grows; the final read after the gateway stops decides


# ------------------------------------------------------------------ attempt


def _wait_listening(docker: Docker, clock: Clock, gateway: str, timing: Timing) -> None:
    deadline = clock.monotonic() + timing.ready_timeout_s
    while True:
        output = docker.logs(gateway, 1 << 16)
        if LISTENING in output:
            return
        state = docker.states([gateway])[gateway]
        if not state["running"]:
            tail = output.decode("utf-8", errors="replace").strip()[-500:]
            raise GatewayFailed(f"the gateway exited with status {state['exit_code']} before listening: {tail}")
        if clock.monotonic() >= deadline:
            raise GatewayFailed(f"the gateway was not listening after {timing.ready_timeout_s:.0f}s")
        clock.sleep(0.2)


def _kill_and_wait(docker: Docker, clock: Clock, name: str, timing: Timing) -> bool:
    """SIGKILL a container and wait until it has stopped. False if it had stopped by itself."""
    killed = docker.kill(name)
    deadline = clock.monotonic() + timing.stop_timeout_s
    while docker.states([name])[name]["running"]:
        if clock.monotonic() >= deadline:
            raise RunTaskError(f"{name} did not stop after SIGKILL")
        clock.sleep(0.2)
    return killed


def _watch_harness(docker: Docker, clock: Clock, names: dict, wall_seconds: int, timing: Timing) -> dict:
    """Start the harness and wait until it exits or is killed. Returns lifecycle facts.

    Deadlines are checked before the gateway log is read, and the read is
    bounded by the time left, so a large log cannot delay a kill. A harness
    that stopped by itself before the kill landed is `exited`.
    """
    harness, gateway = names["harness"], names["gateway"]
    started_ms = clock.now_ms()
    start = clock.monotonic()
    docker.check(["docker", "start", harness])
    wall_deadline = start + wall_seconds
    grace_deadline = None
    tail = RecordTail()
    termination = None
    while termination is None:
        states = docker.states([harness, gateway])
        now = clock.monotonic()
        if not states[harness]["running"]:
            termination = "exited"
        elif now >= wall_deadline:
            termination = "wall_timeout" if _kill_and_wait(docker, clock, harness, timing) else "exited"
        elif grace_deadline is not None and now >= grace_deadline:
            termination = "budget_exhausted" if _kill_and_wait(docker, clock, harness, timing) else "exited"
        elif not states[gateway]["running"]:
            raise GatewayFailed("the gateway stopped while the harness was running")
        else:
            next_deadline = wall_deadline if grace_deadline is None else min(wall_deadline, grace_deadline)
            _poll_records(docker, gateway, tail, timeout=max(1.0, min(LOG_READ_TIMEOUT_S, next_deadline - now)))
            if tail.void:
                _kill_and_wait(docker, clock, harness, timing)
                termination = "exited"  # the attempt is void; its lifecycle is discarded
                break
            if tail.first_refusal_ms is not None and grace_deadline is None:
                # The grace period runs from the gateway's answer, not from when the log was read.
                since_s = max(0, clock.now_ms() - tail.first_refusal_ms) / 1000
                grace_deadline = clock.monotonic() + timing.budget_grace_s - since_s
                next_deadline = min(wall_deadline, grace_deadline)
            clock.sleep(max(0.0, min(timing.poll_s, next_deadline - clock.monotonic())))
    ended_ms = clock.now_ms()
    exit_code = docker.states([harness])[harness]["exit_code"]
    if not 0 <= exit_code <= 255:
        raise RunTaskError(f"the harness container reported exit status {exit_code}")
    return {"started_at_ms": started_ms, "ended_at_ms": max(ended_ms, started_ms),
            "termination": termination, "exit_code": exit_code}


def _snapshot_patch(docker: Docker, helper: str, pristine: Path, scratch: Path, ignore: list[str],
                    timing: Timing) -> bytes:
    raw = scratch / "snapshot"
    with docker.copy_out(helper, "/workspace", timeout=timing.snapshot_timeout_s) as stream:
        extract_snapshot(stream, raw, root="workspace", max_bytes=MAX_WORKSPACE_BYTES,
                         max_entries=MAX_WORKSPACE_ENTRIES)
    return workspace_diff(pristine, raw / "workspace", scratch / "diff", ignore)


def _attempt_resources(names: dict) -> dict:
    return {
        "containers": [names["harness"], names["gateway"], names["helper"]],
        "networks": [names["network"]],
        "volumes": [names["workspace"], names["task"], names["gateway_log"]],
    }


def run_attempt(job: TaskJob, cohort: dict, rep: int, attempt: int, *, docker: Docker, clock: Clock,
                pristine: Path, scratch: Path, timing: Timing = DEFAULT_TIMING) -> Attempt:
    names = profile.rep_names(job.task_id, rep, attempt)
    resources = _attempt_resources(names)
    docker.remove(**resources)  # leftovers from an earlier run on the same daemon
    try:
        return _run_attempt(job, cohort, names, docker=docker, clock=clock, pristine=pristine,
                            scratch=scratch, timing=timing)
    finally:
        for error in docker.remove(**resources):
            _stderr(f"warning: {error}")


def _run_attempt(job: TaskJob, cohort: dict, names: dict, *, docker: Docker, clock: Clock,
                 pristine: Path, scratch: Path, timing: Timing) -> Attempt:
    suite = job.suite
    task_dir = job.suite_dir / "tasks" / job.task_id
    task = _load_json(task_dir / "task.json")
    scratch.mkdir(parents=True, exist_ok=False)

    docker.check(profile.network_create_args(names))
    for volume in (names["workspace"], names["task"], names["gateway_log"]):
        docker.check(profile.volume_create_args(volume))
    docker.check(profile.helper_create_args(names, job.toolkit_image))
    workspace_tar, task_tar = scratch / "workspace.tar", scratch / "task.tar"
    write_tar(workspace_tar, tree_entries(pristine), uid=profile.HARNESS_UID, gid=profile.HARNESS_UID)
    write_tar(task_tar, [("instruction.md", task_dir / task["instruction"])], uid=0, gid=0)
    docker.copy_in(names["helper"], "/workspace", workspace_tar)
    docker.copy_in(names["helper"], "/task", task_tar)

    docker.check(profile.gateway_run_args(names, cohort, job.toolkit_image, suite["infra"]["upstream_retries"]))
    _wait_listening(docker, clock, names["gateway"], timing)
    docker.check(profile.gateway_connect_args(names))

    docker.check(profile.harness_create_args(names, cohort, job.harness["image"], job.harness["entrypoint"]))
    facts = _watch_harness(docker, clock, names, cohort["budget"]["wall_seconds"], timing)

    docker.stop(names["gateway"], timing.gateway_stop_grace_s)
    gateway_exit = docker.states([names["gateway"]])[names["gateway"]]["exit_code"]
    if gateway_exit != 0:
        raise GatewayFailed(f"the gateway exited with status {gateway_exit}; its records cannot be trusted")
    gateway_log = _read_gateway_log(docker, names["gateway"])
    if any(r["outcome"] == "upstream_failed" for r in parse_records(gateway_log)):
        return Attempt(void=True)

    harness_log = docker.logs(names["harness"], MAX_HARNESS_LOG_BYTES)
    patch = _snapshot_patch(docker, names["helper"], pristine, scratch, suite["ignore_paths"], timing)
    return Attempt(void=False, gateway_log=gateway_log, harness_log=harness_log, patch=patch, **facts)


def run_rep(job: TaskJob, cohort: dict, rep: int, *, docker: Docker, clock: Clock, pristine: Path,
            work: Path, timing: Timing = DEFAULT_TIMING) -> tuple[Attempt, int]:
    """Run attempts until one is not void. Returns it and the number of attempts taken."""
    limit = 1 + job.suite["infra"]["max_reruns_per_rep"]
    for attempt in range(1, limit + 1):
        result = run_attempt(job, cohort, rep, attempt, docker=docker, clock=clock, pristine=pristine,
                             scratch=work / f"r{rep}a{attempt}", timing=timing)
        if not result.void:
            seconds = (result.ended_at_ms - result.started_at_ms) / 1000
            _stderr(f"rep {rep} attempt {attempt}: {result.termination}, exit status {result.exit_code}, "
                    f"{seconds:.0f}s")
            return result, attempt
        _stderr(f"rep {rep} attempt {attempt}: void (the provider failed); "
                + ("running it again" if attempt < limit else "no re-runs left"))
    raise RerunsExhausted(f"rep {rep}: every allowed attempt ({limit}) was void")


# ------------------------------------------------------------------ grading


class DockerProbeRunner:
    """hb.grade.ProbeRunner that runs the probe in the sandbox container.

    The patched tree, the probe program and its inputs are copied into a fresh
    volume, which the sandbox mounts read-only. Returns the probe's stdout, or
    b"" when it runs past its time limit or writes more than a report can be.
    Raises only when the sandbox itself cannot be set up or started.
    """

    def __init__(self, docker: Docker, toolkit_image: str, names: dict, scratch: Path):
        self.docker, self.image, self.names, self.scratch = docker, toolkit_image, names, Path(scratch)

    def __call__(self, program: Path, tree: Path, inputs: Path, timeout_seconds: int) -> bytes:
        docker, names = self.docker, self.names
        resources = {"containers": [names["sandbox"], names["helper"]], "volumes": [names["volume"]]}
        docker.remove(**resources)
        try:
            docker.check(profile.volume_create_args(names["volume"]))
            docker.check(profile.probe_helper_args(names, self.image))
            self.scratch.mkdir(parents=True, exist_ok=True)
            tar_path = self.scratch / "probe.tar"
            entries = ([("tree", Path(tree))] + tree_entries(Path(tree), prefix="tree/")
                       + [("program", Path(program).parent), (f"program/{Path(program).name}", Path(program)),
                          ("inputs", Path(inputs).parent), (f"inputs/{Path(inputs).name}", Path(inputs))])
            write_tar(tar_path, entries, uid=0, gid=0)
            docker.copy_in(names["helper"], "/probe", tar_path)
            docker.check(profile.probe_create_args(names, self.image, Path(program).name, Path(inputs).name))
            status, output = docker.capture(["docker", "start", "--attach", names["sandbox"]],
                                            timeout=timeout_seconds + PROBE_START_SLACK_S,
                                            max_stdout=MAX_OBSERVATION_BYTES)
            if status is None:  # over time, or more output than a report can be
                docker.kill(names["sandbox"])
                return b""
            error = docker.states([names["sandbox"]])[names["sandbox"]]["error"]
            if error:
                raise DockerError(f"the probe sandbox could not start: {error}")
            return output
        finally:
            for problem in docker.remove(**resources):
                _stderr(f"warning: {problem}")


# --------------------------------------------------------------------- task


def _ensure_image(docker: Docker, ref: str) -> None:
    if not docker.image_present(ref):
        docker.pull(ref)


def run_task(job: TaskJob, *, docker: Docker, clock: Clock, out_dir: Path, work_dir: Path,
             timing: Timing = DEFAULT_TIMING,
             probe_runner: Callable[[int, Path], Callable] | None = None) -> Path:
    """Run every rep of one task and write out_dir/task-bundle-<task>.tar. Returns its path.

    probe_runner(rep, scratch) builds the probe runner for a rep; by default
    the sandbox container (DockerProbeRunner).
    """
    suite = job.suite
    if job.task_id not in [t["id"] for t in suite["tasks"]]:
        raise RunTaskError(f"task {job.task_id!r} is not in the suite")
    task_dir = job.suite_dir / "tasks" / job.task_id
    task = _load_json(task_dir / "task.json")
    cohort = cohort_from_suite(suite, tree_digest(job.suite_dir))
    work_dir, out_dir = Path(work_dir).resolve(), Path(out_dir)
    work_dir.mkdir(parents=True, exist_ok=False)
    if probe_runner is None:
        def probe_runner(rep, scratch):
            return DockerProbeRunner(docker, job.toolkit_image, profile.probe_names(job.task_id, rep), scratch)

    _ensure_image(docker, job.toolkit_image)
    _ensure_image(docker, job.harness["image"])
    pristine = work_dir / "pristine"
    materialize(task_dir / task["repo"], pristine, suite["ignore_paths"])

    bundle = work_dir / "bundle"
    for rep in range(1, suite["reps"] + 1):
        result, attempts = run_rep(job, cohort, rep, docker=docker, clock=clock, pristine=pristine,
                                   work=work_dir, timing=timing)
        rep_dir = bundle / "reps" / str(rep)
        rep_dir.mkdir(parents=True)
        _dump(rep_dir / "lifecycle.json", {
            "schema": LIFECYCLE_SCHEMA,
            "task_id": job.task_id,
            "rep": rep,
            "attempts": attempts,
            "harness_image": job.harness["image"],
            "started_at_ms": result.started_at_ms,
            "ended_at_ms": result.ended_at_ms,
            "termination": result.termination,
            "exit_code": result.exit_code,
        })
        (rep_dir / "gateway.jsonl").write_bytes(result.gateway_log)
        (rep_dir / "workspace.patch").write_bytes(result.patch)
        (rep_dir / "harness.log").write_bytes(result.harness_log)
        grade = grade_rep(suite=suite, task_dir=task_dir, patch=result.patch, rep=rep,
                          grader_image=job.toolkit_image, workdir=work_dir / f"grade-r{rep}",
                          run_probe=probe_runner(rep, work_dir / f"probe-r{rep}"))
        _dump(rep_dir / "grade.json", grade)
        _stderr(f"rep {rep}: resolved={grade['resolved']} (advisory)")

    _dump(bundle / MANIFEST_NAME, build_task_manifest(bundle, run=job.run, task_id=job.task_id, reps=suite["reps"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"task-bundle-{job.task_id}.tar"
    pack_bundle(bundle, target)
    return target
