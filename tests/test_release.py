"""Release tooling: inputs, pins, the release commit and the release table entry (spec §6.3).

The network steps (pushing, pulling, verifying attestations) are not run here:
verify_commands() is checked as the argument lists it returns.
"""

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hb import release
from hb.taskbase import TASK_BASE_FILE
from hb.release import Git, ReleaseError

ROOT = Path(__file__).resolve().parent.parent
OTHER_TASK_BASE = "ghcr.io/sigormatt/hb-task-base@sha256:" + "6" * 64
EXAMPLES = ROOT / "examples"
BENCH = ROOT / ".github" / "workflows" / "bench.yml"
TOOLKIT_REF = "ghcr.io/sigormatt/hb-toolkit@sha256:" + "a" * 64
SUITE_REF = "ghcr.io/sigormatt/hb-suite-py-maint@sha256:" + "b" * 64
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
# bench.yml with placeholder pins, as on main, whatever this checkout is (a release tag pins real images).
PLACEHOLDER_BENCH = re.sub(r"^(  HB_(?:TOOLKIT|SUITE)_IMAGE: [^@\s]+@)sha256:[0-9a-f]{64}$",
                           lambda m: m.group(1) + release.PLACEHOLDER_DIGEST, BENCH.read_text(), flags=re.MULTILINE)
NO_JSONSCHEMA = ("import runpy, sys; sys.modules['jsonschema'] = sys.modules['referencing'] = None; "
                 "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")
# Text mode as on Windows, where a file written with newline=None gets os.linesep, "\r\n", for each "\n".
WINDOWS_TEXT = """
import builtins, io, runpy, sys
open_ = io.open
def windows_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, *args, **kwargs):
    if newline is None and "b" not in mode and set(mode) & set("wax"):
        newline = "\\r\\n"
    return open_(file, mode, buffering, encoding, errors, newline, *args, **kwargs)
io.open = builtins.open = windows_open
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=hb-test", "-c", "user.email=hb-test@example.com",
                           "-c", "commit.gpgsign=false", *args], env=GIT_ENV, check=True, capture_output=True,
                          text=True).stdout.strip()


def cli(repo: Path, *args: str, env=None, stdlib_only=False, windows=False) -> subprocess.CompletedProcess:
    """scripts/release.py; with stdlib_only, importing jsonschema or referencing fails, as in the release job;
    with windows, text files are written as on Windows."""
    prelude = NO_JSONSCHEMA if stdlib_only else WINDOWS_TEXT if windows else None
    prefix = [sys.executable, "-c", prelude] if prelude else [sys.executable]
    return subprocess.run([*prefix, str(ROOT / "scripts" / "release.py"), "--root", str(repo), *args],
                          env=env or GIT_ENV, capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository with the release layout: the example suite and registry where a release takes them."""
    root = tmp_path / "repo"
    shutil.copytree(EXAMPLES / "suite", root / release.SUITE_DIR)
    shutil.copy(EXAMPLES / "registry.json", root / release.REGISTRY_FILE)
    shutil.copy(EXAMPLES / "task-base.json", root / TASK_BASE_FILE)
    (root / release.BENCH_WORKFLOW).parent.mkdir(parents=True)
    (root / release.BENCH_WORKFLOW).write_text(PLACEHOLDER_BENCH)
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "--message", "build commit")
    return root


def check_inputs(repo: Path) -> dict:
    return release.check_inputs(repo / release.SUITE_DIR, repo / release.REGISTRY_FILE, repo / TASK_BASE_FILE)


def pins_file(repo: Path, **changes) -> Path:
    pins = {"version": "v0.1.0", "build_sha": git(repo, "rev-parse", "HEAD"),
            "toolkit_image": TOOLKIT_REF, "suite_image": SUITE_REF, **changes}
    path = repo.parent / "release-pins.json"
    path.write_text(json.dumps(pins))
    return path


def release_commit(repo: Path) -> str:
    """Pin with the CLI and commit, as the maintainer does. Returns the release commit."""
    proc = cli(repo, "pin", str(pins_file(repo)))
    assert proc.returncode == 0, proc.stderr
    git(repo, "commit", "--quiet", "--all", "--message", "Release v0.1.0")
    return git(repo, "rev-parse", "HEAD")


# ------------------------------------------------------------------ values

@pytest.mark.parametrize("version", ["v0.1.0", "v1.0.0", "v10.20.30"])
def test_versions(version):
    assert release.check_version(version) == version


