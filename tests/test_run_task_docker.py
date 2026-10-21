"""run-task end to end against a real Docker daemon.

Opt-in: set HB_DOCKER_TESTS=1. HB_TEST_BASE_IMAGE names the base for the
toolkit image (python3 3.12+; default python:3.12-slim) and HB_TEST_HARNESS_BASE
the base for the stub harness (default: the same). The baseline harness
(harnesses/baseline) is built on its own pinned base, or on HB_TEST_BASELINE_BASE.

What runs: the toolkit image built from docker/toolkit.Dockerfile (gateway,
helper and probe sandbox), a stub harness image (tests/docker/stub_harness.py)
and a stub provider on the host, reached over TLS through the Docker bridge.
The test layer on top of the toolkit image only adds the stub's certificate as
the trusted CA. Most tests run the orchestrator in this process with the host's
docker CLI; test_run_task_inside_the_toolkit_container runs it the way bench.yml
does, in the toolkit image with the host's Docker socket (that needs a base with
git and the docker CLI, such as docker/toolkit-base.Dockerfile builds).

These tests check what the toolkit does (bundles, terminations, re-runs, probe
results). They do not probe the isolation of any container; that is verified by
asserting on the profile (tests/test_profile.py).
"""

import http.server
import json
import os
import queue
import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from hb import profile
from hb.dockercli import Clock, Docker
from hb.manifest import extract_bundle, pack_bundle, tree_digest, verify_task_bundle
from hb.orchestrator import DockerProbeRunner, GatewayFailed, TaskJob, Timing, run_task
from hb.schemas import validate

pytestmark = pytest.mark.skipif(os.environ.get("HB_DOCKER_TESTS") != "1",
                                reason="Docker tests are opt-in: set HB_DOCKER_TESTS=1")

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_SUITE = ROOT / "examples" / "suite"
TASK_ID = "fix-pagination-off-by-one"
PRISTINE = (EXAMPLE_SUITE / "tasks" / TASK_ID / "repo" / "pager.py").read_text()
FIXED = PRISTINE.replace("start = number * size", "start = (number - 1) * size")
WRONG = PRISTINE.replace("start = number * size", "start = (number - 1) * (size - 1)")
RUN = {"repository": "example-user/hb-runs", "run_id": 18000000001, "run_attempt": 1}
PLACEHOLDER_IMAGE = "ghcr.io/sigormatt/hb-test@sha256:" + "0" * 64
TIMING = Timing(poll_s=0.5, budget_grace_s=2.0)


def sh(*args, **kw) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout.strip()


def build(context: Path, dockerfile: Path, tag: str, **build_args) -> str:
    argv = ["docker", "build", "--quiet", "--file", str(dockerfile), "--tag", tag]
    for key, value in build_args.items():
        argv += ["--build-arg", f"{key}={value}"]
    sh(*argv, str(context))
    return sh("docker", "image", "inspect", "--format", "{{.Id}}", tag)


def bridge_gateway_ip() -> str:
    ip = sh("docker", "network", "inspect", "bridge", "--format", "{{range .IPAM.Config}}{{.Gateway}}{{end}}")
    return ip or "172.17.0.1"


