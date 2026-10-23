"""The task base: the image every harness image starts from (spec §6.5).

docker/task-base.Dockerfile builds it: the toolkit's pinned Python 3.12 image,
git, and pytest with every wheel pinned by hash. .github/workflows/task-base.yml
builds it from one commit of main, the build commit, in a job that cannot push
or attest, and tries it under the harness container's profile. The job that
can push and attest builds nothing: it loads the image, checks it is the one
built, pushes it to GHCR as ghcr.io/sigormatt/hb-task-base, attests it and
uploads task-base-pins.json.

A maintainer then pins it in task-base.json, in a pull request
(`scripts/task_base.py pin`), which refuses unless the build files at main are
the ones the image was built from. verify_commands() is what proves the pinned
image is public and was built by task-base.yml from its build commit on main
(`scripts/task_base.py verify`).

Every harness image is built on the pinned task base: check_built_on()
requires an image's first layers to be exactly the task base's layers
(hb.harness, spec §6.4). The suite pins the same image in runtime.task_base,
which puts it in the cohort key (check_suite_task_base).

Standard library only, so the privileged jobs run it without anything from PyPI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .release import (
    BUILD_REF, DIGEST, GIT_SHA, REPOSITORY, SUITE_DIR, TASK_BASE_REPOSITORY, Git, ReleaseError, check_image,
    load_json, parse_json,
)

TASK_BASE_FILE = "task-base.json"   # the pin, at the repository root
TASK_BASE_SCHEMA = "hb/task-base/1"
TASK_BASE_WORKFLOW = f"{REPOSITORY}/.github/workflows/task-base.yml"
TASK_BASE_DOCKERFILE = "docker/task-base.Dockerfile"
BUILD_FILES = (TASK_BASE_DOCKERFILE, "docker/task-base-requirements.txt")  # what the image is built from
PINS_KEYS = ("build_sha", "image")
FILE_KEYS = ("schema", "image", "build_commit")
SUITE_FILE = f"{SUITE_DIR}/suite.json"
_SUITE_PIN = re.compile(r'("task_base"\s*:\s*)"[^"]*"')


def _check_build_sha(build_sha) -> str:
    if not isinstance(build_sha, str) or not GIT_SHA.fullmatch(build_sha):
        raise ReleaseError(f"{build_sha!r} is not a full commit sha")
    return build_sha


def image_tag(build_sha: str) -> str:
    """A tag for one build: the build commit's first twelve hex digits."""
    return _check_build_sha(build_sha)[:12]


def check_pins(pins) -> dict:
    """task-base-pins.json, which task-base.yml writes for the maintainer. Raises ReleaseError."""
    if not isinstance(pins, dict) or sorted(pins) != sorted(PINS_KEYS):
        raise ReleaseError(f"task base pins: expected exactly the keys {', '.join(PINS_KEYS)}")
    try:
        _check_build_sha(pins["build_sha"])
    except ReleaseError:
        raise ReleaseError("task base pins: build_sha is not a full commit sha") from None
    check_image(pins["image"], TASK_BASE_REPOSITORY)
    return pins


def read_pins_file(path: Path) -> dict:
    return check_pins(load_json(Path(path), Path(path).name))


def task_base_document(pins: dict) -> dict:
    """task-base.json for the image task-base.yml built (pins)."""
    return {"schema": TASK_BASE_SCHEMA, "image": pins["image"], "build_commit": pins["build_sha"]}


def check_task_base(document) -> dict:
    """task-base.json, checked with the standard library (the rules of schemas/task-base.schema.json)."""
    if not isinstance(document, dict) or sorted(document) != sorted(FILE_KEYS):
        raise ReleaseError(f"{TASK_BASE_FILE}: expected exactly the keys {', '.join(FILE_KEYS)}")
    if document["schema"] != TASK_BASE_SCHEMA:
        raise ReleaseError(f"{TASK_BASE_FILE}: schema is not {TASK_BASE_SCHEMA}")
    try:
        _check_build_sha(document["build_commit"])
        check_image(document["image"], TASK_BASE_REPOSITORY)
    except ReleaseError as exc:
        raise ReleaseError(f"{TASK_BASE_FILE}: {exc}") from None
    return document


def read_task_base(root: Path) -> dict:
    """task-base.json in a checkout. Raises ReleaseError if it is missing or malformed."""
    path = Path(root) / TASK_BASE_FILE
    if not path.is_file():
        raise ReleaseError(f"{TASK_BASE_FILE} is missing: no task base is pinned yet")
    return check_task_base(load_json(path, TASK_BASE_FILE))