@pytest.mark.parametrize("version", ["0.1.0", "v1.0", "v01.0.0", "v1.0.0-rc1", "v1.0.0\n", "V1.0.0", "", None])
def test_malformed_versions_are_refused(version):
    with pytest.raises(ReleaseError):
        release.check_version(version)


@pytest.mark.parametrize("ref", [
    "ghcr.io/sigormatt/hb-toolkit:v0.1.0",                          # a tag, not a digest
    "ghcr.io/sigormatt/hb-toolkit@sha256:" + "A" * 64,              # not lowercase hex
    "ghcr.io/sigormatt/hb-toolkit@sha256:" + "a" * 63,
    "ghcr.io/someone/hb-toolkit@sha256:" + "a" * 64,                # another repository
    "ghcr.io/sigormatt/hb-toolkit@" + release.PLACEHOLDER_DIGEST,   # the placeholder
    None,
])
def test_image_refs_must_be_the_repository_pinned_by_a_real_digest(ref):
    with pytest.raises(ReleaseError):
        release.check_image(ref, release.TOOLKIT_REPOSITORY)


# ------------------------------------------------------------------ bench.yml pins

def test_bench_yml_on_main_pins_placeholders():
    # True on main and every branch (CI runs on branches only); a release tag's commit pins real images.
    pins = release.read_pins(BENCH.read_text())
    assert pins == {"toolkit_image": release.TOOLKIT_REPOSITORY + "@" + release.PLACEHOLDER_DIGEST,
                    "suite_image": release.suite_repository("py-maint") + "@" + release.PLACEHOLDER_DIGEST}


def test_pinning_changes_the_two_pin_lines_and_nothing_else():
    before = PLACEHOLDER_BENCH
    after = release.pin_workflow(before, toolkit_image=TOOLKIT_REF, suite_image=SUITE_REF)
    assert release.read_pins(after) == {"toolkit_image": TOOLKIT_REF, "suite_image": SUITE_REF}
    changed = [line for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line[:2] in ("- ", "+ ")]
    assert changed == [f"- {line}" if n < 2 else f"+ {line}" for n, line in enumerate([
        f"  HB_TOOLKIT_IMAGE: {release.TOOLKIT_REPOSITORY}@{release.PLACEHOLDER_DIGEST}",
        f"  HB_SUITE_IMAGE: {release.suite_repository('py-maint')}@{release.PLACEHOLDER_DIGEST}",
        f"  HB_TOOLKIT_IMAGE: {TOOLKIT_REF}",
        f"  HB_SUITE_IMAGE: {SUITE_REF}",
    ])]


@pytest.mark.parametrize("text", [
    "env:\n  HB_TOOLKIT_IMAGE: x\n",                                          # no suite pin
    "env:\n  HB_TOOLKIT_IMAGE: x\n  HB_SUITE_IMAGE: y\n  HB_SUITE_IMAGE: z\n",  # a pin twice
    "env:\n    HB_TOOLKIT_IMAGE: x\n    HB_SUITE_IMAGE: y\n",                  # not the env block's indentation
])
def test_pins_must_appear_exactly_once(text):
    with pytest.raises(ReleaseError):
        release.read_pins(text)
    with pytest.raises(ReleaseError):
        release.pin_workflow(text, toolkit_image=TOOLKIT_REF, suite_image=SUITE_REF)


# ------------------------------------------------------------------ release-pins.json

def good_pins(**changes) -> dict:
    return {"version": "v0.1.0", "build_sha": "c" * 40, "toolkit_image": TOOLKIT_REF, "suite_image": SUITE_REF,
            **changes}


def test_release_pins():
    assert release.check_pins(good_pins()) == good_pins()


@pytest.mark.parametrize("pins", [
    {k: v for k, v in good_pins().items() if k != "version"},
    good_pins(extra=1),
    good_pins(version="0.1.0"),
    good_pins(build_sha="c" * 39),
    good_pins(build_sha="HEAD"),
    good_pins(toolkit_image=SUITE_REF),
    good_pins(suite_image=TOOLKIT_REF),
    good_pins(suite_image="ghcr.io/sigormatt/hb-suite-py-maint@" + release.PLACEHOLDER_DIGEST),
    good_pins(suite_image=None),
    [],
])
def test_malformed_release_pins_are_refused(pins):
    with pytest.raises(ReleaseError):
        release.check_pins(pins)


# ------------------------------------------------------------------ inputs

def test_release_inputs(repo):
    inputs = check_inputs(repo)
    assert inputs["suite_id"] == "py-maint"
    assert inputs["harness_ids"] == ["baseline", "example-harness"]