class StubProvider:
    """OpenAI-compatible stub on the host, over TLS. Answers from a queue of scripted responses."""

    def __init__(self, host_ip: str, cert: Path, key: str):
        self.responses = queue.Queue()
        self.requests, self.keys = [], []
        provider = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                provider.requests.append(json.loads(self.rfile.read(length)))
                provider.keys.append(self.headers.get("Authorization"))
                status, body = provider.responses.get(timeout=5)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.base_url = f"https://{host_ip}:{self.server.server_address[1]}/v1/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, directive: dict) -> None:
        self.responses.put((200, {
            "id": "stub", "object": "chat.completion", "created": 0, "model": "stub",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(directive)}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
        }))

    def reply(self, message: dict) -> None:
        """Answer with this assistant message as it is (tool calls, for a real harness)."""
        self.responses.put((200, {
            "id": "stub", "object": "chat.completion", "created": 0, "model": "stub",
            "choices": [{"index": 0, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                         "message": message}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
        }))

    def fail(self, status: int) -> None:
        self.responses.put((status, {"error": {"type": "invalid_api_key", "message": "stub"}}))

    def assert_drained(self):
        assert self.responses.empty(), "the stub provider has unused responses"


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    if shutil.which("docker") is None or shutil.which("openssl") is None:
        pytest.skip("needs the docker and openssl commands")
    base = os.environ.get("HB_TEST_BASE_IMAGE", "python:3.12-slim")
    harness_base = os.environ.get("HB_TEST_HARNESS_BASE", base)
    work = tmp_path_factory.mktemp("docker-env")
    host_ip = bridge_gateway_ip()
    cert, key = work / "cert.pem", work / "key.pem"
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=hb-stub",
       "-addext", f"subjectAltName=IP:{host_ip}", "-keyout", str(key), "-out", str(cert))

    build(ROOT, ROOT / "docker" / "toolkit.Dockerfile", "hb-test/toolkit:latest",
          BASE_IMAGE=base, REGISTRY_FILE="examples/registry.json")
    tls = work / "toolkit-tls"
    tls.mkdir()
    shutil.copy(cert, tls / "ca.pem")
    (tls / "Dockerfile").write_text("FROM hb-test/toolkit:latest\nCOPY ca.pem /opt/hb-test/ca.pem\n"
                                    "ENV SSL_CERT_FILE=/opt/hb-test/ca.pem\n")
    toolkit = build(tls, tls / "Dockerfile", "hb-test/toolkit-tls:latest")

    stub = work / "stub-harness"
    stub.mkdir()
    shutil.copy(ROOT / "tests" / "docker" / "stub_harness.py", stub / "stub_harness.py")
    (stub / "Dockerfile").write_text(f"FROM {harness_base}\nCOPY stub_harness.py /hb/stub_harness.py\n")
    harness = build(stub, stub / "Dockerfile", "hb-test/stub-harness:latest")

    provider = StubProvider(host_ip, cert, str(key))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MODEL_API_KEY", "stub-key")  # handed to the gateway container by name
        yield {"toolkit": toolkit, "harness": harness, "provider": provider}
    provider.server.shutdown()


def make_job(tmp_path, env, harness=None, **changes) -> TaskJob:
    suite_dir = tmp_path / "suite"
    shutil.copytree(EXAMPLE_SUITE, suite_dir)
    suite = json.loads((suite_dir / "suite.json").read_text())
    suite["model"]["base_url"] = env["provider"].base_url
    suite["infra"].update(upstream_retries=0, max_reruns_per_rep=1)
    for dotted, value in changes.items():
        target = suite
        *parents, leaf = dotted.split("__")
        for part in parents:
            target = target[part]
        target[leaf] = value
    (suite_dir / "suite.json").write_text(json.dumps(suite, indent=2))
    harness = harness or {"id": "stub", "image": env["harness"], "entrypoint": ["python3", "/hb/stub_harness.py"]}
    return TaskJob(suite=suite, suite_dir=suite_dir, task_id=TASK_ID, harness=harness,
                   toolkit_image=env["toolkit"], run=RUN)


def run(tmp_path, env, harness=None, **changes) -> Path:
    job = make_job(tmp_path, env, harness, **changes)
    bundle = run_task(job, docker=Docker(), clock=Clock(), out_dir=tmp_path / "out",
                      work_dir=tmp_path / "work", timing=TIMING)
    env["provider"].assert_drained()
    return checked_bundle(bundle, tmp_path / "bundle")


def checked_bundle(bundle: Path, out: Path) -> Path:
    """Extract a task bundle, verify it against its manifest and validate every record."""
    extract_bundle(bundle, out)
    manifest = json.loads((out / "task-manifest.json").read_text())
    verify_task_bundle(out, manifest)
    validate("task-manifest", manifest)
    for rep_dir in sorted((out / "reps").iterdir()):
        # Locally built images have no registry digest, so they are referenced by
        # image id; put a placeholder in before checking the schema.
        lifecycle = json.loads((rep_dir / "lifecycle.json").read_text())
        validate("lifecycle", {**lifecycle, "harness_image": PLACEHOLDER_IMAGE})
        grade = json.loads((rep_dir / "grade.json").read_text())
        validate("grade", {**grade, "grader_image": PLACEHOLDER_IMAGE})
        for line in (rep_dir / "gateway.jsonl").read_text().splitlines():
            validate("gateway-record", json.loads(line))
    return out


