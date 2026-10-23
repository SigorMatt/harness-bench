"""The task base image: its build files, its pin and its steps (spec §6.5).

The network steps (pushing, pulling, verifying attestations) are not run here:
the verify commands are checked as argument lists, and the online path with
stand-ins for docker and gh. tests/test_run_task_docker.py builds the image.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hb import profile, taskbase
from hb.release import Git, ReleaseError
from hb.schemas import validate

ROOT = Path(__file__).resolve().parent.parent
DOCKER = ROOT / "docker"
WORKFLOW = ROOT / ".github" / "workflows" / "task-base.yml"
IMAGE = "ghcr.io/sigormatt/hb-task-base@sha256:" + "a" * 64
BUILD = "c" * 40
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=hb-test", "-c", "user.email=hb-test@example.com",
                           "-c", "commit.gpgsign=false", *args], env=GIT_ENV, check=True, capture_output=True,
                          text=True).stdout.strip()


def cli(repo: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    """scripts/task_base.py under -I -S, so no third-party package can be imported."""
    return subprocess.run([sys.executable, "-I", "-S", str(ROOT / "scripts" / "task_base.py"), "--root", str(repo),
                           *args], env=env or GIT_ENV, capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository holding docker/, at its build commit."""
    root = tmp_path / "repo"
    shutil.copytree(DOCKER, root / "docker")
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "--message", "build commit")
    return root


# ------------------------------------------------------------------ the build files

def python_image(dockerfile: str) -> str:
    found = re.findall(r"^ARG PYTHON_IMAGE=(\S+)$", (DOCKER / dockerfile).read_text(), re.MULTILINE)
    assert len(found) == 1, dockerfile
    return found[0]


def test_the_task_base_is_built_on_the_toolkits_python():
    # Probes run in the toolkit image, so agents and probes see the same interpreter.
    image = python_image("task-base.Dockerfile")
    assert image == python_image("toolkit-base.Dockerfile")
    assert re.fullmatch(r"python:3\.12-[a-z0-9.-]+@sha256:[0-9a-f]{64}", image)


def test_every_requirement_is_pinned_by_version_and_hash():
    lines = [line for line in (DOCKER / "task-base-requirements.txt").read_text().splitlines()
             if line and not line.startswith("#")]
    pinned = [re.fullmatch(r"([a-z0-9-]+)==([0-9][0-9A-Za-z.]*) --hash=sha256:[0-9a-f]{64}", line) for line in lines]
    assert all(pinned), lines
    names = [match.group(1) for match in pinned]
    assert names == sorted(set(names))
    assert "pytest" in names


def dockerfile_instructions() -> list[tuple[str, list[str]]]:
    """(instruction, shell words) of the task base Dockerfile, continuation lines joined, comments dropped."""
    text = (DOCKER / "task-base.Dockerfile").read_text()
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    joined = re.sub(r"\\\n", " ", "\n".join(lines))
    return [(line.split()[0], shlex.split(line)[1:]) for line in joined.splitlines() if line.strip()]


def run_commands() -> list[list[str]]:
    """The commands of the Dockerfile's RUN instructions, split at &&."""
    commands = []
    for instruction, words in dockerfile_instructions():
        if instruction == "RUN":
            command = []
            for word in words + ["&&"]:
                if word == "&&":
                    commands.append(command)
                    command = []
                else:
                    command.append(word)
    return commands


def test_the_dockerfile_installs_exactly_the_hashed_requirements():
    copies = [words for instruction, words in dockerfile_instructions() if instruction == "COPY"]
    assert copies == [["task-base-requirements.txt", "/opt/hb/task-base-requirements.txt"]]
    pip = [c for c in run_commands() if c[:4] == ["python3", "-m", "pip", "install"]]
    assert len(pip) == 1
    assert {"--require-hashes", "--no-cache-dir"} <= set(pip[0])
    assert pip[0][pip[0].index("--only-binary") + 1] == ":all:"
    assert pip[0][pip[0].index("--requirement") + 1] == "/opt/hb/task-base-requirements.txt"
    assert [c[-1] for c in run_commands() if c[:3] == ["python3", "-m", "pip"]] == [
        "/opt/hb/task-base-requirements.txt"]  # no other pip command


