"""Harness images: adapters, the registry entry and its checks (spec §6.4).

The network steps (pushing, pulling, verifying attestations) are not run here:
the verify commands are checked as argument lists, and the online path with
stand-ins for docker and gh.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hb import harness
from hb.manifest import tree_digest
from hb.release import Git, ReleaseError

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "harnesses" / "baseline"
IMAGE = "ghcr.io/sigormatt/hb-harness-baseline@sha256:" + "a" * 64
OTHER_IMAGE = "ghcr.io/sigormatt/hb-harness-baseline@sha256:" + "b" * 64
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}



def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=hb-test", "-c", "user.email=hb-test@example.com",
                           "-c", "commit.gpgsign=false", *args], env=GIT_ENV, check=True, capture_output=True,
                          text=True).stdout.strip()


def cli(repo: Path, *args: str, env=None, stdlib_only=False) -> subprocess.CompletedProcess:
    """scripts/harness.py; with stdlib_only, under -I -S, so no third-party package can be imported."""
    prefix = [sys.executable, "-I", "-S"] if stdlib_only else [sys.executable]
    return subprocess.run([*prefix, str(ROOT / "scripts" / "harness.py"), "--root", str(repo), *args],
                          env=env or GIT_ENV, capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository holding the baseline adapter, at its build commit."""
    root = tmp_path / "repo"
    shutil.copytree(BASELINE, root / "harnesses" / "baseline", ignore=shutil.ignore_patterns("__pycache__"))
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "--message", "build commit")
    return root


def pins_file(repo: Path, **changes) -> Path:
    pins = {"harness": "baseline", "build_sha": git(repo, "rev-parse", "HEAD"), "image": IMAGE, **changes}
    path = repo.parent / "harness-pins.json"
    path.write_text(json.dumps(pins))
    return path


def registry(repo: Path) -> dict:
    return json.loads((repo / "registry.json").read_text())


def pinned(repo: Path, **changes) -> dict:
    """Pin as the maintainer does and commit; returns the entry."""
    proc = cli(repo, "pin", str(pins_file(repo, **changes)))
    assert proc.returncode == 0, proc.stderr
    git(repo, "add", "registry.json")
    git(repo, "commit", "--quiet", "--message", "Register baseline")
    return next(e for e in registry(repo)["harnesses"] if e["id"] == "baseline")


# ------------------------------------------------------------------ the adapter

def test_the_baseline_adapter():
    adapter = harness.check_inputs(BASELINE, "baseline")
    assert adapter == json.loads((BASELINE / "harness.json").read_text())
    assert adapter["entrypoint"] == ["python3", "/hb/agent.py"]
    assert harness.config_digest(BASELINE) == tree_digest(BASELINE / "config")


def good_harness(**changes) -> dict:
    return {**json.loads((BASELINE / "harness.json").read_text()), **changes}


@pytest.mark.parametrize("document,message", [
    ({k: v for k, v in good_harness().items() if k != "homepage"}, "exactly the keys"),
    (good_harness(extra=1), "exactly the keys"),
    (good_harness(schema="hb/harness/2"), "schema"),
    (good_harness(id="other"), "directory's name"),
    (good_harness(version=""), "version"),
    (good_harness(version="1.0 beta"), "version"),
    (good_harness(homepage="http://example.com"), "https"),
    (good_harness(entrypoint=[]), "entrypoint"),
    (good_harness(entrypoint=["/hb/run me"]), "entrypoint"),
    (good_harness(entrypoint="/hb/entrypoint"), "entrypoint"),
    ([], "exactly the keys"),
])
def test_malformed_harness_json_is_refused(document, message):
    with pytest.raises(ReleaseError, match=message):
        harness.check_harness(document, "baseline")


