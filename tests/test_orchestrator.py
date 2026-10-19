"""Orchestrator logic against a fake Docker and a fake clock.

The fake follows each attempt's script: when the harness exits, which gateway
records appear when, how the gateway exits, what the workspace holds. Real
Docker semantics are covered by tests/test_run_task_docker.py.
"""

import contextlib
import hashlib
import io
import json
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hb import orchestrator, profile
from hb.dockercli import DockerError
from hb.grade import run_probe_unsandboxed_for_tests
from hb.manifest import extract_bundle, verify_task_bundle
from hb.orchestrator import (
    DockerProbeRunner, GatewayFailed, RecordTail, RerunsExhausted, RunTaskError, TaskJob, Timing, parse_records,
    run_task,
)
from hb.schemas import validate
from hb.transfer import LimitExceeded

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_SUITE = ROOT / "examples" / "suite"
TASK_ID = "fix-pagination-off-by-one"
TOOLKIT = "ghcr.io/sigormatt/hb-toolkit@sha256:" + "d" * 64
HARNESS = {"id": "baseline", "image": "ghcr.io/sigormatt/hb-harness-baseline@sha256:" + "4" * 64,
           "entrypoint": ["/hb/entrypoint"]}
RUN = {"repository": "example-user/hb-runs", "run_id": 18000000001, "run_attempt": 1}
PRISTINE = (EXAMPLE_SUITE / "tasks" / TASK_ID / "repo" / "pager.py").read_text()
FIXED = PRISTINE.replace("start = number * size", "start = (number - 1) * size")
TIMING = Timing(poll_s=2.0, budget_grace_s=30.0)


def record(seq: int, outcome: str, started_ms: int = 1000) -> dict:
    forwarded = outcome in ("ok", "upstream_rejected", "usage_unknown", "upstream_failed")
    status = {"ok": 200, "upstream_rejected": 400, "budget_exhausted": 429, "bad_request": 400,
              "usage_unknown": 504, "upstream_failed": 502}[outcome]
    digest = "sha256:" + hashlib.sha256(f"{seq}{outcome}".encode()).hexdigest()
    return {
        "schema": "hb/gateway-record/1", "seq": seq, "started_at_ms": started_ms, "duration_ms": 10,
        "outcome": outcome, "http_status": status, "upstream_attempts": 1 if forwarded else 0,
        "stream": False, "model_rewritten": False,
        "usage": {"input_tokens": 100 if outcome == "ok" else 0, "cached_input_tokens": 0,
                  "output_tokens": 10 if outcome == "ok" else 0},
        "finish_reason": "stop" if outcome == "ok" else "none",
        "tool_calls": {"count": 0, "arguments_valid_json": 0},
        "request_sha256": digest, "response_sha256": digest,
        "totals": {"tokens": 110 * (seq + 1), "calls": seq + 1},
    }


@dataclass
class Script:
    exit_at: float | None = 5.0          # seconds after start; None: runs until killed
    exit_code: int = 0
    records: list = field(default_factory=lambda: [(1.0, "ok")])  # (seconds after start, outcome)
    gateway_exit: int = 0
    gateway_dies_at: float | None = None
    gateway_listens: bool = True
    workspace: dict = field(default_factory=lambda: {"pager.py": FIXED, "tests/test_pager.py": None})
    output: bytes = b"harness: done\n"
    stops_before_kill: bool = False      # exits by itself just as the toolkit kills it
    log_copy_s: float = 0.0              # how long reading the gateway log takes


class FakeClock:
    def __init__(self):
        self.t = 5000.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        assert seconds >= 0
        self.t += max(seconds, 0.001)

    def now_ms(self):
        return int(round(self.t * 1000))