def test_the_dockerfile_gives_the_harness_user_an_account_and_git_an_identity():
    commands = run_commands()
    useradd = next(c for c in commands if c[0] == "useradd")
    assert useradd[useradd.index("--uid") + 1] == str(profile.HARNESS_UID)
    assert profile.HARNESS_USER == f"{profile.HARNESS_UID}:{profile.HARNESS_UID}"
    assert useradd[useradd.index("--gid") + 1] == str(profile.HARNESS_UID)
    assert useradd[useradd.index("--home-dir") + 1] == "/tmp"  # the harness container's HOME
    configs = {c[3]: c[4] for c in commands if c[:3] == ["git", "config", "--system"]}
    assert set(configs) == {"user.name", "user.email", "init.defaultBranch"}
    instructions = [instruction for instruction, _ in dockerfile_instructions()]
    assert "ENTRYPOINT" not in instructions and "USER" not in instructions  # harness images build on it as it is


def workflow_profile() -> list[str]:
    lines = [line.strip() for line in WORKFLOW.read_text().splitlines()]
    start = next(i for i, line in enumerate(lines) if line.startswith("profile=("))
    stop = next(i for i in range(start, len(lines)) if lines[i].endswith(")"))
    return shlex.split(" ".join(lines[start:stop + 1])[len("profile=("):-1])


def flag_values(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


SWITCHES = {"--rm", "--read-only"}  # docker flags without a value


def flag_pairs(args: list[str]) -> list[tuple[str, ...]]:
    """(flag,) or (flag, value) for every docker flag in args; other words are skipped."""
    pairs, i = [], 0
    while i < len(args):
        if args[i] in SWITCHES:
            pairs.append((args[i],))
        elif args[i].startswith("-"):
            pairs.append((args[i], args[i + 1]))
            i += 1
        i += 1
    return pairs


# What the trial does differently from a harness container: no container to keep, no gateway
# network to join, and no workspace volume to work in.
ONLY_IN_THE_TRIAL = {("--rm",), ("--network", "none"), ("--workdir", "/tmp")}


def test_the_workflow_tries_the_image_with_the_harness_containers_profile():
    cohort = json.loads((ROOT / "examples" / "cohort.json").read_text())
    harness = flag_pairs(profile.harness_create_args(profile.rep_names("t", 1, 1), cohort, "image", ["run"]))
    tried = flag_pairs(workflow_profile())
    for pair in tried:
        if pair in ONLY_IN_THE_TRIAL:
            continue
        if pair[0] == "--tmpfs":  # the harness container's also caps the size
            assert [p for p in harness if p[0] == "--tmpfs"][0][1].startswith(pair[1] + ",")
        else:
            assert pair in harness, pair
    for pair in [("--read-only",), ("--user", profile.HARNESS_USER), ("--cap-drop", "ALL"),
                 ("--security-opt", "no-new-privileges"), ("--env", "HOME=/tmp"), ("--network", "none")]:
        assert pair in tried, pair


def test_every_trial_run_uses_the_profile_and_nothing_else():
    lines = [line.strip() for line in WORKFLOW.read_text().splitlines()]
    runs = [line for line in lines if line.startswith("docker run")]
    assert len(runs) >= 3
    for line in runs:
        assert line.startswith('docker run "${profile[@]}" "$IMAGE:$TAG" '), line


# ------------------------------------------------------------------ pins, task-base.json, verify commands

def test_image_tags_name_the_build():
    assert taskbase.image_tag(BUILD) == "c" * 12
    with pytest.raises(ReleaseError, match="full commit sha"):
        taskbase.image_tag("HEAD")


def test_task_base_pins():
    pins = {"build_sha": BUILD, "image": IMAGE}
    assert taskbase.check_pins(pins) == pins
    document = taskbase.task_base_document(pins)
    assert document == {"schema": "hb/task-base/1", "image": IMAGE, "build_commit": BUILD}
    assert taskbase.check_task_base(document) == document
    validate("task-base", document)


BAD_IMAGES = ["ghcr.io/sigormatt/hb-toolkit@sha256:" + "a" * 64, "ghcr.io/sigormatt/hb-task-base:latest",
              "ghcr.io/sigormatt/hb-task-base@sha256:" + "0" * 64]  # the last is the placeholder


@pytest.mark.parametrize("pins", [
    [],
    {"build_sha": BUILD},
    {"build_sha": BUILD, "image": IMAGE, "extra": 1},
    {"build_sha": "c" * 39, "image": IMAGE},
] + [{"build_sha": BUILD, "image": image} for image in BAD_IMAGES])
def test_malformed_task_base_pins_are_refused(pins):
    with pytest.raises(ReleaseError):
        taskbase.check_pins(pins)


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema="hb/task-base/2"),
    lambda d: d.pop("build_commit"),
    lambda d: d.update(extra=1),
    lambda d: d.update(build_commit="HEAD"),
] + [lambda d, image=image: d.update(image=image) for image in BAD_IMAGES])
def test_a_malformed_task_base_file_is_refused(change):
    document = taskbase.task_base_document({"build_sha": BUILD, "image": IMAGE})
    change(document)
    with pytest.raises(ReleaseError, match="task-base.json"):
        taskbase.check_task_base(document)