def edit_json(path: Path, change) -> None:
    document = json.loads(path.read_text())
    change(document)
    path.write_text(json.dumps(document))


@pytest.mark.parametrize("break_it,message", [
    (lambda r: shutil.rmtree(r / release.SUITE_DIR), "suite is not in the repository"),
    (lambda r: (r / release.REGISTRY_FILE).unlink(), "registry is not in the repository"),
    (lambda r: (r / release.REGISTRY_FILE).write_text("{"), "cannot be read as JSON"),
    (lambda r: edit_json(r / release.REGISTRY_FILE, lambda d: d["harnesses"][0].pop("image")), "schema"),
    (lambda r: edit_json(r / release.REGISTRY_FILE, lambda d: d["harnesses"].append(d["harnesses"][0])),
     "duplicate"),
    (lambda r: edit_json(r / release.SUITE_DIR / "suite.json", lambda d: d.pop("budget")), "schema"),
    (lambda r: edit_json(r / release.SUITE_DIR / "suite.json", lambda d: d["tasks"].append({
        "id": "missing-task", "category": d["tasks"][0]["category"]})), "missing-task"),
    (lambda r: edit_json(r / release.SUITE_DIR / "suite.json",  # cannot fit in a hosted job
                         lambda d: (d.update(reps=10), d["infra"].update(max_reruns_per_rep=3))), "minutes"),
    (lambda r: edit_json(next((r / release.SUITE_DIR / "tasks").glob("*/task.json")), lambda d: d.pop("scope")),
     "schema"),
    (lambda r: (r / TASK_BASE_FILE).unlink(), "no task base is pinned"),
    (lambda r: edit_json(r / TASK_BASE_FILE, lambda d: d.pop("build_commit")), "expected exactly the keys"),
    (lambda r: edit_json(r / TASK_BASE_FILE, lambda d: d.update(image=OTHER_TASK_BASE)),
     "runtime.task_base is .* not the task base task-base.json pins"),
    (lambda r: edit_json(r / release.SUITE_DIR / "suite.json", lambda d: d["runtime"].update(task_base=OTHER_TASK_BASE)),
     "runtime.task_base is .* not the task base task-base.json pins"),
    (lambda r: edit_json(r / release.REGISTRY_FILE, lambda d: d["harnesses"][1].update(task_base=OTHER_TASK_BASE)),
     "not built on the pinned task base: example-harness"),
    (lambda r: edit_json(r / release.REGISTRY_FILE, lambda d: d["harnesses"][0].pop("task_base")), "schema"),
    (lambda r: edit_json(r / release.REGISTRY_FILE, lambda d: d["harnesses"][0].pop("build_commit")), "schema"),
])
def test_inputs_no_run_could_use_are_refused(repo, break_it, message):
    break_it(repo)
    with pytest.raises(ReleaseError, match=message):
        check_inputs(repo)


@pytest.mark.parametrize("break_it,message", [
    # Nothing at run time notices a missing repo: every rep would start with an empty workspace.
    (lambda task: shutil.rmtree(task / "repo"), "repo is not a directory"),
    (lambda task: shutil.rmtree(task / "repo") or (task / "repo").write_text(""), "repo is not a directory"),
    (lambda task: (task / "instruction.md").unlink(), "instruction.md is not a file"),
    (lambda task: (task / "probes" / "probe.py").unlink(), "probes/probe.py is not a file"),
    (lambda task: (task / "checks" / "target.json").unlink() or (task / "checks" / "target.json").mkdir(),
     "checks/target.json is not a file"),
])
def test_inputs_refuse_a_task_path_a_run_reads_that_is_not_there(repo, break_it, message):
    break_it(next((repo / release.SUITE_DIR / "tasks").iterdir()))
    with pytest.raises(ReleaseError, match=message):
        check_inputs(repo)


def test_inputs_command_prints_the_workflow_outputs(repo):
    proc = cli(repo, "inputs", "--version", "v0.1.0")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "version=v0.1.0", f"toolkit={release.TOOLKIT_REPOSITORY}", "suite=ghcr.io/sigormatt/hb-suite-py-maint",
        f"suite_dir={release.SUITE_DIR}", f"registry={release.REGISTRY_FILE}", "harness=baseline"]
    assert cli(repo, "inputs", "--version", "0.1.0").returncode == 1