def rep(out: Path, n: int, name: str):
    path = out / "reps" / str(n) / name
    return json.loads(path.read_text()) if name.endswith(".json") else path.read_text()


def leftovers(job_task: str = TASK_ID) -> list[str]:
    containers = sh("docker", "ps", "--all", "--format", "{{.Names}}").split()
    volumes = sh("docker", "volume", "ls", "--format", "{{.Name}}").split()
    networks = sh("docker", "network", "ls", "--format", "{{.Name}}").split()
    return [n for n in containers + volumes + networks if n.startswith(f"hb-{job_task}-")]


def test_two_reps_end_to_end(tmp_path, env):
    provider = env["provider"]
    provider.answer({"write": {"pager.py": FIXED}})
    provider.answer({
        "write": {"pager.py": WRONG, "tests/test_pager.py": "def test_nothing():\n    pass\n",
                  "NOTES.md": "notes\n", "__pycache__/pager.cpython-312.pyc": "cache",
                  ".git/config": "[core]\n"},
        "symlink": {"passwd": "/etc/passwd"},
    })
    out = run(tmp_path, env, reps=2)

    for n in (1, 2):
        lifecycle = rep(out, n, "lifecycle.json")
        assert (lifecycle["termination"], lifecycle["exit_code"], lifecycle["attempts"]) == ("exited", 0, 1)
        assert "stub-harness: applied" in rep(out, n, "harness.log")
        [record] = [json.loads(line) for line in rep(out, n, "gateway.jsonl").splitlines()]
        assert record["outcome"] == "ok" and record["usage"]["input_tokens"] == 50
    assert rep(out, 1, "grade.json")["resolved"] is True

    second = rep(out, 2, "grade.json")
    assert second["resolved"] is False
    assert second["normalization"]["protected_restored"] == 1
    patch = rep(out, 2, "workspace.patch")
    assert "NOTES.md" in patch and "tests/test_pager.py" in patch and "pager.py" in patch
    for text in (".git", "__pycache__", "passwd"):
        assert text not in patch

    # The instruction reached the harness through the read-only task volume.
    instruction = (EXAMPLE_SUITE / "tasks" / TASK_ID / "instruction.md").read_text()
    assert provider.requests[-1]["messages"][0]["content"] == instruction
    assert provider.requests[-1]["model"] == "Qwen/Qwen3-30B-A3B-Instruct-2507"
    assert provider.keys[-1] == "Bearer stub-key"  # the gateway's key, passed to its container by name
    assert leftovers() == []


def test_void_attempt_runs_again(tmp_path, env):
    env["provider"].fail(401)  # a setup failure on the provider side voids the attempt
    env["provider"].answer({"write": {"pager.py": FIXED}})
    # A run of one task and one rep may have no re-run (spec §5.1), so the job
    # would stop at the void: at 200 permille, four more tasks allow the run one.
    example = json.loads((EXAMPLE_SUITE / "suite.json").read_text())["tasks"]
    tasks = example + [{"id": f"zz-filler-{n}", "category": example[0]["category"]} for n in range(1, 5)]
    out = run(tmp_path, env, reps=1, tasks=tasks, infra__max_rerun_permille=200)
    lifecycle = rep(out, 1, "lifecycle.json")
    assert lifecycle["attempts"] == 2 and lifecycle["termination"] == "exited"
    assert "upstream_failed" not in rep(out, 1, "gateway.jsonl")
    assert rep(out, 1, "grade.json")["resolved"] is True
    assert leftovers() == []