@pytest.mark.parametrize("break_it,message", [
    (lambda a: shutil.rmtree(a), "does not exist"),
    (lambda a: (a / "Dockerfile").unlink(), "Dockerfile is missing"),
    (lambda a: shutil.rmtree(a / "config"), "missing or empty"),
    (lambda a: [p.unlink() for p in (a / "config").iterdir()], "missing or empty"),
    (lambda a: (a / "config" / "link.json").symlink_to("agent.json"), "config"),
    (lambda a: (a / "harness.json").write_text("{"), "cannot be read as JSON"),
])
def test_an_incomplete_adapter_is_refused(tmp_path, break_it, message):
    adapter = tmp_path / "harnesses" / "baseline"
    shutil.copytree(BASELINE, adapter, ignore=shutil.ignore_patterns("__pycache__"))
    break_it(adapter)
    with pytest.raises(ReleaseError, match=message):
        harness.load_adapter(adapter, "baseline")


def test_the_schema_has_the_same_rules(tmp_path):
    adapter = tmp_path / "harnesses" / "baseline"
    shutil.copytree(BASELINE, adapter, ignore=shutil.ignore_patterns("__pycache__"))
    from hb.schemas import validate
    validate("harness", json.loads((adapter / "harness.json").read_text()))
    with pytest.raises(ReleaseError):
        harness.check_inputs(tmp_path / "harnesses" / "missing", "missing")


@pytest.mark.parametrize("bad_id", ["Base", "../x", "", "a b", None])
def test_harness_ids_are_slugs(bad_id):
    with pytest.raises(ReleaseError):
        harness.adapter_path(bad_id)


def test_image_tags_name_the_version_and_the_build():
    assert harness.image_tag("0.1.0", "c" * 40) == "0.1.0-" + "c" * 12
    assert harness.image_tag("1.2.3+build.5", "d" * 40) == "1.2.3_build.5-" + "d" * 12
    assert harness.image_tag(".1", "d" * 40) == "v.1-" + "d" * 12  # a tag cannot start with '.' or '-'
    assert harness.image_tag("-rc", "d" * 40) == "v-rc-" + "d" * 12


# ------------------------------------------------------------------ the built image

def copied_hb(tmp_path: Path) -> Path:
    """/hb as the baseline Dockerfile lays it out."""
    hb_dir = tmp_path / "hb"
    shutil.copytree(BASELINE / "config", hb_dir / "config")
    shutil.copy(BASELINE / "agent.py", hb_dir / "agent.py")
    return hb_dir


def test_the_image_holds_config_and_the_entrypoints_files(tmp_path):
    harness.check_image_files(BASELINE, copied_hb(tmp_path), good_harness())


def inspect_of(labels=None, entrypoint=None) -> list:
    labels = {harness.HARNESS_LABEL: "baseline"} if labels is None else labels
    return [{"Id": "sha256:" + "9" * 64, "Config": {"Entrypoint": entrypoint, "Cmd": ["python3"], "Labels": labels}}]


def test_symlinks_in_the_copied_image_are_refused(tmp_path):
    # docker cp copies a symlink as a symlink; following it here would read this machine's files.
    real = copied_hb(tmp_path / "real")
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real)
    with pytest.raises(ReleaseError, match="is a symlink"):
        harness.check_image_files(BASELINE, linked_root, good_harness())
    linked_config = copied_hb(tmp_path / "linked-config")
    shutil.rmtree(linked_config / "config")
    (linked_config / "config").symlink_to(BASELINE / "config")  # the right files, but the host's
    with pytest.raises(ReleaseError, match="is a symlink"):
        harness.check_image_files(BASELINE, linked_config, good_harness())
    linked_entry = copied_hb(tmp_path / "linked-entry")
    (linked_entry / "agent.py").unlink()
    (linked_entry / "agent.py").symlink_to(BASELINE / "agent.py")
    with pytest.raises(ReleaseError, match="as a regular file"):
        harness.check_image_files(BASELINE, linked_entry, good_harness())
    nested = copied_hb(tmp_path / "nested")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "run").write_text("x")
    (nested / "bin").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ReleaseError, match="as a regular file"):
        harness.check_image_files(BASELINE, nested, good_harness(entrypoint=["/hb/bin/run"]))


@pytest.mark.parametrize("argument", ["/hb/../inspect.json", "/hb//etc/passwd", "/hb/./agent.py", "/hb/config/",
                                      "/hb/"])
