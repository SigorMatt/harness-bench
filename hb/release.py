"""Releases: two images built from one commit, and one commit that pins them.

A release (spec §6.3) is:

- the toolkit image (hb/, schemas/ and the harness registry) and the suite
  image (the suite tree at /suite), built by .github/workflows/release.yml from
  one commit of main, the build commit, on a GitHub-hosted runner, pushed to
  GHCR and given build-provenance attestations;
- the release commit: a child of the build commit whose only change is the two
  image pins in bench.yml, pushed as the tag vMAJOR.MINOR.PATCH and never
  merged into main. A workflow's own token cannot change workflow files, so a
  maintainer makes it (`scripts/release.py pin`).

The release commit and the build commit differ only in bench.yml, which neither
image contains, so the code at the release commit is the code in its images.
check_release_commit() and verify_commands() check that before the tag is
pushed. release_entry_at() computes the release table entry (spec §3.4) from the
release commit's committed bytes alone.

Only check_inputs() needs jsonschema, and imports it itself: the privileged
release job runs the rest with the standard library only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from .identity import cohort_from_suite, cohort_id, entry_from_registry, entry_id
from .manifest import VerificationError, list_files, tree_digest
from .plan import PlanError, plan_run

REPOSITORY = "SigorMatt/harness-bench"
SIGNER_WORKFLOW = f"{REPOSITORY}/.github/workflows/bench.yml"
RELEASE_WORKFLOW = f"{REPOSITORY}/.github/workflows/release.yml"
BUILD_REF = "refs/heads/main"         # release.yml builds only from main
BENCH_WORKFLOW = ".github/workflows/bench.yml"
SUITE_DIR = "suites/py-maint"         # what the suite image holds at /suite
REGISTRY_FILE = "registry.json"       # what the toolkit image holds as its registry
IMAGE_PREFIX = "ghcr.io/sigormatt/"
TOOLKIT_REPOSITORY = IMAGE_PREFIX + "hb-toolkit"
SUITE_REPOSITORY_PREFIX = IMAGE_PREFIX + "hb-suite-"
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64
PINS_FILE_KEYS = ("version", "build_sha", "toolkit_image", "suite_image")
REGULAR_MODES = ("100644", "100755")

VERSION = re.compile(r"v(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")
GIT_SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_PIN_LINE = re.compile(r"^  (HB_TOOLKIT_IMAGE|HB_SUITE_IMAGE): (\S+)$", re.MULTILINE)
_PIN_KEYS = {"HB_TOOLKIT_IMAGE": "toolkit_image", "HB_SUITE_IMAGE": "suite_image"}
# Replace refs (refs/replace/*) would let a local object stand in for a commit or a blob.
_GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1"}


class ReleaseError(Exception):
    """The release cannot go ahead as it is."""


def check_version(version: str) -> str:
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ReleaseError(f"version {version!r} is not vMAJOR.MINOR.PATCH")
    return version


def suite_repository(suite_id: str) -> str:
    return SUITE_REPOSITORY_PREFIX + suite_id


def check_image(ref: str, repository: str) -> str:
    """ref must be repository@sha256:<64 hex>, and not the placeholder digest."""
    name, _, digest = ref.partition("@") if isinstance(ref, str) else ("", "", "")
    if name != repository or not DIGEST.fullmatch(digest):
        raise ReleaseError(f"{ref!r} is not {repository} pinned by digest")
    if digest == PLACEHOLDER_DIGEST:
        raise ReleaseError(f"{ref!r} is the placeholder digest, not a released image")
    return ref


def parse_json(text, what: str):
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ReleaseError(f"{what}: cannot be read as JSON ({exc})") from None


def load_json(path: Path, what: str):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"{what}: cannot be read ({exc})") from None
    return parse_json(text, what)


def suite_id_of(suite) -> str:
    """The suite's id, which names its image. Raises ReleaseError for anything but a slug."""
    suite_id = suite.get("id") if isinstance(suite, dict) else None
    if not isinstance(suite_id, str) or not SLUG.fullmatch(suite_id):
        raise ReleaseError("suite.json has no valid id")
    return suite_id