def test_verify_commands():
    assert taskbase.verify_commands(IMAGE, BUILD) == [
        ["docker", "pull", "--quiet", IMAGE],
        ["gh", "attestation", "verify", "oci://" + IMAGE, "--repo", "SigorMatt/harness-bench",
         "--signer-workflow", "SigorMatt/harness-bench/.github/workflows/task-base.yml",
         "--source-digest", BUILD, "--source-ref", "refs/heads/main", "--deny-self-hosted-runners"],
    ]


# ------------------------------------------------------------------ the command line

def test_names_and_pins_need_only_the_standard_library(repo, tmp_path):
    names = cli(repo, "names", "--build-sha", BUILD)
    assert names.returncode == 0, names.stderr
    assert names.stdout.splitlines() == ["image=ghcr.io/sigormatt/hb-task-base", "tag=" + "c" * 12]
    assert cli(repo, "names", "--build-sha", "HEAD").returncode == 1
    out = tmp_path / "task-base-pins.json"
    proc = cli(repo, "pins", "--build-sha", BUILD, "--image", IMAGE, "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert taskbase.read_pins_file(out) == {"build_sha": BUILD, "image": IMAGE}
    out.unlink()
    proc = cli(repo, "pins", "--build-sha", BUILD, "--image", "x", "--out", str(out))
    assert proc.returncode == 1 and not out.exists()


def test_names_refuses_a_gitattributes_file(repo):
    (repo / ".gitattributes").write_text("*.txt text eol=crlf\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "--quiet", "--message", "attributes")
    proc = cli(repo, "names", "--build-sha", BUILD)
    assert proc.returncode == 1 and ".gitattributes" in proc.stderr


def test_names_refuses_a_checkout_without_the_dockerfile(repo):
    (repo / "docker" / "task-base.Dockerfile").unlink()
    proc = cli(repo, "names", "--build-sha", BUILD)
    assert proc.returncode == 1 and "task-base.Dockerfile is missing" in proc.stderr


def pins_file(repo: Path, **changes) -> Path:
    path = repo.parent / "task-base-pins.json"
    path.write_text(json.dumps({"build_sha": git(repo, "rev-parse", "HEAD"), "image": IMAGE, **changes}))
    return path


def pinned(repo: Path) -> dict:
    """Pin as the maintainer does and commit; returns task-base.json."""
    proc = cli(repo, "pin", str(pins_file(repo)))
    assert proc.returncode == 0, proc.stderr
    git(repo, "add", "task-base.json")
    git(repo, "commit", "--quiet", "--message", "Pin the task base")
    return json.loads((repo / "task-base.json").read_text())


def test_pin_writes_task_base_json_from_the_build(repo):
    build = git(repo, "rev-parse", "HEAD")
    document = pinned(repo)
    assert document == {"schema": "hb/task-base/1", "image": IMAGE, "build_commit": build}
    validate("task-base", document)
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 0, proc.stderr
    assert f"the build files are those of its build commit {build}" in proc.stdout


def test_an_unrelated_change_keeps_the_pin_valid(repo):
    pins = pins_file(repo)
    (repo / "docker" / "toolkit.Dockerfile").write_text("# changed\n")
    git(repo, "commit", "--quiet", "--all", "--message", "an unrelated change")
    assert cli(repo, "pin", str(pins)).returncode == 0


@pytest.mark.parametrize("build_file", taskbase.BUILD_FILES)
def test_pin_and_verify_refuse_changed_build_files(repo, build_file):
    pins = pins_file(repo)
    with open(repo / build_file, "a", encoding="utf-8") as fh:
        fh.write("# changed\n")
    git(repo, "commit", "--quiet", "--all", "--message", "a change to the task base")
    proc = cli(repo, "pin", str(pins))
    assert proc.returncode == 1 and f"{build_file} changed since" in proc.stderr
    assert not (repo / "task-base.json").exists()


def test_verify_refuses_build_files_changed_after_the_pin(repo):
    pinned(repo)
    (repo / "docker" / "task-base-requirements.txt").write_text("pytest==9.1.0 --hash=sha256:" + "0" * 64 + "\n")
    git(repo, "commit", "--quiet", "--all", "--message", "a later change to the task base")
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 1 and "run task-base.yml again" in proc.stderr


def test_pin_refuses_uncommitted_changes_and_malformed_pins(repo):
    (repo / "docker" / "task-base.Dockerfile").write_text("FROM scratch\n")
    proc = cli(repo, "pin", str(pins_file(repo)))
    assert proc.returncode == 1 and "uncommitted changes" in proc.stderr
    git(repo, "checkout", "--", "docker")
    proc = cli(repo, "pin", str(pins_file(repo, image="ghcr.io/sigormatt/hb-task-base:latest")))
    assert proc.returncode == 1 and "pinned by digest" in proc.stderr


def test_verify_refuses_a_missing_or_malformed_task_base_json(repo):
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 1 and "task-base.json is missing" in proc.stderr
    (repo / "task-base.json").write_text(json.dumps({"schema": "hb/task-base/1", "image": IMAGE}))
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 1 and "expected exactly the keys" in proc.stderr


def fake_tools(tmp_path: Path, gh_exit: int = 0) -> tuple[dict, Path]:
    """PATH with recording stand-ins for docker and gh (git stays reachable). Returns the env and the log.

    Each call is logged with the number of entries in its DOCKER_CONFIG.
    """
    bin_dir, log = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir(parents=True)
    (bin_dir / "git").symlink_to(shutil.which("git"))
    record = ('n=0; for f in "$DOCKER_CONFIG"/* "$DOCKER_CONFIG"/.[!.]*; do [ -e "$f" ] && n=$((n + 1)); done\n'
              'printf "%s|%s|%s\\n" "${0##*/}" "$*" "$n" >> ' + str(log) + "\n")
    docker = "#!/bin/sh\n" + record + 'case "$1" in\n  context) echo unix:///fake.sock ;;\nesac\nexit 0\n'
    for name, script in {"gh": f"#!/bin/sh\n{record}exit {gh_exit}\n", "docker": docker}.items():
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)
    env = {k: v for k, v in GIT_ENV.items() if k != "DOCKER_HOST"}
    env["PATH"] = str(bin_dir)
    return env, log


def test_verify_pulls_anonymously_and_checks_provenance(repo, tmp_path):
    document = pinned(repo)
    env, log = fake_tools(tmp_path)
    proc = cli(repo, "verify", env=env)
    assert proc.returncode == 0, proc.stderr
    made = [line.split("|") for line in log.read_text().splitlines()]
    after_context = [(tool, args) for tool, args, _ in made if not args.startswith("context")]
    assert after_context == [(c[0], " ".join(c[1:])) for c in taskbase.verify_commands(
        IMAGE, document["build_commit"])]
    # An empty Docker configuration for every call after the one that reads the current context.
    assert {entries for _, args, entries in made if not args.startswith("context")} == {"0"}
    assert f"built by task-base.yml from {document['build_commit']} on main" in proc.stdout


def test_verify_refuses_a_failing_attestation(repo, tmp_path):
    pinned(repo)
    env, _ = fake_tools(tmp_path, gh_exit=1)
    proc = cli(repo, "verify", env=env)
    assert proc.returncode == 1 and "failed (exit status 1): gh attestation verify" in proc.stderr


def test_build_files_changed_compares_committed_objects_and_modes(repo):
    build = git(repo, "rev-parse", "HEAD")
    (repo / "docker" / "task-base.Dockerfile").write_text("FROM scratch\n")  # uncommitted: not compared
    assert taskbase.build_files_changed(Git(repo), build) == []
    git(repo, "checkout", "--", "docker")
    git(repo, "update-index", "--chmod=+x", "docker/task-base-requirements.txt")  # COPY keeps the mode
    git(repo, "commit", "--quiet", "--message", "an executable requirements file")
    assert taskbase.build_files_changed(Git(repo), build) == ["docker/task-base-requirements.txt"]
    git(repo, "rm", "--quiet", "--force", "docker/task-base-requirements.txt")
    git(repo, "commit", "--quiet", "--message", "no requirements")
    assert taskbase.build_files_changed(Git(repo), build) == ["docker/task-base-requirements.txt"]


def test_build_files_missing_at_both_commits_are_changed(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("no docker/ here\n")
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "--message", "no build files")
    build = git(root, "rev-parse", "HEAD")
    assert taskbase.build_files_changed(Git(root), build) == list(taskbase.BUILD_FILES)
    with pytest.raises(ReleaseError, match="run task-base.yml again"):
        taskbase.check_build_files_unchanged(Git(root), build)


def test_task_base_at_reads_the_committed_file(repo):
    document = pinned(repo)
    (repo / "task-base.json").write_text("{}")  # uncommitted: not read
    assert taskbase.task_base_at(Git(repo), "HEAD") == document
    with pytest.raises(ReleaseError):
        taskbase.task_base_at(Git(repo), "HEAD~1")  # the build commit has no task-base.json


# ------------------------------------------------------------------ harness images and the suite

BASE_LAYERS = ["sha256:" + "1" * 64, "sha256:" + "2" * 64]


def inspect_with(layers, repo_digests=None) -> list:
    return [{"Id": "sha256:" + "9" * 64, "RepoDigests": repo_digests or [], "RootFS": {"Type": "layers", "Layers": layers}}]


def test_an_image_built_on_the_task_base_starts_with_its_layers():
    base = inspect_with(BASE_LAYERS, [IMAGE])
    taskbase.check_built_on(inspect_with(BASE_LAYERS + ["sha256:" + "3" * 64]), base)
    taskbase.check_built_on(inspect_with(BASE_LAYERS), base)  # adding nothing is still built on it
    assert taskbase.pulled_as(base, IMAGE) and not taskbase.pulled_as(base, IMAGE.replace("a", "b"))
    assert not taskbase.pulled_as(base + base, IMAGE)


@pytest.mark.parametrize("layers", [
    ["sha256:" + "3" * 64] + BASE_LAYERS,  # the base's layers, but not first
    BASE_LAYERS[1:] + ["sha256:" + "3" * 64],
    BASE_LAYERS[:1],  # fewer than the base
    [BASE_LAYERS[1], BASE_LAYERS[0]],
])
def test_an_image_not_built_on_the_task_base_is_refused(layers):
    with pytest.raises(ReleaseError, match="not built on the task base"):
        taskbase.check_built_on(inspect_with(layers), inspect_with(BASE_LAYERS))


@pytest.mark.parametrize("inspect", [[], [{}], [{"RootFS": {"Layers": []}}], [{"RootFS": {"Layers": ["x"]}}],
                                     inspect_with(BASE_LAYERS) * 2, {"RootFS": {"Layers": BASE_LAYERS}}])
def test_an_inspection_without_layer_digests_for_one_image_is_refused(inspect):
    with pytest.raises(ReleaseError, match="no layer digests"):
        taskbase.image_layers(inspect)


def test_the_suite_must_pin_the_pinned_task_base():
    document = taskbase.task_base_document({"build_sha": BUILD, "image": IMAGE})
    taskbase.check_suite_task_base({"runtime": {"task_base": IMAGE}}, document)
    for suite in ({"runtime": {"task_base": IMAGE.replace("a", "b")}}, {"runtime": {}}, {}, []):
        with pytest.raises(ReleaseError, match="runtime.task_base"):
            taskbase.check_suite_task_base(suite, document)


def add_suite(repo: Path, task_base: str = IMAGE.replace("a", "e")) -> Path:
    """suites/py-maint/suite.json from the example suite, pinning task_base; committed."""
    suite = json.loads((ROOT / "examples" / "suite" / "suite.json").read_text())
    suite["runtime"]["task_base"] = task_base
    path = repo / "suites" / "py-maint" / "suite.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(suite, indent=2) + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "the suite")
    return path


def test_pin_sets_the_suites_task_base_and_nothing_else(repo):
    suite = add_suite(repo)
    before = suite.read_text()
    proc = cli(repo, "pin", str(pins_file(repo)))
    assert proc.returncode == 0, proc.stderr
    assert "git add task-base.json suites/py-maint/suite.json" in proc.stdout
    changed = [line for line in difflib_lines(before, suite.read_text())]
    assert changed == [f'-    "task_base": "{IMAGE.replace("a", "e")}"', f'+    "task_base": "{IMAGE}"']
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "Pin the task base")
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 0 and "runtime.task_base is the same image" in proc.stdout, proc.stderr