def test_entrypoint_files_outside_the_copied_image_are_refused(tmp_path, argument):
    # The image job keeps its inspect.json next to the copied /hb, and /etc/passwd is the host's.
    (tmp_path / "inspect.json").write_text("[]")
    with pytest.raises(ReleaseError, match="as a regular file"):
        harness.check_image_files(BASELINE, copied_hb(tmp_path), good_harness(entrypoint=["python3", argument]))


def test_an_image_labelled_with_its_id_and_without_an_entrypoint():
    harness.check_image_config(inspect_of(), "baseline")
    harness.check_image_config(inspect_of(entrypoint=[]), "baseline")


@pytest.mark.parametrize("inspect,message", [
    (inspect_of(entrypoint=["docker-entrypoint.sh"]), "ENTRYPOINT of its own"),
    (inspect_of(labels={}), "label is None"),
    (inspect_of(labels={harness.HARNESS_LABEL: "other"}), "label is 'other'"),
    (inspect_of(labels=None) + inspect_of(), "no image configuration"),
    ([{}], "no image configuration"),
])
def test_an_image_with_an_entrypoint_or_without_its_label_is_refused(inspect, message):
    with pytest.raises(ReleaseError, match=message):
        harness.check_image_config(inspect, "baseline")


@pytest.mark.parametrize("break_it,message", [
    (lambda h: (h / "config" / "agent.json").write_text("{}"), "is not the adapter's config"),
    (lambda h: (h / "config" / "extra.json").write_text("{}"), "is not the adapter's config"),
    (lambda h: shutil.rmtree(h / "config"), "is not the adapter's config"),
    (lambda h: (h / "agent.py").unlink(), "names /hb/agent.py"),
])
def test_an_image_without_them_is_refused(tmp_path, break_it, message):
    hb_dir = copied_hb(tmp_path)
    break_it(hb_dir)
    with pytest.raises(ReleaseError, match=message):
        harness.check_image_files(BASELINE, hb_dir, good_harness())


# ------------------------------------------------------------------ pins and the registry

def test_harness_pins():
    pins = {"harness": "baseline", "build_sha": "c" * 40, "image": IMAGE}
    assert harness.check_pins(pins) == pins


@pytest.mark.parametrize("pins", [
    {"harness": "baseline", "build_sha": "c" * 40},
    {"harness": "baseline", "build_sha": "c" * 40, "image": IMAGE, "extra": 1},
    {"harness": "baseline", "build_sha": "HEAD", "image": IMAGE},
    {"harness": "other", "build_sha": "c" * 40, "image": IMAGE},
    {"harness": "baseline", "build_sha": "c" * 40, "image": "ghcr.io/sigormatt/hb-harness-baseline:0.1.0"},
    {"harness": "baseline", "build_sha": "c" * 40, "image": "ghcr.io/sigormatt/hb-harness-baseline@sha256:" + "0" * 64},
    {"harness": "Base", "build_sha": "c" * 40, "image": IMAGE},
])
def test_malformed_harness_pins_are_refused(pins):
    with pytest.raises(ReleaseError):
        harness.check_pins(pins)


def entry(harness_id: str, **changes) -> dict:
    return {"id": harness_id, "version": "1", "image": f"ghcr.io/sigormatt/hb-harness-{harness_id}@sha256:" + "e" * 64,
            "config_digest": "sha256:" + "f" * 64, "entrypoint": ["/hb/x"], "homepage": "https://example.com",
            "build_commit": "c" * 40, **changes}


def test_pinning_adds_or_replaces_one_entry_and_keeps_the_others_sorted():
    first = harness.pin_registry(None, entry("zeta"))
    assert first == {"schema": "hb/registry/1", "harnesses": [entry("zeta")]}
    second = harness.pin_registry(first, entry("alpha"))
    assert [h["id"] for h in second["harnesses"]] == ["alpha", "zeta"]
    third = harness.pin_registry(second, entry("zeta", version="2"))
    assert third["harnesses"] == [entry("alpha"), entry("zeta", version="2")]


@pytest.mark.parametrize("bad", [[], {"schema": "hb/registry/2", "harnesses": []}, {"schema": "hb/registry/1"},
                                 {"schema": "hb/registry/1", "harnesses": ["x"]}])