class FakeDocker:
    def __init__(self, clock: FakeClock, scripts: dict):
        self.clock, self.scripts = clock, scripts
        self.calls, self.removed, self.copied_in = [], [], {}
        self.started = {}      # harness name -> start time
        self.self_stopped = {}  # harness name -> time it stopped by itself (stops_before_kill)
        self.killed = {}       # harness name -> kill time
        self.stopped = set()   # gateway names
        self.gateway_started = {}

    # which attempt a container belongs to
    def _script(self, name):
        for (rep, attempt), script in self.scripts.items():
            names = profile.rep_names(TASK_ID, rep, attempt)
            if name in names.values():
                return script, names
        raise AssertionError(f"no script for {name}")

    def _harness_end(self, name):
        script, _ = self._script(name)
        start = self.started.get(name)
        if start is None:
            return None
        ends = [t for t in (self.killed.get(name), self.self_stopped.get(name),
                            start + script.exit_at if script.exit_at is not None else None) if t is not None]
        return min(ends) if ends else None

    def _running(self, name):
        script, names = self._script(name)
        now = self.clock.t
        if name == names["harness"]:
            end = self._harness_end(name)
            return name in self.started and (end is None or now < end)
        if name == names["gateway"]:
            if not script.gateway_listens or name in self.stopped:
                return False
            start = self.gateway_started[name]
            return script.gateway_dies_at is None or now < start + script.gateway_dies_at
        return False

    def _exit_code(self, name):
        script, names = self._script(name)
        if name == names["harness"]:
            end = self._harness_end(name)
            if name in self.killed and end == self.killed[name]:
                return 137
            return script.exit_code
        if name == names["gateway"]:
            return script.gateway_exit if script.gateway_listens and script.gateway_dies_at is None else 1
        return 0

    # ----------------------------------------------------- Docker interface

    def remove(self, *, containers=(), networks=(), volumes=()):
        self.removed.append((tuple(containers), tuple(networks), tuple(volumes)))
        return []

    def check(self, args, *, stdin=None, timeout=None):
        self.calls.append(list(args))
        if args[:3] == ["docker", "run", "--detach"]:
            self.gateway_started[args[args.index("--name") + 1]] = self.clock.t
        elif args[:2] == ["docker", "start"]:
            self.started[args[2]] = self.clock.t
        return b""

    def copy_in(self, container, path, tar_path):
        self.calls.append(["copy_in", container, path])
        with tarfile.open(tar_path) as tar:
            self.copied_in[(container, path)] = sorted(m.name for m in tar)

    @contextlib.contextmanager
    def copy_out(self, container, path, *, timeout=None):
        self.calls.append(["copy_out", container, path])
        script, names = self._script(container)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            def add(name, data=None):
                info = tarfile.TarInfo(name)
                if data is None:
                    info.type = tarfile.DIRTYPE
                    tar.addfile(info)
                else:
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            if path == orchestrator.GATEWAY_LOG:
                if names["gateway"] not in self.stopped and script.log_copy_s:
                    self.clock.t += min(script.log_copy_s, timeout)
                    if script.log_copy_s > timeout:
                        raise DockerError("timed out copying the log")
                start = self.started.get(names["harness"])
                visible = [(t, o) for t, o in script.records
                           if names["gateway"] in self.stopped or (start is not None and self.clock.t >= start + t)]
                add("gateway.jsonl", b"".join(
                    json.dumps(record(i, o, int(round((start + t) * 1000)) - 10)).encode() + b"\n"
                    for i, (t, o) in enumerate(visible)))
            elif path == "/workspace":
                add("workspace")
                files = {"pager.py": PRISTINE, "tests/test_pager.py":
                         (EXAMPLE_SUITE / "tasks" / TASK_ID / "repo/tests/test_pager.py").read_text()}
                files.update(script.workspace)
                for rel in sorted(files):
                    if files[rel] is not None:
                        add(f"workspace/{rel}", files[rel].encode())
            else:
                raise AssertionError(path)
        buf.seek(0)
        yield buf

    def states(self, names):
        return {n: {"running": self._running(n), "exit_code": self._exit_code(n), "error": ""} for n in names}

    def kill(self, name):
        self.calls.append(["kill", name])
        if not self._running(name):
            return False
        if self._script(name)[0].stops_before_kill:
            self.self_stopped[name] = self.clock.t
            return False
        self.killed[name] = self.clock.t
        return True

    def stop(self, name, grace_s):
        self.calls.append(["stop", name, grace_s])
        self.stopped.add(name)

    def logs(self, name, max_bytes):
        script, names = self._script(name)
        if name == names["gateway"]:
            return b"hb-gateway listening on 0.0.0.0:8080\n" if script.gateway_listens else b"bad settings\n"
        return script.output

    def image_present(self, ref):
        return True

    def pull(self, ref):
        raise AssertionError("no pulls expected")