def test_the_release_job_commands_need_only_the_standard_library(repo, tmp_path):
    # names and pins run in the privileged release job, where nothing from PyPI is installed.
    names = cli(repo, "names", "--version", "v0.1.0", stdlib_only=True)
    assert names.returncode == 0, names.stderr
    assert names.stdout == cli(repo, "inputs", "--version", "v0.1.0").stdout
    out = tmp_path / "release-pins.json"
    pins = cli(ROOT, "pins", "--version", "v0.1.0", "--build-sha", "c" * 40, "--toolkit-image", TOOLKIT_REF,
               "--suite-image", SUITE_REF, "--out", str(out), stdlib_only=True)
    assert pins.returncode == 0, pins.stderr
    assert release.read_pins_file(out) == good_pins()
    inputs = cli(repo, "inputs", "--version", "v0.1.0", stdlib_only=True)  # the check job installs jsonschema
    assert inputs.returncode != 0 and "jsonschema" in inputs.stderr


def test_the_placeholder_copy_undoes_a_release_pin():
    pinned = release.pin_workflow(PLACEHOLDER_BENCH, toolkit_image=TOOLKIT_REF, suite_image=SUITE_REF)
    assert re.sub(r"^(  HB_(?:TOOLKIT|SUITE)_IMAGE: [^@\s]+@)sha256:[0-9a-f]{64}$",
                  lambda m: m.group(1) + release.PLACEHOLDER_DIGEST, pinned, flags=re.MULTILINE) == PLACEHOLDER_BENCH


def test_inputs_refuse_a_gitattributes_file(repo):
    (repo / ".gitattributes").write_text("*.py text eol=crlf\n")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "attributes")
    proc = cli(repo, "inputs", "--version", "v0.1.0")
    assert proc.returncode == 1 and ".gitattributes" in proc.stderr


def test_inputs_refuse_a_symlink_in_the_suite(repo):
    (repo / release.SUITE_DIR / "link.json").symlink_to("suite.json")
    with pytest.raises(ReleaseError):
        check_inputs(repo)


@pytest.mark.parametrize("name", [".dockerignore", ".containerignore", ".DockerIgnore"])
def test_inputs_refuse_an_ignore_file_at_the_suite_root(repo, name):
    # The suite directory is the suite image's build context: the classic builder, Podman and
    # Buildah read these there (case-insensitively on macOS and Windows) and leave files out.
    (repo / release.SUITE_DIR / name).write_text("tasks\n")
    with pytest.raises(ReleaseError, match="would leave files out"):
        check_inputs(repo)
    (repo / release.SUITE_DIR / name).unlink()
    (repo / release.SUITE_DIR / name).symlink_to("missing")  # even one that points nowhere
    with pytest.raises(ReleaseError, match="would leave files out"):
        check_inputs(repo)


@pytest.mark.parametrize("names", [("x:y.txt",), ("a\\b.txt",), ("NUL",), ("tasks/con.txt",), ("a?b",),
                                   ("trail.",), ("dir /x",), ("Foo.py", "foo.py"),
                                   ("Docs/a.txt", "docs/b.txt"), ("A", "a/b.txt")])
def test_inputs_refuse_paths_some_file_system_cannot_hold(repo, names):
    # So that scripts/release.py entry can rebuild the suite tree on any maintainer's machine.
    for name in names:
        target = repo / release.SUITE_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(name)
    with pytest.raises(ReleaseError, match="not a portable path|differ only in case"):
        check_inputs(repo)


def test_portable_paths():
    release.check_portable_paths(["suite.json", "tasks/a/task.json", "tasks/a/repo/x.py", "tasks/b/task.json",
                                  ".hidden", "tasks/a/repo/.github/ci.yml", "console.py", "nul_test.py"])


NAMES = ["ok.py", ".hidden", "console.py", "COM0", "nul_test.py", "NUL", "nul", "con.txt", "COM1.json",
         "lpt9.a.b", "nul .txt", "CONOUT$", "com\u00b9", "a?b", "a<b", "a>b", 'a"b', "a|b", "a*b", "x:y",
         "a\\b", "tab\tname", "trail.", "trail ", "...", ".. "]


@pytest.mark.skipif(not hasattr(__import__("ntpath"), "isreserved"), reason="ntpath.isreserved is new in 3.13")
@pytest.mark.parametrize("name", NAMES)
def test_reserved_names_are_pythons(name):
    import ntpath
    if ":" in name or "\\" in name:  # isreserved() reads a path: a drive, or two names; refused here as names
        assert release.name_problem(name) is not None
    else:
        assert (release.name_problem(name) is not None) == ntpath.isreserved(name)