def test_the_workflows_commit_steps_take_the_suite_with_the_pin():
    # pin may rewrite the suite as well as task-base.json; a step staging only
    # task-base.json would leave the suite naming the old task base
    steps = [line for line in WORKFLOW.read_text().splitlines() if "git commit" in line]
    assert len(steps) == 2
    for line in steps:
        assert "git commit --all " in line and "git add" not in line, line


def difflib_lines(before: str, after: str) -> list[str]:
    import difflib
    return [line.rstrip("\n") for line in difflib.unified_diff(before.splitlines(True), after.splitlines(True), n=0)
            if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def test_verify_refuses_a_suite_that_names_another_task_base(repo):
    pinned(repo)
    add_suite(repo)
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 1 and "runtime.task_base is" in proc.stderr and "not the task base" in proc.stderr


def test_pin_refuses_a_suite_it_cannot_set_exactly_once(repo):
    suite = add_suite(repo)
    suite.write_text(suite.read_text().replace('"track"', '"task_base": "x",\n  "track"'))
    git(repo, "commit", "--quiet", "--all", "--message", "two task bases")
    proc = cli(repo, "pin", str(pins_file(repo)))
    assert proc.returncode == 1 and 'must hold "task_base" exactly once' in proc.stderr
    assert not (repo / "task-base.json").exists()