def test_pinning_refuses_a_malformed_registry(bad):
    with pytest.raises(ReleaseError, match="registry"):
        harness.pin_registry(bad, entry("x"))


def test_verify_commands():
    assert harness.verify_commands(entry("baseline")) == [
        ["docker", "pull", "--quiet", entry("baseline")["image"]],
        ["gh", "attestation", "verify", "oci://" + entry("baseline")["image"], "--repo", "SigorMatt/harness-bench",
         "--signer-workflow", "SigorMatt/harness-bench/.github/workflows/harness.yml",
         "--source-digest", "c" * 40, "--source-ref", "refs/heads/main", "--deny-self-hosted-runners"],
    ]


# ------------------------------------------------------------------ the command line

def test_inputs_and_names(repo):
    proc = cli(repo, "inputs", "--harness", "baseline")
    assert proc.returncode == 0, proc.stderr
    names = cli(repo, "names", "--harness", "baseline", "--build-sha", "c" * 40, stdlib_only=True)
    assert names.returncode == 0, names.stderr
    assert names.stdout.splitlines() == ["id=baseline", "version=0.1.0", "dir=harnesses/baseline",
                                         "image=ghcr.io/sigormatt/hb-harness-baseline", "tag=0.1.0-" + "c" * 12]
    assert cli(repo, "names", "--harness", "baseline", "--build-sha", "HEAD").returncode == 1
    assert cli(repo, "inputs", "--harness", "missing").returncode == 1
    inputs = cli(repo, "inputs", "--harness", "baseline", stdlib_only=True)  # the check job installs jsonschema
    assert inputs.returncode != 0 and "jsonschema" in inputs.stderr


def inspect_file(tmp_path: Path, **changes) -> Path:
    path = tmp_path / "inspect.json"
    path.write_text(json.dumps(inspect_of(**changes)))
    return path


def test_check_image_and_pins_need_only_the_standard_library(repo, tmp_path):
    proc = cli(repo, "check-image", "--harness", "baseline", "--copied", str(copied_hb(tmp_path)),
               "--inspect", str(inspect_file(tmp_path)), stdlib_only=True)
    assert proc.returncode == 0, proc.stderr
    proc = cli(repo, "check-image", "--harness", "baseline", "--copied", str(copied_hb(tmp_path / "b")),
               "--inspect", str(inspect_file(tmp_path, entrypoint=["x"])))
    assert proc.returncode == 1 and "ENTRYPOINT" in proc.stderr
    out = tmp_path / "harness-pins.json"
    proc = cli(repo, "pins", "--harness", "baseline", "--build-sha", "c" * 40, "--image", IMAGE, "--out", str(out),
               stdlib_only=True)
    assert proc.returncode == 0, proc.stderr
    assert harness.read_pins_file(out) == {"harness": "baseline", "build_sha": "c" * 40, "image": IMAGE}
    out.unlink()
    proc = cli(repo, "pins", "--harness", "baseline", "--build-sha", "c" * 40, "--image", "x", "--out", str(out))
    assert proc.returncode == 1 and not out.exists()


def test_pin_computes_the_entry_from_the_build_commit(repo):
    build = git(repo, "rev-parse", "HEAD")
    (repo / "harnesses" / "baseline" / "config" / "untracked.json").write_text("{}")  # not committed: not in it
    entry_now = pinned(repo)
    assert entry_now == {"id": "baseline", "version": "0.1.0", "image": IMAGE,
                         "config_digest": tree_digest(BASELINE / "config"), "entrypoint": ["python3", "/hb/agent.py"],
                         "homepage": good_harness()["homepage"], "build_commit": build}
    from hb.schemas import validate
    validate("registry", registry(repo))
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 0, proc.stderr
    assert f"the entry is what its build commit {build} says" in proc.stdout


def test_pinning_again_replaces_the_entry(repo):
    pinned(repo)
    assert pinned(repo, image=OTHER_IMAGE)["image"] == OTHER_IMAGE
    assert len(registry(repo)["harnesses"]) == 1