def test_a_dockerignore_deeper_in_the_suite_is_an_ordinary_file(repo):
    # No builder reads it, so it is copied like any other file.
    task = next((repo / release.SUITE_DIR / "tasks").iterdir())
    (task / ".dockerignore").write_text("repo\n")
    assert check_inputs(repo)["suite_id"] == "py-maint"


def test_pins_command_writes_only_what_pin_accepts(tmp_path):
    out = tmp_path / "release-pins.json"
    args = ["pins", "--version", "v0.1.0", "--build-sha", "c" * 40, "--toolkit-image", TOOLKIT_REF,
            "--suite-image", SUITE_REF, "--out", str(out)]
    assert cli(ROOT, *args).returncode == 0
    assert release.read_pins_file(out) == good_pins()
    out.unlink()
    args[args.index("--suite-image") + 1] = "ghcr.io/sigormatt/hb-suite-py-maint:v0.1.0"
    proc = cli(ROOT, *args)
    assert proc.returncode == 1 and "is not" in proc.stderr
    assert not out.exists()


# ------------------------------------------------------------------ the release commit

def test_pin_then_verify_offline(repo):
    build = git(repo, "rev-parse", "HEAD")
    commit = release_commit(repo)
    assert release.check_release_commit(Git(repo), "HEAD") == {
        "release_sha": commit, "build_sha": build, "toolkit_image": TOOLKIT_REF, "suite_image": SUITE_REF}
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 0, proc.stderr
    assert f"the build commit {build}" in proc.stdout


def test_pin_keeps_every_other_byte_where_text_mode_writes_crlf(repo):
    # Written as text on Windows, every line would end in "\r\n", and a clone that commits files as they are
    # (core.autocrlf false or unset) would record that: verify refuses such a commit.
    git(repo, "config", "core.autocrlf", "false")
    build = (repo / release.BENCH_WORKFLOW).read_bytes()
    proc = cli(repo, "pin", str(pins_file(repo)), windows=True)
    assert proc.returncode == 0, proc.stderr
    assert (repo / release.BENCH_WORKFLOW).read_bytes() == release.pin_workflow(
        build.decode(), toolkit_image=TOOLKIT_REF, suite_image=SUITE_REF).encode()
    git(repo, "commit", "--quiet", "--all", "--message", "Release v0.1.0")
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 0, proc.stderr


def test_pin_needs_the_build_commit_checked_out_and_clean(repo):
    pins = pins_file(repo)
    (repo / "notes.txt").write_text("x")
    git(repo, "add", "notes.txt")
    git(repo, "commit", "--quiet", "--message", "later work")
    proc = cli(repo, "pin", str(pins))
    assert proc.returncode == 1 and "not the build commit" in proc.stderr
    git(repo, "switch", "--quiet", "--detach", "HEAD~1")
    (repo / release.SUITE_DIR / "suite.json").write_text("{}")
    proc = cli(repo, "pin", str(pins))
    assert proc.returncode == 1 and "uncommitted changes" in proc.stderr
    assert release.read_pins((repo / release.BENCH_WORKFLOW).read_text())["toolkit_image"].endswith("0" * 64)


def test_pin_refuses_a_suite_image_named_for_another_suite(repo):
    pins = pins_file(repo, suite_image="ghcr.io/sigormatt/hb-suite-other@sha256:" + "b" * 64)
    proc = cli(repo, "pin", str(pins))
    assert proc.returncode == 1 and "hb-suite-py-maint" in proc.stderr


@pytest.mark.parametrize("extra_change,message", [
    (lambda r: (r / "README.md").write_text("x"), "changes only"),
    (lambda r: (r / release.SUITE_DIR / "suite.json").write_text("{}"), "changes only"),
    (lambda r: (r / release.BENCH_WORKFLOW).write_text((r / release.BENCH_WORKFLOW).read_text()
                                                        .replace("max-parallel: 8", "max-parallel: 64")),
     "only the two image pins"),
])
def test_a_release_commit_changes_nothing_but_the_pins(repo, extra_change, message):
    assert cli(repo, "pin", str(pins_file(repo))).returncode == 0
    extra_change(repo)
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "Release v0.1.0")
    with pytest.raises(ReleaseError, match=message):
        release.check_release_commit(Git(repo), "HEAD")
    assert cli(repo, "verify", "--offline").returncode == 1


def commit_after_pin(repo: Path, change) -> None:
    """Pin, stage it, make one more change to the index, and commit what is staged."""
    assert cli(repo, "pin", str(pins_file(repo))).returncode == 0
    git(repo, "add", "--all")
    change(repo)
    git(repo, "commit", "--quiet", "--message", "Release v0.1.0")