def check_task_paths(task_id: str, task_dir: Path, task: dict) -> None:
    """Every path of a task that a run reads is there, a directory for the repo and files for the rest.

    A missing instruction, probe or check fails the task job; a missing repo
    would not: hb.tree.materialize copies what it finds, so every rep would
    start with an empty workspace.
    """
    wanted = [(task["repo"], "directory")] + [(rel, "file") for rel in (
        task["instruction"], task["probe"]["program"], task["probe"]["inputs"],
        task["checks"]["target"], task["checks"]["preservation"])]
    for rel, kind in wanted:
        path = task_dir / rel
        if not (path.is_dir() if kind == "directory" else path.is_file()):
            raise ReleaseError(f"task {task_id}: {rel} is not a {kind} in the task directory")


# Names Windows reserves, as Python 3.13's ntpath.isreserved() has them (this
# package also runs on 3.12): control characters, wildcards, separators, colon
# and pipe; a trailing dot or space; and DOS device names, with any extension.
_WINDOWS_RESERVED_CHARS = frozenset({chr(i) for i in range(32)} | set('"*:<>?|/\\'))
_WINDOWS_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                                  | {f"{device}{n}" for device in ("COM", "LPT") for n in "123456789\u00b9\u00b2\u00b3"})


def name_problem(name: str) -> str | None:
    """Why Windows would not store this file or directory name as it is, or None."""
    if name[-1:] in (".", " "):
        return "it ends in a dot or a space"
    if _WINDOWS_RESERVED_CHARS.intersection(name):
        return 'it holds a control character or one of "*/:<>?\\|'
    if name.partition(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES:
        return "it is a device name on Windows"
    return None


def check_portable_paths(paths: list[str]) -> None:
    """Linux, macOS and Windows each store these paths as they are and keep them apart.

    No name that Windows reserves (name_problem), and no two paths, or a path
    and a directory, that differ only in case (one name on a case-insensitive
    file system). So `scripts/release.py entry` can rebuild the suite tree on
    a maintainer's machine running any of them.
    """
    seen = {}
    for path in paths:
        parts = path.split("/")
        for part in parts:
            problem = name_problem(part)
            if problem:
                raise ReleaseError(f"{path!r} is not a portable path: {problem}")
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            other = seen.setdefault(prefix.casefold(), prefix)
            if other != prefix:
                raise ReleaseError(f"{other!r} and {prefix!r} differ only in case")


def check_inputs(suite_dir: Path, registry_path: Path) -> dict:
    """Check the suite and registry a release would bake into its images.

    Returns the suite id, the registry's harness ids, and the suite tree digest.
    Raises ReleaseError, so a release with inputs no run could use stops before
    anything is built.
    """
    from jsonschema import ValidationError  # only here: see the module docstring

    from .schemas import validate

    suite_dir, registry_path = Path(suite_dir), Path(registry_path)
    if not (suite_dir / "suite.json").is_file():
        raise ReleaseError(f"{suite_dir}/suite.json is missing: the release suite is not in the repository")
    if not registry_path.is_file():
        raise ReleaseError(f"{registry_path} is missing: the release registry is not in the repository")
    ignore_files = sorted(name for name in os.listdir(suite_dir)
                          if name.casefold() in (".dockerignore", ".containerignore"))
    if ignore_files:
        # The suite directory is the suite image's build context. The classic Docker builder,
        # Podman and Buildah read these at its root (case-insensitively on macOS and Windows);
        # only BuildKit lets docker/suite.Dockerfile.dockerignore take precedence.
        raise ReleaseError(f"{suite_dir}: {', '.join(ignore_files)} at the suite's root would leave files out "
                           "of the suite image")
    suite = load_json(suite_dir / "suite.json", "suite.json")
    registry = load_json(registry_path, registry_path.name)
    try:
        validate("suite", suite)
        validate("registry", registry)
        plan = plan_run(suite_dir)  # task ids, every task.json, probe timeouts, fits in a hosted job
        for task_id in plan["tasks"]:
            task = load_json(suite_dir / "tasks" / task_id / "task.json", f"task {task_id}")
            validate("task", task)
            check_task_paths(task_id, suite_dir / "tasks" / task_id, task)
        digest = tree_digest(suite_dir)  # regular files only, printable paths
        check_portable_paths([f["path"] for f in list_files(suite_dir, canonical_paths=False)])
        harness_ids = [harness["id"] for harness in registry["harnesses"]]
        for harness_id in harness_ids:
            entry_from_registry(registry, harness_id)  # refuses duplicate ids
    except ValidationError as exc:
        raise ReleaseError(f"schema: {exc.message}") from None
    except (PlanError, VerificationError, ValueError) as exc:
        raise ReleaseError(str(exc)) from None
    return {"suite_id": suite_id_of(suite), "harness_ids": harness_ids, "suite_digest": digest}


def read_pins(workflow_text: str) -> dict:
    """The two image pins in bench.yml's env block: {"toolkit_image", "suite_image"}."""
    found = {}
    for match in _PIN_LINE.finditer(workflow_text):
        key = _PIN_KEYS[match.group(1)]
        if key in found:
            raise ReleaseError(f"bench.yml pins {match.group(1)} more than once")
        found[key] = match.group(2)
    missing = sorted(set(_PIN_KEYS.values()) - set(found))
    if missing:
        raise ReleaseError(f"bench.yml has no pin line for {', '.join(missing)}")
    return found


def pin_workflow(workflow_text: str, *, toolkit_image: str, suite_image: str) -> str:
    """bench.yml with its two pin lines set to these images and nothing else changed."""
    read_pins(workflow_text)  # exactly one line of each
    values = {"HB_TOOLKIT_IMAGE": toolkit_image, "HB_SUITE_IMAGE": suite_image}
    return _PIN_LINE.sub(lambda m: f"  {m.group(1)}: {values[m.group(1)]}", workflow_text)


def check_pins(pins) -> dict:
    """The content of release-pins.json, which release.yml writes for the maintainer. Raises ReleaseError."""
    if not isinstance(pins, dict) or sorted(pins) != sorted(PINS_FILE_KEYS):
        raise ReleaseError(f"release pins: expected exactly the keys {', '.join(PINS_FILE_KEYS)}")
    check_version(pins["version"])
    if not isinstance(pins["build_sha"], str) or not GIT_SHA.fullmatch(pins["build_sha"]):
        raise ReleaseError("release pins: build_sha is not a full commit sha")
    check_image(pins["toolkit_image"], TOOLKIT_REPOSITORY)
    suite_name = pins["suite_image"].partition("@")[0] if isinstance(pins["suite_image"], str) else ""
    if not suite_name.startswith(SUITE_REPOSITORY_PREFIX):
        raise ReleaseError(f"release pins: {pins['suite_image']!r} is not a suite image")
    check_image(pins["suite_image"], suite_name)
    return pins


def read_pins_file(path: Path) -> dict:
    return check_pins(load_json(Path(path), Path(path).name))


def release_entry(*, suite_dir: Path, registry: dict, toolkit_image: str, suite_image: str,
                  signer_sha: str) -> dict:
    """The release table entry (spec §3.4, schemas/release.schema.json) for one release commit."""
    suite = json.loads((Path(suite_dir) / "suite.json").read_text(encoding="utf-8"))
    cohort = cohort_from_suite(suite, tree_digest(Path(suite_dir)))
    return {
        "schema": "hb/release/1",
        "signer_workflow": SIGNER_WORKFLOW,
        "signer_sha": signer_sha,
        "toolkit_image": toolkit_image,
        "suite_image": suite_image,
        "cohort_ids": [cohort_id(cohort)],
        "entry_ids": sorted(entry_id(entry_from_registry(registry, h["id"])) for h in registry["harnesses"]),
        "task_ids": sorted(t["id"] for t in suite["tasks"]),
    }


# ------------------------------------------------------------------ git

class Git:
    """git in one repository, run as argument lists.

    The checks use plumbing with NUL-separated output (ls-tree -z, diff-tree -z
    --raw) and cat-file, which no quoting, submodule, filter or attribute
    setting changes, with replace refs off. Only is_clean() uses porcelain.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def raw(self, *args: str, stdin: bytes | None = None) -> bytes:
        try:
            proc = subprocess.run(["git", "-C", str(self.root), *args], input=stdin, capture_output=True,
                                  env={**os.environ, **_GIT_ENV}, check=False)
        except OSError as exc:
            raise ReleaseError(f"git: {exc}") from None
        if proc.returncode != 0:
            raise ReleaseError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
        return proc.stdout

    def out(self, *args: str) -> str:
        return self.raw(*args).decode(errors="replace").strip()

    def head(self) -> str:
        return self.out("rev-parse", "--verify", "HEAD^{commit}")

    def is_clean(self) -> bool:
        return self.out("status", "--porcelain", "--untracked-files=no") == ""

    def text(self, commit: str, path: str) -> str:
        """A committed file's content, as stored (no filters), decoded as UTF-8."""
        try:
            return self.raw("cat-file", "blob", f"{commit}:{path}").decode("utf-8")
        except UnicodeDecodeError:
            raise ReleaseError(f"{path} at {commit} is not UTF-8") from None

    def tree(self, commit: str, paths: list[str] = ()) -> list[tuple[str, str, str]]:
        """(mode, object, path) of every committed file under paths (all files when none)."""
        listing = self.raw("ls-tree", "-r", "-z", "--full-tree", commit, "--", *paths)
        entries = []
        for record in filter(None, listing.split(b"\0")):
            meta, _, path = record.partition(b"\t")
            mode, _, obj = meta.decode().split(" ")
            entries.append((mode, obj, path.decode("utf-8", errors="replace")))
        return entries

    def export(self, commit: str, paths: list[str], dest: Path) -> None:
        """Write the committed bytes of every file under paths into dest, a new directory.

        Read with cat-file, so no checkout filter, line-ending conversion or
        export attribute applies. Only regular files with portable paths are
        allowed, checked before anything is written, and dest must then hold
        exactly the committed paths, which a case-insensitive file system does
        not when two of them differ only in case.
        """
        entries = self.tree(commit, paths)
        for path in paths:
            if not any(p == path or p.startswith(path.rstrip("/") + "/") for _, _, p in entries):
                raise ReleaseError(f"{commit} has no {path}")
        for mode, _, path in entries:
            parts = PurePosixPath(path).parts
            if mode not in REGULAR_MODES or not parts or ".." in parts or PurePosixPath(path).is_absolute():
                raise ReleaseError(f"{path} at {commit} is not a regular file")
            problems = [problem for problem in map(name_problem, parts) if problem]
            if problems:
                # On Windows a backslash separates and a colon names a drive, so such a path could be
                # written outside dest, and a device name would receive the bytes.
                raise ReleaseError(f"{path!r} at {commit} is not a portable path: {problems[0]}")
        blobs = self.raw("cat-file", "--batch", stdin="".join(f"{obj}\n" for _, obj, _ in entries).encode())
        offset = 0
        for _, obj, path in entries:
            header_end = blobs.find(b"\n", offset)
            header = blobs[offset:header_end].decode(errors="replace").split(" ")
            if header_end < 0 or len(header) != 3 or header[:2] != [obj, "blob"] or not header[2].isdigit():
                raise ReleaseError(f"git cat-file could not read {path} at {commit}: {' '.join(header)}")
            size, start = int(header[2]), header_end + 1
            target = Path(dest).joinpath(*PurePosixPath(path).parts)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blobs[start:start + size])
            except OSError as exc:
                raise ReleaseError(f"{path} at {commit} cannot be written here ({exc})") from None
            offset = start + size + 1  # the blob is followed by a newline
        committed = {path for _, _, path in entries}
        written = {f["path"] for f in list_files(Path(dest), canonical_paths=False)}
        if written != committed:
            raise ReleaseError(f"this file system cannot hold apart the paths of {commit}, such as names that differ "
                               f"only in case: {sorted(written ^ committed)}")


