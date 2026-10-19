"""The Docker CLI wrapper, against a scripted stand-in for the `docker` binary.

The stand-in is chosen by Docker(binary=...); no daemon is involved.
"""

import stat
import sys
import time

import pytest

from hb.dockercli import Docker, DockerError

FAKE = r'''
import json, os, sys, time
args = sys.argv[1:]
cmd = args[0]
if cmd == "echo":
    sys.stdout.write(args[1])
elif cmd == "cat":
    sys.stdout.buffer.write(sys.stdin.buffer.read())
elif cmd == "fail":
    sys.stderr.write("boom: " + args[1])
    sys.exit(3)
elif cmd == "sleep":
    time.sleep(float(args[1]))
elif cmd == "flood":
    sys.stdout.buffer.write(b"x" * int(args[1]))
    sys.stdout.flush()
    time.sleep(30)
elif cmd == "linger":  # closes stdout but keeps running
    sys.stdout.write("partial")
    sys.stdout.flush()
    os.close(1)
    time.sleep(30)
elif cmd == "stream":  # writes, then exits with the given status
    sys.stdout.buffer.write(b"y" * int(args[1]))
    sys.stdout.flush()
    sys.stderr.write("stream status " + args[2])
    sys.exit(int(args[2]))
elif cmd == "kill":
    if args[1] != "running":
        sys.stderr.write("Error response from daemon: container is not running")
        sys.exit(1)
elif cmd == "container":  # container inspect --format '{{json .State}}' NAMES...
    for name in args[4:]:
        print(json.dumps({"Running": name in ("running", "stuck"), "ExitCode": 0, "Error": ""}))
elif cmd == "logs":
    sys.stdout.write("o" * 10)
    sys.stdout.flush()
    sys.stderr.write("e" * 10)
elif cmd in ("rm", "network", "volume"):
    name = args[-1]
    if name == "missing":
        sys.stderr.write("Error response from daemon: No such object: missing")
        sys.exit(1)
    if name == "busy":
        sys.stderr.write("Error response from daemon: conflict: in use")
        sys.exit(1)
else:
    sys.exit(99)
'''


@pytest.fixture
def docker(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text(f"#!{sys.executable}\n{FAKE}")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return Docker(binary=str(fake))


def test_commands_must_start_with_docker(docker):
    with pytest.raises(ValueError):
        docker.check(["sh", "-c", "true"])


def test_check_returns_stdout_and_raises_on_failure(docker):
    assert docker.check(["docker", "echo", "hello"]) == b"hello"
    with pytest.raises(DockerError, match="exited 3: boom: x"):
        docker.check(["docker", "fail", "x"])


def test_timeouts_raise(docker):
    start = time.monotonic()
    with pytest.raises(DockerError, match="timed out"):
        docker.check(["docker", "sleep", "30"], timeout=0.5)
    assert time.monotonic() - start < 10


def test_capture(docker):
    assert docker.capture(["docker", "echo", "report"], timeout=10, max_stdout=100) == (0, b"report")
    assert docker.capture(["docker", "sleep", "30"], timeout=0.5, max_stdout=100) == (None, b"")
    status, out = docker.capture(["docker", "flood", "5000"], timeout=10, max_stdout=1000)
    assert status is None and len(out) > 1000  # more than a report can be: stopped early
    start = time.monotonic()
    assert docker.capture(["docker", "linger"], timeout=1, max_stdout=100) == (None, b"partial")
    assert time.monotonic() - start < 10


def test_stream_out(docker):
    with docker.stream_out(["docker", "stream", "100", "0"], timeout=10) as stream:
        assert stream.read(10) == b"y" * 10  # the rest is drained when the block ends
    with pytest.raises(DockerError, match="exited 2: stream status 2"):
        with docker.stream_out(["docker", "stream", "100", "2"], timeout=10) as stream:
            stream.read()
    with pytest.raises(DockerError, match="timed out"):
        with docker.stream_out(["docker", "sleep", "30"], timeout=0.5) as stream:
            stream.read()
    with pytest.raises(KeyError):  # an error inside the block propagates, and the command is killed
        with docker.stream_out(["docker", "sleep", "30"], timeout=60):
            raise KeyError("inside")


def test_kill_reports_whether_it_stopped_the_container(docker):
    assert docker.kill("running") is True
    assert docker.kill("stopped") is False  # had stopped by itself: not an error
    with pytest.raises(DockerError):
        docker.kill("stuck")  # still running after a failed kill


def test_states(docker):
    states = docker.states(["running", "stopped"])
    assert states == {"running": {"running": True, "exit_code": 0, "error": ""},
                      "stopped": {"running": False, "exit_code": 0, "error": ""}}


def test_logs_combine_both_streams_and_keep_the_tail(docker):
    out = docker.logs("c", max_bytes=1000)
    assert sorted(out.decode()) == sorted("o" * 10 + "e" * 10)
    assert len(docker.logs("c", max_bytes=5)) == 5


def test_remove_ignores_missing_objects_and_reports_the_rest(docker):
    assert docker.remove(containers=["a", "missing"], networks=["missing"], volumes=["v"]) == []
    errors = docker.remove(containers=["busy"])
    assert len(errors) == 1 and "conflict" in errors[0]


def test_inputs_from_a_file(docker, tmp_path):
    data = tmp_path / "in.tar"
    data.write_bytes(b"abc")
    assert docker.check(["docker", "cat"], stdin=data) == b"abc"
    assert docker.check(["docker", "cat"], stdin=b"bytes") == b"bytes"