def add_file(repo: Path, name: str, text: str = "x") -> None:
    (repo / name).write_text(text)
    git(repo, "add", "--", name)


@pytest.mark.parametrize("change,message", [
    (lambda r: add_file(r, " "), "changes only"),                                  # a name of whitespace only
    (lambda r: add_file(r, "a\u00a0b"), "changes only"),                           # a non-ASCII name
    (lambda r: git(r, "update-index", "--add", "--cacheinfo", f"160000,{'d' * 40},vendor"), "changes only"),
    (lambda r: git(r, "update-index", "--chmod=+x", release.BENCH_WORKFLOW), "not its mode"),
    (lambda r: add_file(r, f"{release.SUITE_DIR}/.gitattributes", "* -text\n"), "changes only"),
])
def test_paths_git_might_hide_still_count(repo, change, message):
    commit_after_pin(repo, change)
    with pytest.raises(ReleaseError, match=message):
        release.check_release_commit(Git(repo), "HEAD")


def test_replace_refs_cannot_stand_in_for_a_release_commit(repo):
    build = git(repo, "rev-parse", "HEAD")
    good = release_commit(repo)
    git(repo, "switch", "--quiet", "--detach", build)
    commit_after_pin(repo, lambda r: add_file(r, "extra.txt"))
    bad = git(repo, "rev-parse", "HEAD")
    git(repo, "replace", bad, good)  # a local ref that makes git show the good commit for the bad one
    with pytest.raises(ReleaseError, match="changes only"):
        release.check_release_commit(Git(repo), bad)


def test_an_executable_bench_yml_may_stay_executable(repo):
    git(repo, "update-index", "--chmod=+x", release.BENCH_WORKFLOW)
    (repo / release.BENCH_WORKFLOW).chmod(0o755)
    git(repo, "commit", "--quiet", "--message", "executable")
    release_commit(repo)
    assert release.check_release_commit(Git(repo), "HEAD")["toolkit_image"] == TOOLKIT_REF


def test_export_takes_only_committed_regular_files_that_exist(repo, tmp_path):
    with pytest.raises(ReleaseError, match="has no missing.json"):
        Git(repo).export("HEAD", ["missing.json"], tmp_path / "a")
    (repo / release.SUITE_DIR / "link.json").symlink_to("suite.json")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "symlink")
    with pytest.raises(ReleaseError, match="link.json at HEAD is not a regular file"):
        Git(repo).export("HEAD", [release.SUITE_DIR], tmp_path / "b")


@pytest.mark.parametrize("name", ["a\\..\\..\\escape.txt", "c:escape.txt", "sub/x:y.txt", "NUL", "sub/con.json",
                                  "trail."])
def test_export_refuses_paths_another_file_system_reads_differently(repo, tmp_path, name):
    # Legal names in git and on Linux; on Windows a backslash separates and a colon names a drive,
    # so writing them could leave the scratch directory. Refused before anything is written.
    target = repo / release.SUITE_DIR / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "odd name")
    dest = tmp_path / "out"
    with pytest.raises(ReleaseError, match="not a portable path"):
        Git(repo).export("HEAD", [release.SUITE_DIR], dest)
    assert not dest.exists()


@pytest.mark.parametrize("names", [("Foo.py", "foo.py"), ("Docs/a.txt", "docs/b.txt"), ("A", "a/b.txt"),
                                   ("B/x.py", "b")])
def test_export_refuses_paths_the_file_system_cannot_hold_apart(repo, tmp_path, monkeypatch, names):
    # Two paths in git and on Linux, but not on a case-insensitive file system (macOS and Windows by default),
    # where the exported tree, and so the suite digest and the cohort, would not be the committed one.
    for name in names:
        (repo / release.SUITE_DIR / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / release.SUITE_DIR / name).write_text(name)
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "names that differ only in case")
    Git(repo).export("HEAD", [release.SUITE_DIR], tmp_path / "linux")
    assert all((tmp_path / "linux" / release.SUITE_DIR / name).read_text() == name for name in names)
    folded = tmp_path / "folded"
    write_bytes, mkdir = Path.write_bytes, Path.mkdir

    def existing(path):  # the entry a case-insensitive file system opens for path
        if folded not in path.parents:
            return path
        found = folded
        for part in path.relative_to(folded).parts:
            same = [p for p in found.iterdir() if p.name.casefold() == part.casefold()] if found.is_dir() else []
            found = same[0] if same else found / part
        return found

    monkeypatch.setattr(Path, "write_bytes", lambda path, data: write_bytes(existing(path), data))
    monkeypatch.setattr(Path, "mkdir", lambda path, *args, **kwargs: mkdir(existing(path), *args, **kwargs))
    with pytest.raises(ReleaseError, match="cannot hold apart|cannot be written"):
        Git(repo).export("HEAD", [release.SUITE_DIR], folded)