def test_pin_refuses_an_adapter_that_changed_since_the_build(repo):
    pins = pins_file(repo)
    (repo / "harnesses" / "baseline" / "config" / "agent.json").write_text("{}")
    git(repo, "commit", "--quiet", "--all", "--message", "change the config")
    proc = cli(repo, "pin", str(pins))
    assert proc.returncode == 1 and "has changed since the build commit" in proc.stderr
    assert not (repo / "registry.json").exists()


def test_pin_refuses_uncommitted_changes(repo):
    (repo / "harnesses" / "baseline" / "agent.py").write_text("# changed\n")
    proc = cli(repo, "pin", str(pins_file(repo)))
    assert proc.returncode == 1 and "uncommitted changes" in proc.stderr


@pytest.mark.parametrize("change,message", [
    (lambda e: e.pop("build_commit"), "has no build_commit"),
    (lambda e: e.update(version="9.9.9"), "version"),
    (lambda e: e.update(config_digest="sha256:" + "0" * 63 + "1"), "config_digest"),
    (lambda e: e.update(entrypoint=["/hb/other"]), "entrypoint"),
    (lambda e: e.update(image="ghcr.io/sigormatt/hb-harness-other@sha256:" + "a" * 64), "hb-harness-baseline"),
    (lambda e: e.update(extra="x"), "extra"),
])
def test_verify_refuses_an_entry_its_build_commit_does_not_back(repo, change, message):
    pinned(repo)
    document = registry(repo)
    change(document["harnesses"][0])
    (repo / "registry.json").write_text(json.dumps(document))
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 1 and message in proc.stderr, proc.stderr


def test_verify_takes_ids(repo):
    pinned(repo)
    assert cli(repo, "verify", "--offline", "baseline").returncode == 0
    twice = cli(repo, "verify", "--offline", "baseline", "baseline")
    assert twice.returncode == 0 and twice.stdout.count("baseline:") == 1  # each id checked once
    proc = cli(repo, "verify", "--offline", "baseline", "missing")
    assert proc.returncode == 1 and "no entry for missing" in proc.stderr
    (repo / "registry.json").unlink()
    proc = cli(repo, "verify", "--offline")
    assert proc.returncode == 1 and "lists no harness" in proc.stderr


def fake_tools(tmp_path: Path, config_dir: Path, inspect: list | None = None, gh_exit: int = 0,
               symlink: bool = False) -> tuple[dict, Path]:
    """PATH with recording stand-ins for docker and gh (git stays reachable). Returns the env and the log.

    The docker stand-in answers `image inspect` with inspect, and `cp <container>:/hb/config DEST`
    by copying config_dir, as if the pulled image held it (or, with symlink, by leaving a symlink to it,
    as docker cp does for a symlink it does not follow).
    """
    bin_dir, log = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir(parents=True)
    (bin_dir / "git").symlink_to(shutil.which("git"))
    (tmp_path / "inspect.json").write_text(json.dumps(inspect_of() if inspect is None else inspect))
    record = ('n=0; for f in "$DOCKER_CONFIG"/* "$DOCKER_CONFIG"/.[!.]*; do [ -e "$f" ] && n=$((n + 1)); done\n'
              'printf "%s|%s|%s\\n" "${0##*/}" "$*" "$n" >> ' + str(log) + "\n")
    docker = ("#!/bin/sh\n" + record + 'case "$1" in\n'
              '  context) echo unix:///fake.sock ;;\n'
              f'  image) /bin/cat {tmp_path / "inspect.json"} ;;\n'
              '  create) echo fake-container ;;\n'
              '  cp) for dest; do :; done; ' + (f'/bin/ln -s {config_dir} "$dest"' if symlink
                                                 else f'/bin/cp -r {config_dir} "$dest"') + ' ;;\n'
              'esac\nexit 0\n')
    tools = {"gh": f"#!/bin/sh\n{record}exit {gh_exit}\n", "docker": docker}
    for name, script in tools.items():
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)
    env = {k: v for k, v in GIT_ENV.items() if k != "DOCKER_HOST"}
    env["PATH"] = str(bin_dir)
    return env, log