def check_release_commit(git: Git, commit: str) -> dict:
    """Check that commit is a release commit.

    It must have one parent, the build commit, and change nothing but the two
    image pins in bench.yml (same file mode, no other path), to images in this
    release's repositories; and no .gitattributes may change how its files are
    checked out. Returns {"release_sha", "build_sha", "toolkit_image", "suite_image"}.
    """
    if git.out("rev-parse", "--is-shallow-repository") == "true":
        raise ReleaseError("this clone is shallow: run git fetch --unshallow first")
    parents = git.out("rev-list", "--parents", "-n", "1", commit).split()
    if len(parents) != 2:
        raise ReleaseError(f"{commit} must have exactly one parent, the build commit")
    commit, build = parents
    raw = git.raw("diff-tree", "-r", "-z", "--raw", "--no-renames", "--ignore-submodules=none", build, commit)
    fields = raw.split(b"\0")[:-1]
    changes = [(fields[i].decode(errors="replace"), fields[i + 1].decode(errors="replace"))
               for i in range(0, len(fields) - 1, 2)]
    if len(changes) != 1 or changes[0][1] != BENCH_WORKFLOW:
        raise ReleaseError(f"a release commit changes only {BENCH_WORKFLOW}; this one changes "
                           f"{[path for _, path in changes]}")
    meta = changes[0][0].split(" ")
    if len(meta) != 5 or meta[0] != ":" + meta[1] or meta[1] not in REGULAR_MODES or meta[4] != "M":
        raise ReleaseError(f"a release commit changes the content of {BENCH_WORKFLOW} only, not its mode")
    if any(PurePosixPath(path).name == ".gitattributes" for _, _, path in git.tree(commit)):
        raise ReleaseError("the release commit has a .gitattributes file, which can make a checkout differ "
                           "from the committed files the release entry is computed from")
    before, after = git.text(build, BENCH_WORKFLOW), git.text(commit, BENCH_WORKFLOW)
    pins = read_pins(after)
    if pin_workflow(before, **pins) != after:
        raise ReleaseError(f"a release commit changes only the two image pins in {BENCH_WORKFLOW}")
    suite_id = suite_id_of(parse_json(git.text(commit, f"{SUITE_DIR}/suite.json"), f"{SUITE_DIR}/suite.json"))
    check_image(pins["toolkit_image"], TOOLKIT_REPOSITORY)
    check_image(pins["suite_image"], suite_repository(suite_id))
    return {"release_sha": commit, "build_sha": build, **pins}