def test_a_release_commit_whose_build_has_gitattributes_is_refused(repo):
    (repo / ".gitattributes").write_text("*.json -text\n")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "attributes")
    commit_after_pin(repo, lambda r: None)
    with pytest.raises(ReleaseError, match="gitattributes"):
        release.check_release_commit(Git(repo), "HEAD")


@pytest.mark.parametrize("pins,message", [
    ({"toolkit_image": "ghcr.io/sigormatt/hb-other@sha256:" + "a" * 64, "suite_image": SUITE_REF}, "hb-toolkit"),
    ({"toolkit_image": TOOLKIT_REF, "suite_image": "ghcr.io/sigormatt/hb-suite-other@sha256:" + "b" * 64},
     "hb-suite-py-maint"),
    ({"toolkit_image": TOOLKIT_REF, "suite_image": SUITE_REF.split("@")[0] + "@" + release.PLACEHOLDER_DIGEST},
     "placeholder"),
])
def test_a_hand_made_release_commit_must_pin_this_releases_images(repo, pins, message):
    workflow = repo / release.BENCH_WORKFLOW
    workflow.write_text(release.pin_workflow(workflow.read_text(), **pins))
    git(repo, "commit", "--quiet", "--all", "--message", "Release v0.1.0")
    with pytest.raises(ReleaseError, match=message):
        release.check_release_commit(Git(repo), "HEAD")


def test_a_shallow_clone_is_named_as_the_problem(repo, tmp_path):
    release_commit(repo)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--quiet", "--depth", "1", f"file://{repo}", str(shallow)], env=GIT_ENV,
                   check=True, capture_output=True)
    proc = cli(shallow, "verify", "--offline")
    assert proc.returncode == 1 and "shallow" in proc.stderr


def test_pin_reports_unreadable_files_without_a_traceback(repo):
    (repo / release.SUITE_DIR / "suite.json").write_text("{")
    git(repo, "commit", "--quiet", "--all", "--message", "broken suite")
    proc = cli(repo, "pin", str(pins_file(repo)))
    assert proc.returncode == 1 and "cannot be read as JSON" in proc.stderr and "Traceback" not in proc.stderr
    git(repo, "revert", "--no-edit", "HEAD")
    (repo / release.BENCH_WORKFLOW).write_bytes(PLACEHOLDER_BENCH.encode() + b"# \xff\n")
    git(repo, "commit", "--quiet", "--all", "--message", "not utf-8")
    proc = cli(repo, "pin", str(pins_file(repo)))
    assert proc.returncode == 1 and "not UTF-8" in proc.stderr and "Traceback" not in proc.stderr


def test_a_release_commit_has_exactly_one_parent(repo):
    with pytest.raises(ReleaseError, match="exactly one parent"):
        release.check_release_commit(Git(repo), "HEAD")  # the root commit
    git(repo, "switch", "--quiet", "--create", "side")
    (repo / "side.txt").write_text("x")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "side")
    git(repo, "switch", "--quiet", "main")
    git(repo, "merge", "--quiet", "--no-ff", "--message", "merge", "side")
    with pytest.raises(ReleaseError, match="exactly one parent"):
        release.check_release_commit(Git(repo), "HEAD")


def test_placeholder_pins_are_not_a_release(repo):
    (repo / "x").write_text("x")  # a commit on top of the build commit, with bench.yml unchanged
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "--message", "x")
    with pytest.raises(ReleaseError):
        release.check_release_commit(Git(repo), "HEAD")


def test_verify_commands():
    pins = {"build_sha": "c" * 40, "toolkit_image": TOOLKIT_REF, "suite_image": SUITE_REF}
    expected = []
    for ref in (TOOLKIT_REF, SUITE_REF):
        expected += [
            ["docker", "pull", "--quiet", ref],
            ["gh", "attestation", "verify", f"oci://{ref}", "--repo", "SigorMatt/harness-bench",
             "--signer-workflow", "SigorMatt/harness-bench/.github/workflows/release.yml",
             "--source-digest", "c" * 40, "--source-ref", "refs/heads/main", "--deny-self-hosted-runners"],
        ]
    assert release.verify_commands(pins) == expected