def make_suite(tmp_path, **changes) -> tuple[dict, Path]:
    suite_dir = tmp_path / "suite"
    shutil.copytree(EXAMPLE_SUITE, suite_dir)
    suite = json.loads((suite_dir / "suite.json").read_text())
    for dotted, value in changes.items():
        target = suite
        *parents, leaf = dotted.split("__")
        for key in parents:
            target = target[key]
        target[leaf] = value
    (suite_dir / "suite.json").write_text(json.dumps(suite, indent=2))
    return suite, suite_dir


def run(tmp_path, scripts, timing=TIMING, **suite_changes):
    suite, suite_dir = make_suite(tmp_path, **suite_changes)
    clock = FakeClock()
    docker = FakeDocker(clock, scripts)
    job = TaskJob(suite=suite, suite_dir=suite_dir, task_id=TASK_ID, harness=HARNESS, toolkit_image=TOOLKIT, run=RUN)
    bundle = run_task(job, docker=docker, clock=clock, out_dir=tmp_path / "out", work_dir=tmp_path / "work",
                      timing=timing, probe_runner=lambda rep, scratch: run_probe_unsandboxed_for_tests)
    out = tmp_path / "extracted"
    extract_bundle(bundle, out)
    return docker, out


def rep_file(out, rep, name):
    path = out / "reps" / str(rep) / name
    return json.loads(path.read_text()) if name.endswith(".json") else path.read_bytes()


def test_three_reps_produce_a_valid_bundle(tmp_path):
    scripts = {
        (1, 1): Script(),
        (2, 1): Script(workspace={}, exit_code=3),
        (3, 1): Script(exit_at=None, records=[(1.0, "ok"), (2.0, "ok")]),
    }
    docker, out = run(tmp_path, scripts, budget__wall_seconds=60)
    manifest = json.loads((out / "task-manifest.json").read_text())
    verify_task_bundle(out, manifest)
    validate("task-manifest", manifest)
    assert manifest["run"] == RUN and manifest["reps"] == 3
    for rep in (1, 2, 3):
        validate("lifecycle", rep_file(out, rep, "lifecycle.json"))
        validate("grade", rep_file(out, rep, "grade.json"))
        for line in rep_file(out, rep, "gateway.jsonl").splitlines():
            validate("gateway-record", json.loads(line))

    first, second, third = (rep_file(out, r, "lifecycle.json") for r in (1, 2, 3))
    assert (first["termination"], first["exit_code"], first["attempts"]) == ("exited", 0, 1)
    assert (second["termination"], second["exit_code"]) == ("exited", 3)
    assert (third["termination"], third["exit_code"]) == ("wall_timeout", 137)
    assert third["ended_at_ms"] - third["started_at_ms"] == 60_000
    assert first["harness_image"] == HARNESS["image"]

    assert rep_file(out, 1, "grade.json")["resolved"] is True
    assert rep_file(out, 2, "grade.json")["resolved"] is False
    assert rep_file(out, 2, "workspace.patch") == b""
    assert b"(number - 1) * size" in rep_file(out, 1, "workspace.patch")
    assert rep_file(out, 1, "harness.log") == b"harness: done\n"
    assert rep_file(out, 1, "grade.json")["grader_image"] == TOOLKIT