def verify_commands(pins: dict) -> list[list[str]]:
    """What proves the pinned images are public and were built by release.yml from the build commit.

    Run as argument lists with an empty DOCKER_CONFIG, so the pull is anonymous
    and succeeds only for a public image. gh needs the maintainer's GitHub login.
    """
    commands = []
    for ref in (pins["toolkit_image"], pins["suite_image"]):
        commands.append(["docker", "pull", "--quiet", ref])
        commands.append([
            "gh", "attestation", "verify", f"oci://{ref}",
            "--repo", REPOSITORY,
            "--signer-workflow", RELEASE_WORKFLOW,
            "--source-digest", pins["build_sha"],
            "--source-ref", BUILD_REF,
            "--deny-self-hosted-runners",
        ])
    return commands


def release_entry_at(git: Git, commit: str, scratch: Path) -> dict:
    """The release table entry for a release commit, from its committed bytes only."""
    pins = check_release_commit(git, commit)
    git.export(pins["release_sha"], [SUITE_DIR, REGISTRY_FILE], Path(scratch))
    registry = load_json(Path(scratch) / REGISTRY_FILE, REGISTRY_FILE)
    try:
        return release_entry(suite_dir=Path(scratch) / SUITE_DIR, registry=registry,
                             toolkit_image=pins["toolkit_image"], suite_image=pins["suite_image"],
                             signer_sha=pins["release_sha"])
    except (KeyError, TypeError, ValueError, VerificationError) as exc:
        raise ReleaseError(f"the suite or registry at {commit} cannot give a release entry ({exc!r})") from None