def calls(log: Path) -> list[list[str]]:
    return [line.split("|") for line in log.read_text().splitlines()]


def test_verify_online_pulls_anonymously_checks_provenance_and_the_image(repo, tmp_path):
    entry_now = pinned(repo)
    env, log = fake_tools(tmp_path, BASELINE / "config")
    proc = cli(repo, "verify", env=env, stdlib_only=True)
    assert proc.returncode == 0, proc.stderr
    made = [(tool, args) for tool, args, _ in calls(log) if not args.startswith("context")]
    assert made == [(command[0], " ".join(command[1:])) for command in harness.verify_commands(entry_now)] + [
        ("docker", f"image inspect {IMAGE}"), ("docker", f"create {IMAGE} /hb-never-run"),
        ("docker", "cp --follow-link fake-container:/hb/config " + made[4][1].split()[-1]),
        ("docker", "rm fake-container")]
    # An empty Docker configuration for every call after the one that reads the current context.
    assert {entries for _, args, entries in calls(log) if not args.startswith("context")} == {"0"}
    assert "holds config_digest's /hb/config" in proc.stdout


@pytest.mark.parametrize("setup,message", [
    (lambda tmp: {"gh_exit": 1}, "failed (exit status 1): gh attestation verify"),
    (lambda tmp: {"inspect": inspect_of(labels={harness.HARNESS_LABEL: "other"})}, "label is 'other'"),
    (lambda tmp: {"inspect": inspect_of(entrypoint=["/bin/sh"])}, "ENTRYPOINT of its own"),
    (lambda tmp: {"config_dir": tmp / "other-config"}, "not the entry's config_digest"),
    (lambda tmp: {"symlink": True}, "is a symlink"),  # even to the right files: they would be this machine's
])
def test_verify_online_refuses_an_image_that_does_not_match(repo, tmp_path, setup, message):
    pinned(repo)
    other = tmp_path / "other-config"
    shutil.copytree(BASELINE / "config", other)
    (other / "agent.json").write_text("{}")
    options = {"config_dir": BASELINE / "config", **setup(tmp_path)}
    env, _ = fake_tools(tmp_path / "tools", **options)
    proc = cli(repo, "verify", env=env)
    assert proc.returncode == 1 and message in proc.stderr, proc.stderr


def test_entry_at_reads_committed_bytes_only(repo, tmp_path):
    build = git(repo, "rev-parse", "HEAD")
    (repo / "harnesses" / "baseline" / "config" / "agent.json").write_text("{}")  # uncommitted
    result = harness.entry_at(Git(repo), build, "baseline", IMAGE, tmp_path / "scratch")
    assert result["config_digest"] == tree_digest(BASELINE / "config")


def test_check_image_compares_with_the_committed_files_not_the_checkout(repo, tmp_path):
    # The image is built from the checkout; what it must hold is what the build commit says.
    (repo / "harnesses" / "baseline" / "config" / "agent.json").write_text("{}")  # not committed
    image = tmp_path / "hb"
    shutil.copytree(repo / "harnesses" / "baseline" / "config", image / "config")
    shutil.copy(BASELINE / "agent.py", image / "agent.py")
    proc = cli(repo, "check-image", "--harness", "baseline", "--copied", str(image),
               "--inspect", str(inspect_file(tmp_path)))
    assert proc.returncode == 1 and "is not the adapter's config" in proc.stderr


def test_names_refuses_a_gitattributes_file(repo):
    (repo / ".gitattributes").write_text("*.json text eol=crlf\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "--quiet", "--message", "attributes")
    proc = cli(repo, "names", "--harness", "baseline", "--build-sha", "c" * 40)
    assert proc.returncode == 1 and ".gitattributes" in proc.stderr


def test_an_entry_stays_tied_to_its_build_commit(repo):
    # Later changes to the adapter on main do not change what the entry was built from.
    pinned(repo)
    (repo / "harnesses" / "baseline" / "config" / "agent.json").write_text("{}")
    git(repo, "commit", "--quiet", "--all", "--message", "a later change")
    proc = cli(repo, "verify", "--offline", stdlib_only=True)
    assert proc.returncode == 0, proc.stderr