def test_containers_start_in_order_and_everything_is_removed(tmp_path):
    docker, _ = run(tmp_path, {(1, 1): Script(), (2, 1): Script(), (3, 1): Script()})
    names = profile.rep_names(TASK_ID, 1, 1)
    calls = [c for c in docker.calls if c[0] == "docker" or c[0] in ("copy_in", "stop")]
    order = [calls.index(c) for c in (
        profile.network_create_args(names),
        profile.helper_create_args(names, TOOLKIT),
        ["copy_in", names["helper"], "/workspace"],
        ["copy_in", names["helper"], "/task"],
        profile.gateway_run_args(names, json.loads(json.dumps(docker_cohort(tmp_path))), TOOLKIT, 3),
        profile.gateway_connect_args(names),
        ["docker", "start", names["harness"]],
        ["stop", names["gateway"], TIMING.gateway_stop_grace_s],
    )]
    assert order == sorted(order)
    assert docker.copied_in[(names["helper"], "/task")] == ["instruction.md"]
    assert "pager.py" in docker.copied_in[(names["helper"], "/workspace")]
    resources = (tuple([names["harness"], names["gateway"], names["helper"]]), (names["network"],),
                 (names["workspace"], names["task"], names["gateway_log"]))
    assert docker.removed.count(resources) == 2  # before (leftovers) and after the attempt


def docker_cohort(tmp_path):
    from hb.identity import cohort_from_suite
    from hb.manifest import tree_digest
    suite_dir = tmp_path / "suite"
    return cohort_from_suite(json.loads((suite_dir / "suite.json").read_text()), tree_digest(suite_dir))


def test_budget_refusal_ends_the_rep_after_the_grace_period(tmp_path):
    scripts = {
        (1, 1): Script(exit_at=None, records=[(1.0, "ok"), (3.0, "budget_exhausted")]),
        (2, 1): Script(exit_at=20.0, records=[(1.0, "ok"), (3.0, "budget_exhausted")]),
    }
    _, out = run(tmp_path, scripts, reps=2)
    first, second = rep_file(out, 1, "lifecycle.json"), rep_file(out, 2, "lifecycle.json")
    # The refusal is answered at 3 s and read at 4 s; the grace runs from the answer.
    assert (first["termination"], first["exit_code"]) == ("budget_exhausted", 137)
    assert first["ended_at_ms"] - first["started_at_ms"] == 33_000
    assert second["termination"] == "exited"  # it exited by itself within the grace period


def test_harness_that_stops_as_it_is_killed_counts_as_exited(tmp_path):
    _, out = run(tmp_path, {(1, 1): Script(exit_at=None, stops_before_kill=True)}, reps=1,
                 budget__wall_seconds=60)
    lifecycle = rep_file(out, 1, "lifecycle.json")
    assert (lifecycle["termination"], lifecycle["exit_code"]) == ("exited", 0)


@pytest.mark.parametrize("log_copy_s", [7.0, 0.7])  # 0.7: the last read starts with under a second left
def test_slow_log_reads_do_not_delay_the_wall_clock_kill(tmp_path, log_copy_s):
    _, out = run(tmp_path, {(1, 1): Script(exit_at=None, log_copy_s=log_copy_s)}, reps=1, budget__wall_seconds=60)
    lifecycle = rep_file(out, 1, "lifecycle.json")
    assert lifecycle["termination"] == "wall_timeout"
    assert 60_000 <= lifecycle["ended_at_ms"] - lifecycle["started_at_ms"] <= 60_010  # the last read was cut short


def test_void_attempt_is_run_again_in_fresh_containers(tmp_path):
    scripts = {
        (1, 1): Script(exit_at=None, records=[(1.0, "ok"), (2.0, "upstream_failed")]),
        (1, 2): Script(),
    }
    docker, out = run(tmp_path, scripts, reps=1)
    lifecycle = rep_file(out, 1, "lifecycle.json")
    assert lifecycle["attempts"] == 2 and lifecycle["termination"] == "exited"
    assert b"upstream_failed" not in rep_file(out, 1, "gateway.jsonl")
    first = profile.rep_names(TASK_ID, 1, 1)
    assert ["kill", first["harness"]] in docker.calls  # ended as soon as the void showed in the log
    assert docker.killed[first["harness"]] - docker.started[first["harness"]] == pytest.approx(2.0)
    assert any(first["workspace"] in volumes for _, _, volumes in docker.removed)