def test_wall_clock_and_budget_end_reps(tmp_path, env):
    env["provider"].answer({"write": {"pager.py": FIXED}, "then": "sleep"})
    env["provider"].answer({"write": {"pager.py": FIXED}, "then": "again"})  # its second request is refused
    out = run(tmp_path, env, reps=2, budget__model_calls=1, budget__wall_seconds=15)

    first = rep(out, 1, "lifecycle.json")
    assert (first["termination"], first["exit_code"]) == ("wall_timeout", 137)
    assert 15_000 <= first["ended_at_ms"] - first["started_at_ms"] < 20_000
    assert rep(out, 1, "grade.json")["resolved"] is True  # the work done before the limit counts

    second = rep(out, 2, "lifecycle.json")
    assert (second["termination"], second["exit_code"]) == ("budget_exhausted", 137)
    assert second["ended_at_ms"] - second["started_at_ms"] < 10_000
    outcomes = [json.loads(line)["outcome"] for line in rep(out, 2, "gateway.jsonl").splitlines()]
    assert outcomes == ["ok", "budget_exhausted"]
    assert leftovers() == []


def local_digest_ref(repository: str, image_id: str) -> str | None:
    """repository@digest for a locally built image, if this daemon resolves it.

    With the containerd image store a local image's id is its manifest digest
    and resolves by digest; the classic store needs a registry for that.
    """
    ref = f"{repository}@{image_id}"
    probe = subprocess.run(["docker", "image", "inspect", ref], capture_output=True, check=False)
    return ref if probe.returncode == 0 else None


DRIVER = (
    "import json, os; from pathlib import Path; "
    "from hb.dockercli import Clock, Docker; from hb.orchestrator import TaskJob, Timing, run_task; "
    "spec = json.loads(os.environ['HB_TEST_JOB']); "
    "job = TaskJob(suite=json.loads(Path('/suite/suite.json').read_text()), suite_dir=Path('/suite'), "
    "task_id=spec['task_id'], harness=spec['harness'], toolkit_image=spec['toolkit_image'], run=spec['run']); "
    "run_task(job, docker=Docker(), clock=Clock(), out_dir=Path('/out'), work_dir=Path('/tmp/work'), "
    "timing=Timing(poll_s=0.5, budget_grace_s=2.0))"
)


def test_run_task_inside_the_toolkit_container(tmp_path, env):
    """The deployment path of bench.yml: the toolkit in its own image, with the host's
    Docker socket, starting the gateway, helper and probe containers as siblings."""
    tools = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python3", env["toolkit"], "-c",
         "import shutil, sys; sys.exit(0 if shutil.which('git') and shutil.which('docker') else 1)"],
        capture_output=True, check=False)
    if tools.returncode != 0:
        pytest.skip("the toolkit base image has no git or docker CLI (build docker/toolkit-base.Dockerfile)")
    job = make_job(tmp_path, env, reps=1)
    env["provider"].answer({"write": {"pager.py": FIXED}})
    out = tmp_path / "out"
    out.mkdir()
    mounts = ["--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
              "--mount", f"type=bind,src={job.suite_dir},dst=/suite,readonly",
              "--mount", f"type=bind,src={out},dst=/out"]
    toolkit_ref = local_digest_ref("hb-test/toolkit-tls", env["toolkit"])
    harness_ref = local_digest_ref("hb-test/stub-harness", env["harness"])
    if toolkit_ref and harness_ref:
        # Exactly as bench.yml runs it: the CLI, its inputs from the environment.
        registry = tmp_path / "registry.json"
        registry.write_text(json.dumps({"schema": "hb/registry/1", "harnesses": [{
            **job.harness, "image": harness_ref, "version": "0", "config_digest": "sha256:" + "0" * 64,
            "homepage": "https://example.com/stub"}]}))
        argv = ["docker", "run", "--rm", "--init", *mounts,
                "--mount", f"type=bind,src={registry},dst=/registry.json,readonly",
                "--env", "HARNESS_ID=stub", "--env", f"TASK_ID={TASK_ID}", "--env", "MODEL_API_KEY",
                "--env", f"HB_TOOLKIT_IMAGE={toolkit_ref}", "--env", f"HB_REPOSITORY={RUN['repository']}",
                "--env", f"HB_RUN_ID={RUN['run_id']}", "--env", "HB_RUN_ATTEMPT=1",
                toolkit_ref, "run-task", "--suite", "/suite", "--out", "/out", "--registry", "/registry.json"]
    else:
        # This daemon cannot address local images by digest, which the CLI requires:
        # run the same orchestrator call through a driver instead (the CLI's input
        # checks are covered by tests/test_cli.py).
        spec = {"task_id": TASK_ID, "harness": job.harness, "toolkit_image": env["toolkit"], "run": RUN}
        argv = ["docker", "run", "--rm", "--init", *mounts, "--env", "MODEL_API_KEY",
                "--env", f"HB_TEST_JOB={json.dumps(spec)}", "--entrypoint", "python3", env["toolkit"],
                "-c", DRIVER]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False)
    assert proc.returncode == 0, proc.stderr[-3000:]
    env["provider"].assert_drained()
    bundle = checked_bundle(out / f"task-bundle-{TASK_ID}.tar", tmp_path / "bundle")
    assert rep(bundle, 1, "lifecycle.json")["termination"] == "exited"
    assert rep(bundle, 1, "grade.json")["resolved"] is True
    if toolkit_ref:
        assert rep(bundle, 1, "grade.json")["grader_image"] == toolkit_ref
    assert leftovers() == []