def fake_tools(tmp_path: Path, gh_exit: int = 0, with_docker: bool = True) -> tuple[dict, Path]:
    """PATH with recording docker and gh stand-ins (git stays reachable). Returns the env and the log."""
    bin_dir, log = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir(parents=True)
    (bin_dir / "git").symlink_to(shutil.which("git"))
    # Shell builtins only: PATH holds nothing but this directory.
    record = ('n=0; for f in "$DOCKER_CONFIG"/* "$DOCKER_CONFIG"/.[!.]*; do [ -e "$f" ] && n=$((n + 1)); done\n'
              'printf "%s|%s|%s|%s\\n" "${0##*/}" "$*" "${DOCKER_HOST:-}" "$n" >> ' + str(log) + "\n")
    tools = {"gh": f"#!/bin/sh\n{record}exit {gh_exit}\n"}
    if with_docker:
        tools["docker"] = ("#!/bin/sh\n"
                           'if [ "$1" = context ]; then echo unix:///fake/docker.sock; exit 0; fi\n'
                           f"{record}exit 0\n")
    for name, script in tools.items():
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)
    env = {k: v for k, v in GIT_ENV.items() if k != "DOCKER_HOST"}
    env["PATH"] = str(bin_dir)
    return env, log


def test_verify_online_pulls_anonymously_from_the_current_daemon_and_checks_provenance(repo, tmp_path):
    build = git(repo, "rev-parse", "HEAD")
    release_commit(repo)
    env, log = fake_tools(tmp_path)
    proc = cli(repo, "verify", env=env)
    assert proc.returncode == 0, proc.stderr
    calls = [line.split("|") for line in log.read_text().splitlines()]
    assert [(tool, args) for tool, args, _, _ in calls] == [
        (command[0], " ".join(command[1:]))
        for command in release.verify_commands({"build_sha": build, "toolkit_image": TOOLKIT_REF,
                                                "suite_image": SUITE_REF})]
    assert {(host, entries) for tool, _, host, entries in calls if tool == "docker"} == {
        ("unix:///fake/docker.sock", "0")}  # the context's daemon, an empty Docker configuration


def test_verify_online_fails_when_a_check_fails_or_a_tool_is_missing(repo, tmp_path):
    release_commit(repo)
    env, _ = fake_tools(tmp_path / "a", gh_exit=1)
    proc = cli(repo, "verify", env=env)
    assert proc.returncode == 1 and "failed (exit status 1): gh attestation verify" in proc.stderr
    env, _ = fake_tools(tmp_path / "b", with_docker=False)
    proc = cli(repo, "verify", env=env)
    assert proc.returncode == 1 and "docker:" in proc.stderr and "Traceback" not in proc.stderr


# ------------------------------------------------------------------ the release table entry

def test_the_example_release_entry():
    example = json.loads((EXAMPLES / "release.json").read_text())
    registry = json.loads((EXAMPLES / "registry.json").read_text())
    assert release.release_entry(suite_dir=EXAMPLES / "suite", registry=registry,
                                 toolkit_image=example["toolkit_image"], suite_image=example["suite_image"],
                                 signer_sha=example["signer_sha"]) == example


def test_entry_comes_from_the_release_commit_alone(repo):
    commit = release_commit(repo)
    # Untracked and uncommitted files in the checkout change nothing.
    (repo / release.SUITE_DIR / "stray.txt").write_text("not committed")
    edit_json(repo / release.REGISTRY_FILE, lambda d: d["harnesses"].pop())
    proc = cli(repo, "entry")
    assert proc.returncode == 0, proc.stderr
    entry = json.loads(proc.stdout)
    registry = json.loads((EXAMPLES / "registry.json").read_text())
    assert entry == release.release_entry(suite_dir=EXAMPLES / "suite", registry=registry,
                                          toolkit_image=TOOLKIT_REF, suite_image=SUITE_REF, signer_sha=commit)
    from hb.schemas import validate
    validate("release", entry)


def test_entry_ignores_the_clones_git_settings(repo):
    commit = release_commit(repo)
    expected = json.loads(cli(repo, "entry").stdout)
    git(repo, "config", "core.autocrlf", "true")  # would change line endings in git archive or a checkout
    proc = cli(repo, "entry")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == expected and expected["signer_sha"] == commit


def test_entry_refuses_a_commit_that_is_not_a_release(repo):
    proc = cli(repo, "entry")
    assert proc.returncode == 1 and "exactly one parent" in proc.stderr