def test_void_seen_only_after_the_gateway_drained(tmp_path):
    # The harness exited at 5 s; the void record is written during the drain.
    scripts = {(1, 1): Script(records=[(1.0, "ok"), (9.0, "upstream_failed")]), (1, 2): Script()}
    _, out = run(tmp_path, scripts, reps=1)
    assert rep_file(out, 1, "lifecycle.json")["attempts"] == 2


def test_scratch_copies_are_removed_after_each_attempt_and_grade(tmp_path):
    # They scale with the workspace; kept for every rep, they would add up on the runner's disk.
    scripts = {(1, 1): Script(records=[(1.0, "upstream_failed")]), (1, 2): Script(), (2, 1): Script()}
    run(tmp_path, scripts, reps=2)
    assert sorted(p.name for p in (tmp_path / "work").iterdir()) == ["bundle", "pristine"]


def test_reruns_are_capped(tmp_path):
    void = [(1.0, "upstream_failed")]
    scripts = {(1, a): Script(records=void) for a in (1, 2)}
    with pytest.raises(RerunsExhausted):
        run(tmp_path, scripts, reps=1, infra__max_reruns_per_rep=1)


def test_gateway_failure_fails_the_job_instead_of_voiding(tmp_path):
    # Exit status 1 means records could not be written, for example because the
    # disk was full. The harness can cause that, so it must not become a re-roll.
    scripts = {(1, 1): Script(records=[(1.0, "upstream_failed")], gateway_exit=1), (1, 2): Script()}
    with pytest.raises(GatewayFailed):
        run(tmp_path, scripts, reps=1)


def test_gateway_that_stops_mid_attempt_fails_the_job(tmp_path):
    with pytest.raises(GatewayFailed):
        run(tmp_path, {(1, 1): Script(exit_at=None, gateway_dies_at=3.0)}, reps=1)


def test_gateway_that_never_listens_fails_the_job(tmp_path):
    with pytest.raises(GatewayFailed, match="before listening"):
        run(tmp_path, {(1, 1): Script(gateway_listens=False)}, reps=1)


def test_snapshot_drops_git_and_ignored_paths(tmp_path):
    workspace = {"pager.py": FIXED, ".git/config": "[core]\n", "pkg/.git/HEAD": "ref\n",
                 "__pycache__/pager.cpython-313.pyc": "x", ".pytest_cache/v/cache": "{}",
                 "NOTES.md": "new file\n"}
    _, out = run(tmp_path, {(1, 1): Script(workspace=workspace)}, reps=1)
    patch = rep_file(out, 1, "workspace.patch").decode()
    assert "pager.py" in patch and "NOTES.md" in patch
    for text in (".git", "__pycache__", ".pytest_cache"):
        assert text not in patch


def test_workspace_over_the_limit_fails_the_job_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "MAX_WORKSPACE_BYTES", 100)
    suite, suite_dir = make_suite(tmp_path, reps=1)
    clock = FakeClock()
    docker = FakeDocker(clock, {(1, 1): Script()})
    job = TaskJob(suite=suite, suite_dir=suite_dir, task_id=TASK_ID, harness=HARNESS, toolkit_image=TOOLKIT, run=RUN)
    with pytest.raises(LimitExceeded):
        run_task(job, docker=docker, clock=clock, out_dir=tmp_path / "out", work_dir=tmp_path / "work",
                 timing=TIMING, probe_runner=lambda rep, scratch: run_probe_unsandboxed_for_tests)
    assert len(docker.removed) == 2  # the attempt's resources, before and after
    assert not (tmp_path / "work" / "r1a1").exists()
    assert not (tmp_path / "out").exists()


def test_unknown_task_is_refused(tmp_path):
    suite, suite_dir = make_suite(tmp_path)
    job = TaskJob(suite=suite, suite_dir=suite_dir, task_id="no-such-task", harness=HARNESS,
                  toolkit_image=TOOLKIT, run=RUN)
    with pytest.raises(RunTaskError):
        run_task(job, docker=FakeDocker(FakeClock(), {}), clock=FakeClock(), out_dir=tmp_path / "o",
                 work_dir=tmp_path / "w")