def test_unreachable_provider_fails_the_job(tmp_path, env):
    # Nothing listens on this port: the gateway cannot connect, so the retries run
    # out before anything is sent. It exits 3, and the job fails instead of re-running.
    with socket.socket() as placeholder:
        placeholder.bind(("0.0.0.0", 0))
        port = placeholder.getsockname()[1]
    host = env["provider"].base_url.split("//", 1)[1].split(":", 1)[0]
    job = make_job(tmp_path, env, reps=1, model__base_url=f"https://{host}:{port}/v1/")
    with pytest.raises(GatewayFailed, match="status 3: the provider could not be reached"):
        run_task(job, docker=Docker(), clock=Clock(), out_dir=tmp_path / "out", work_dir=tmp_path / "work",
                 timing=TIMING)
    assert not (tmp_path / "out").exists()
    assert leftovers() == []


def test_plan_and_seal_inside_the_toolkit_container(tmp_path, env):
    """bench.yml's plan and seal steps: the toolkit image, no network, read-only inputs.

    Sealing the example run's bundle must give exactly the committed example run manifest."""
    def toolkit(*args, mounts, variables):
        argv = ["docker", "run", "--rm", "--network", "none"]
        for source, target, mode in mounts:
            argv += ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if mode == "ro" else "")]
        for name, value in variables.items():
            argv += ["--env", f"{name}={value}"]
        return subprocess.run([*argv, env["toolkit"], *args], capture_output=True, text=True, timeout=300,
                              check=False)

    plan_out, bundles, seal_out = tmp_path / "plan", tmp_path / "bundles", tmp_path / "seal"
    for directory in (plan_out, bundles, seal_out):
        directory.mkdir()
    proc = toolkit("plan", "--suite", "/suite", "--out", "/out",
                   mounts=[(EXAMPLE_SUITE, "/suite", "ro"), (plan_out, "/out", "rw")],
                   variables={"HARNESS_ID": "baseline", "HB_RUN_ATTEMPT": "1"})  # in the test image's registry
    assert proc.returncode == 0, proc.stderr
    assert json.loads((plan_out / "tasks.json").read_text()) == [TASK_ID]
    assert (plan_out / "timeout_minutes").read_text() == "226\n"

    expected = ROOT / "examples" / "run" / "run-manifest.json"
    run_manifest = json.loads(expected.read_text())
    (bundles / f"task-bundle-{TASK_ID}").mkdir()  # one directory per artifact, as the seal job downloads them
    pack_bundle(ROOT / "examples" / "run" / "task-bundles" / TASK_ID,
                bundles / f"task-bundle-{TASK_ID}" / f"task-bundle-{TASK_ID}.tar")
    proc = toolkit("seal", "--suite", "/suite", "--bundles", "/bundles", "--out", "/out",
                   mounts=[(EXAMPLE_SUITE, "/suite", "ro"), (bundles, "/bundles", "ro"), (seal_out, "/out", "rw")],
                   variables={"HARNESS_ID": "baseline", "HB_REPOSITORY": run_manifest["run"]["repository"],
                              "HB_RUN_ID": str(run_manifest["run"]["run_id"]), "HB_RUN_ATTEMPT": "1",
                              "HB_TOOLKIT_IMAGE": run_manifest["release"]["toolkit_image"],
                              "HB_SUITE_IMAGE": run_manifest["release"]["suite_image"]})
    assert proc.returncode == 0, proc.stderr
    assert (seal_out / "run-manifest.json").read_text() == expected.read_text()