def task_base_at(git: Git, commit: str) -> dict:
    """task-base.json as committed at commit."""
    return check_task_base(parse_json(git.text(commit, TASK_BASE_FILE), f"{TASK_BASE_FILE} at {commit}"))


def build_files_changed(git: Git, build: str, commit: str = "HEAD") -> list[str]:
    """The task base's build files that differ between the build commit and commit, or are missing at either.

    Compared as committed (mode, git object) pairs: COPY keeps a file's mode, so a mode is part of the image.
    """
    def objects(at: str) -> dict:
        return {path: (mode, obj) for mode, obj, path in git.tree(at, list(BUILD_FILES))}
    then, now = objects(build), objects(commit)
    return [path for path in BUILD_FILES if path not in then or path not in now or then[path] != now[path]]


def check_build_files_unchanged(git: Git, build: str, commit: str = "HEAD") -> None:
    """The build files at commit are exactly those at the build commit: the image is what they build."""
    changed = build_files_changed(git, build, commit)
    if changed:
        raise ReleaseError(f"{', '.join(changed)} changed since the task base's build commit {build}: "
                           "run task-base.yml again from main")


def verify_commands(image: str, build_commit: str) -> list[list[str]]:
    """What proves the task base is public and was built by task-base.yml from build_commit on main.

    Run as argument lists with an empty DOCKER_CONFIG (hb.release.AnonymousDocker), so the
    pull is anonymous and succeeds only for a public image. gh needs a GitHub login.
    """
    return [
        ["docker", "pull", "--quiet", image],
        ["gh", "attestation", "verify", f"oci://{image}",
         "--repo", REPOSITORY,
         "--signer-workflow", TASK_BASE_WORKFLOW,
         "--source-digest", build_commit,
         "--source-ref", BUILD_REF,
         "--deny-self-hosted-runners"],
    ]


def check_suite_task_base(suite, task_base: dict) -> None:
    """The suite pins the task base task-base.json pins (runtime.task_base). Raises ReleaseError."""
    runtime = suite.get("runtime") if isinstance(suite, dict) else None
    pinned = runtime.get("task_base") if isinstance(runtime, dict) else None
    if pinned != task_base["image"]:
        raise ReleaseError(f"suite.json runtime.task_base is {pinned!r}, not the task base {TASK_BASE_FILE} pins "
                           f"({task_base['image']})")


def pin_suite_text(text: str, task_base: dict) -> str:
    """suite.json's text with runtime.task_base set to the pinned image, and nothing else changed."""
    if len(_SUITE_PIN.findall(text)) != 1:
        raise ReleaseError(f'{SUITE_FILE} must hold "task_base" exactly once')
    pinned = _SUITE_PIN.sub(lambda match: match.group(1) + json.dumps(task_base["image"]), text)
    check_suite_task_base(parse_json(pinned, SUITE_FILE), task_base)  # the one it changed is runtime's
    return pinned


def image_layers(inspect, what: str = "the image") -> list[str]:
    """The layer digests of one `docker image inspect` result: RootFS.Layers, the image configuration's
    rootfs.diff_ids (digests of the uncompressed layers), the base's first. what names it in errors."""
    image = inspect[0] if isinstance(inspect, list) and len(inspect) == 1 else None
    rootfs = image.get("RootFS") if isinstance(image, dict) else None
    layers = rootfs.get("Layers") if isinstance(rootfs, dict) else None
    if not isinstance(layers, list) or not layers or not all(
            isinstance(layer, str) and DIGEST.fullmatch(layer) for layer in layers):
        raise ReleaseError(f"docker image inspect gave no layer digests for {what} (as exactly one image)")
    return layers


def pulled_as(inspect, image: str) -> bool:
    """A `docker image inspect` result of exactly one image that was pulled as image (repository@digest)."""
    found = inspect[0] if isinstance(inspect, list) and len(inspect) == 1 else None
    digests = found.get("RepoDigests") if isinstance(found, dict) else None
    return isinstance(digests, list) and image in digests


def check_built_on(image_inspect, base_inspect) -> None:
    """The image's first layers are exactly the task base's: it was built on the task base.

    Layer digests are digests of the layers' content, so the image starts from exactly
    the task base's file system; its own layers come on top, reviewed with its adapter.
    """
    image, base = image_layers(image_inspect, "the image"), image_layers(base_inspect, "the task base")
    if image[:len(base)] != base:
        differs = next((i for i, (mine, theirs) in enumerate(zip(image, base)) if mine != theirs), len(image))
        raise ReleaseError(f"the image is not built on the task base: its first layers are not the task base's "
                           f"layers (they differ from layer {differs + 1} of the task base's {len(base)})")