def test_record_tail_reads_complete_lines_once():
    tail = RecordTail()
    ok = json.dumps(record(0, "ok")).encode()
    refused = json.dumps(record(1, "budget_exhausted", started_ms=5000)).encode()
    later = json.dumps(record(2, "budget_exhausted", started_ms=9000)).encode()
    tail.feed(ok + b"\n" + refused[:20])
    assert tail.first_refusal_ms is None and tail.offset == len(ok) + 1
    tail.feed(ok + b"\n" + refused + b"\n" + later + b"\n")
    assert tail.first_refusal_ms == 5010 and not tail.void  # the first refusal's answer
    tail.feed(b"")  # a failed or shorter read changes nothing
    assert tail.first_refusal_ms == 5010
    odd = RecordTail()
    odd.feed(json.dumps({**record(0, "budget_exhausted"), "started_at_ms": "soon"}).encode() + b"\n")
    assert odd.first_refusal_ms is None  # skipped; the final parse of the log decides


def test_parse_records_refuses_anything_malformed():
    good = json.dumps(record(0, "ok")).encode() + b"\n"
    assert len(parse_records(good + good)) == 2
    assert parse_records(b"") == []
    for bad in (good[:-1], b"not json\n", b"[]\n", b'{"outcome": "other"}\n'):
        with pytest.raises(RunTaskError):
            parse_records(bad)


class ProbeDocker:
    """Fake for DockerProbeRunner: scripted `docker start --attach` results."""

    def __init__(self, result, error=""):
        self.result, self.error, self.calls, self.copied = result, error, [], []

    def remove(self, **kw):
        self.calls.append(["remove", kw])
        return []

    def check(self, args, **kw):
        self.calls.append(args)
        return b""

    def copy_in(self, container, path, tar_path):
        with tarfile.open(tar_path) as tar:
            self.copied = sorted((m.name, m.uid, m.mode) for m in tar)

    def capture(self, args, *, timeout, max_stdout):
        self.calls.append(args)
        return self.result

    def kill(self, name):
        self.calls.append(["kill", name])
        return True

    def states(self, names):
        return {n: {"running": False, "exit_code": 0, "error": self.error} for n in names}


def probe_run(tmp_path, docker):
    task_dir = EXAMPLE_SUITE / "tasks" / TASK_ID
    names = profile.probe_names(TASK_ID, 1)
    runner = DockerProbeRunner(docker, TOOLKIT, names, tmp_path / "scratch")
    return runner(task_dir / "probes/probe.py", task_dir / "repo", task_dir / "probes/inputs.json", 30)


def test_probe_runner_copies_tree_probe_and_inputs_and_returns_stdout(tmp_path):
    docker = ProbeDocker((0, b'{"case": {"ok": 1}}'))
    assert probe_run(tmp_path, docker) == b'{"case": {"ok": 1}}'
    names = [n for n, _, _ in docker.copied]
    assert names == ["inputs", "inputs/inputs.json", "program", "program/probe.py", "tree", "tree/pager.py",
                     "tree/tests", "tree/tests/test_pager.py"]
    assert {uid for _, uid, _ in docker.copied} == {0}
    start = ["docker", "start", "--attach", profile.probe_names(TASK_ID, 1)["sandbox"]]
    assert start in docker.calls
    assert docker.calls[-1][0] == "remove"


def test_probe_runner_returns_nothing_on_timeout_and_kills_the_sandbox(tmp_path):
    docker = ProbeDocker((None, b'{"partial'))
    assert probe_run(tmp_path, docker) == b""
    assert ["kill", profile.probe_names(TASK_ID, 1)["sandbox"]] in docker.calls


def test_probe_runner_raises_when_the_sandbox_cannot_start(tmp_path):
    with pytest.raises(DockerError):
        probe_run(tmp_path, ProbeDocker((127, b""), error="exec: python3: not found"))