def test_release_plan_check_runs_as_release_yml_runs_it(tmp_path, env):
    """release.yml's plan check, its own shell lines: sandboxed, writing to a directory the runner owns."""
    lines = (ROOT / ".github" / "workflows" / "release.yml").read_text().splitlines()
    sandbox = next(line.strip() for line in lines if line.strip().startswith("sandbox=("))
    start = next(i for i, line in enumerate(lines) if line.strip() == 'mkdir -p "$RUNNER_TEMP/plan"')
    stop = next(i for i in range(start, len(lines)) if lines[i].strip().endswith("plan --suite /suite --out /out"))
    script = "\n".join([sandbox, *(line.strip() for line in lines[start:stop + 1])])
    shutil.copytree(EXAMPLE_SUITE, tmp_path / "suite")
    proc = subprocess.run(["bash", "-euo", "pipefail", "-c", script], capture_output=True, text=True, timeout=300,
                          check=False, env={**os.environ, "RUNNER_TEMP": str(tmp_path), "TOOLKIT": "hb-test/toolkit",
                                            "VERSION": "latest", "HARNESS_ID": "baseline", "HB_RUN_ATTEMPT": "1"})
    assert proc.returncode == 0, proc.stderr
    assert json.loads((tmp_path / "plan" / "tasks.json").read_text()) == [TASK_ID]
    assert (tmp_path / "plan" / "timeout_minutes").read_text() == "226\n"


PROBES = {
    "report.py": "import json, sys\nprint('noise', file=sys.stderr)\njson.dump({'files': sorted(__import__('os')"
                 ".listdir(sys.argv[1]))}, sys.stdout)\n",
    "hang.py": "import json, sys, time\nsys.stdout.write('{}')\nsys.stdout.flush()\ntime.sleep(60)\n",
    "crash.py": "import sys\nsys.stdout.write('{\"partial\": true}')\nsys.stdout.flush()\nraise SystemExit(7)\n",
    "flood.py": "import sys\nsys.stdout.write('x' * (3 << 20))\n",
}


def test_probe_sandbox_follows_the_runner_contract(tmp_path, env):
    tree = tmp_path / "tree"
    (tree / "pkg").mkdir(parents=True)
    (tree / "pkg" / "m.py").write_text("x = 1\n")
    probes = tmp_path / "probes"
    probes.mkdir()
    for name, text in PROBES.items():
        (probes / name).write_text(text)
    (probes / "inputs.json").write_text("{}")
    runner = DockerProbeRunner(Docker(), env["toolkit"], profile.probe_names("probe-test", 1), tmp_path / "s")

    def probe(name, timeout=20):
        return runner(probes / name, tree, probes / "inputs.json", timeout)

    assert json.loads(probe("report.py")) == {"files": ["pkg"]}
    assert probe("hang.py", timeout=2) == b""       # over time: nothing, so every check fails
    assert probe("crash.py") == b'{"partial": true}'  # a crash still returns what was written
    assert probe("flood.py") == b""                 # more than a report can be
    assert leftovers("probe-test") == []


def suite_image_tree(suite: Path, out: Path) -> Path:
    """Build docker/suite.Dockerfile from suite and read /suite back the way bench.yml does."""
    tag = "hb-test/suite:latest"
    build(suite, ROOT / "docker" / "suite.Dockerfile", tag)
    try:
        cid = sh("docker", "create", tag, "/bin/true")  # never started: the image has no /bin/true
        try:
            sh("docker", "cp", f"{cid}:/suite", str(out))
        finally:
            sh("docker", "rm", cid)
    finally:
        sh("docker", "image", "rm", tag)
    return out


def test_suite_image_holds_exactly_the_suite(tmp_path):
    """Dotfiles and a .dockerignore below the suite's root are ordinary files to docker build, with or
    without BuildKit (an ignore file at the root is refused by scripts/release.py inputs)."""
    if shutil.which("docker") is None:
        pytest.skip("needs the docker command")
    suite = tmp_path / "suite"
    shutil.copytree(EXAMPLE_SUITE, suite)
    (suite / ".hidden").write_text("kept\n")
    (suite / "tasks" / TASK_ID / ".dockerignore").write_text("repo\n")
    copied = suite_image_tree(suite, tmp_path / "copied")
    assert tree_digest(copied) == tree_digest(suite)
    assert (copied / "tasks" / TASK_ID / "repo" / "pager.py").is_file()


def buildkit() -> bool:
    if os.environ.get("DOCKER_BUILDKIT") == "0" or shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "buildx", "version"], capture_output=True, check=False).returncode == 0


@pytest.mark.skipif(not buildkit(), reason="BuildKit only: the classic builder does not read the Dockerfile's ignore file")
def test_with_buildkit_the_dockerfiles_ignore_file_takes_precedence(tmp_path):
    # The second guard behind the inputs check: docker/suite.Dockerfile.dockerignore has no patterns.
    suite = tmp_path / "suite"
    shutil.copytree(EXAMPLE_SUITE, suite)
    (suite / ".dockerignore").write_text("tasks\n")
    assert tree_digest(suite_image_tree(suite, tmp_path / "copied")) == tree_digest(suite)


def test_toolkit_build_refuses_a_missing_registry_file(env):
    # With or without BuildKit, an empty REGISTRY_FILE copies the whole build context to
    # /opt/hb/registry.json, as a directory; the build's own check of what it copied is
    # what fails. BuildKit prints the step's output on stderr, the classic builder on stdout.
    base = os.environ.get("HB_TEST_BASE_IMAGE", "python:3.12-slim")
    proc = subprocess.run(["docker", "build", "--force-rm", "--file", str(ROOT / "docker" / "toolkit.Dockerfile"),
                           "--build-arg", f"BASE_IMAGE={base}", "--tag", "hb-test/toolkit-no-registry:latest",
                           str(ROOT)], capture_output=True, text=True, check=False)
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "IsADirectoryError" in output and "/opt/hb/registry.json" in output


@pytest.fixture(scope="session")
def baseline_image(env):
    """harnesses/baseline built as harness.yml builds it: the adapter directory is the context.

    On its pinned base, unless HB_TEST_BASELINE_BASE names another (for a machine that cannot pull it)."""
    base = os.environ.get("HB_TEST_BASELINE_BASE")
    return build(ROOT / "harnesses" / "baseline", ROOT / "harnesses" / "baseline" / "Dockerfile",
                 "hb-test/baseline:latest", **({"PYTHON_IMAGE": base} if base else {}))


def tool_call_message(call_id: str, name: str, arguments: dict) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def test_the_baseline_harness_fixes_the_task_end_to_end(tmp_path, env, baseline_image):
    """The baseline image in a rep: its tool calls go through the gateway and change the workspace."""
    provider = env["provider"]
    provider.reply(tool_call_message("c1", "bash", {"command": "ls"}))
    provider.reply(tool_call_message("c2", "write_file", {"path": "pager.py", "content": FIXED}))
    provider.reply(tool_call_message("c3", "finish", {"summary": "fixed the off-by-one"}))
    entrypoint = json.loads((ROOT / "harnesses" / "baseline" / "harness.json").read_text())["entrypoint"]
    out = run(tmp_path, env, harness={"id": "baseline", "image": baseline_image, "entrypoint": entrypoint},
              reps=1)
    lifecycle = rep(out, 1, "lifecycle.json")
    assert (lifecycle["termination"], lifecycle["exit_code"]) == ("exited", 0)
    assert rep(out, 1, "grade.json")["resolved"] is True
    records = [json.loads(line) for line in rep(out, 1, "gateway.jsonl").splitlines()]
    assert [(r["outcome"], r["tool_calls"]["count"]) for r in records] == [("ok", 1)] * 3
    after_ls = provider.requests[-2]["messages"][-1]
    assert after_ls["role"] == "tool" and after_ls["content"].startswith("exit status 0")
    assert "pager.py" in after_ls["content"]  # the task's repository is the working directory
    assert "finish: fixed the off-by-one" in rep(out, 1, "harness.log")
    assert leftovers() == []
