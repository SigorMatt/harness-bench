"""Harness images: adapters built from this repository and pinned in the registry (spec §6.4).

harnesses/<id>/ holds one adapter: harness.json (hb/harness/1), a Dockerfile
whose build context is that directory, and config/, the stock configuration
the image holds at /hb/config. .github/workflows/harness.yml builds the image
from one commit of main, the build commit, in a job that cannot push or
attest, labelled with its harness id. The job that can then checks the loaded
image against the build commit's committed files (check_image_files,
check_image_config), pushes it to GHCR as ghcr.io/sigormatt/hb-harness-<id>,
attests it and uploads harness-pins.json.

A maintainer then adds the registry entry in a pull request
(`scripts/harness.py pin`). Every field but the image comes from the build
commit's committed files: version, entrypoint and homepage from harness.json,
config_digest the tree digest of config/, and build_commit itself. So
check_entry() can recompute an entry from its build commit, verify_commands()
check the image's provenance against the same commit, and check_pulled_image()
checks the image itself: its label, no ENTRYPOINT, and /hb/config whose digest
is config_digest.

Everything here but check_inputs() uses the standard library only, so the
privileged jobs run it without anything from PyPI.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

from .manifest import VerificationError, list_files, tree_digest
from .release import (
    BUILD_REF, GIT_SHA, IMAGE_PREFIX, REPOSITORY, SLUG, Git, ReleaseError, check_image, load_json,
)

HARNESSES_DIR = "harnesses"
HARNESS_LABEL = "io.github.sigormatt.harness-bench.harness"  # harness.yml labels each image with its harness id
HARNESS_SCHEMA = "hb/harness/1"
REGISTRY_SCHEMA = "hb/registry/1"
HARNESS_WORKFLOW = f"{REPOSITORY}/.github/workflows/harness.yml"
HARNESS_REPOSITORY_PREFIX = IMAGE_PREFIX + "hb-harness-"
HARNESS_KEYS = ("schema", "id", "version", "homepage", "entrypoint")
PINS_KEYS = ("harness", "build_sha", "image")
IMAGE_HB = "/hb/"  # where an adapter image keeps its own files; config/ is /hb/config

VERSION = re.compile(r"[A-Za-z0-9._+-]{1,64}")
HOMEPAGE = re.compile(r"https://[\x21-\x7e]+")
ARGUMENT = re.compile(r"[\x21-\x7e]+")


def check_harness_id(harness_id) -> str:
    if not isinstance(harness_id, str) or not SLUG.fullmatch(harness_id):
        raise ReleaseError(f"harness id {harness_id!r} is not a slug")
    return harness_id


def adapter_path(harness_id: str) -> str:
    """The adapter's directory, relative to the repository root."""
    return f"{HARNESSES_DIR}/{check_harness_id(harness_id)}"


def harness_repository(harness_id: str) -> str:
    return HARNESS_REPOSITORY_PREFIX + check_harness_id(harness_id)


def image_tag(version: str, build_sha: str) -> str:
    """A tag for one build: the harness version, which may hold '+', and the build commit."""
    tag = re.sub(r"[^A-Za-z0-9_.-]", "_", version) + "-" + build_sha[:12]
    return tag if tag[0].isalnum() or tag[0] == "_" else "v" + tag  # a tag cannot start with '.' or '-'


def check_harness(document, harness_id: str) -> dict:
    """harness.json, checked with the standard library (the same rules as schemas/harness.schema.json)."""
    if not isinstance(document, dict) or sorted(document) != sorted(HARNESS_KEYS):
        raise ReleaseError(f"harness.json: expected exactly the keys {', '.join(HARNESS_KEYS)}")
    if document["schema"] != HARNESS_SCHEMA:
        raise ReleaseError(f"harness.json: schema is not {HARNESS_SCHEMA}")
    if document["id"] != harness_id:
        raise ReleaseError(f"harness.json: id {document['id']!r} is not the directory's name {harness_id!r}")
    if not isinstance(document["version"], str) or not VERSION.fullmatch(document["version"]):
        raise ReleaseError("harness.json: version must be 1 to 64 of A-Z a-z 0-9 . _ + -")
    if not isinstance(document["homepage"], str) or not HOMEPAGE.fullmatch(document["homepage"]):
        raise ReleaseError("harness.json: homepage must be an https URL")
    entrypoint = document["entrypoint"]
    if not isinstance(entrypoint, list) or not entrypoint or not all(
            isinstance(a, str) and ARGUMENT.fullmatch(a) for a in entrypoint):
        raise ReleaseError("harness.json: entrypoint must be a list of printable arguments without spaces")
    return document


def load_adapter(adapter_dir: Path, harness_id: str) -> dict:
    """Check an adapter directory and return its harness.json. Raises ReleaseError."""
    adapter_dir = Path(adapter_dir)
    if not adapter_dir.is_dir():
        raise ReleaseError(f"{adapter_path(harness_id)} does not exist")
    harness = check_harness(load_json(adapter_dir / "harness.json", "harness.json"), harness_id)
    if not (adapter_dir / "Dockerfile").is_file():
        raise ReleaseError(f"{adapter_path(harness_id)}/Dockerfile is missing")
    config_digest(adapter_dir)  # config/ exists and holds regular files only
    return harness


def config_digest(adapter_dir: Path) -> str:
    """The tree digest of the adapter's config/: the registry's config_digest."""
    config = Path(adapter_dir) / "config"
    try:
        if not config.is_dir() or not list_files(config, canonical_paths=False):
            raise ReleaseError(f"{config} is missing or empty: an adapter keeps its stock configuration there")
        return tree_digest(config)
    except (VerificationError, ValueError) as exc:
        raise ReleaseError(f"{config}: {exc}") from None


def check_inputs(adapter_dir: Path, harness_id: str) -> dict:
    """load_adapter() plus the harness.json schema. Needs jsonschema."""
    from jsonschema import ValidationError

    from .schemas import validate

    harness = load_adapter(adapter_dir, harness_id)
    try:
        validate("harness", harness)
    except ValidationError as exc:
        raise ReleaseError(f"harness.json: {exc.message}") from None
    return harness


def check_image_config(inspect, harness_id: str) -> None:
    """`docker image inspect` of an adapter image: labelled with its harness id, and no ENTRYPOINT of its own.

    The registry's entrypoint is the container's command (hb.profile), so an image
    ENTRYPOINT would wrap it and something other than the registered command would run.
    """
    image = inspect[0] if isinstance(inspect, list) and len(inspect) == 1 else inspect
    config = image.get("Config") if isinstance(image, dict) else None
    if not isinstance(config, dict):
        raise ReleaseError("docker image inspect gave no image configuration")
    if config.get("Entrypoint"):
        raise ReleaseError(f"the image has an ENTRYPOINT of its own ({config['Entrypoint']}): harness.json's "
                           "entrypoint must be the whole command")
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    if labels.get(HARNESS_LABEL) != harness_id:
        raise ReleaseError(f"the image's {HARNESS_LABEL} label is {labels.get(HARNESS_LABEL)!r}, not {harness_id!r}")


def _regular_file_under(root: Path, rel: str) -> bool:
    """root/rel is a regular file reached through real directories: no component is a symlink."""
    path = Path(root)
    parts = PurePosixPath(rel).parts
    for depth, part in enumerate(parts, 1):
        path = path / part
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            return False
        if not (stat.S_ISREG(mode) if depth == len(parts) else stat.S_ISDIR(mode)):
            return False
    return bool(parts)


def _refuse_symlinked(*paths: Path) -> None:
    # docker cp copies a symlink as a symlink, and walking it on the host would read the host's files.
    for path in paths:
        if Path(path).is_symlink():
            raise ReleaseError(f"{path.name} copied out of the image is a symlink")


def check_image_files(adapter_dir: Path, copied_hb: Path, harness: dict) -> None:
    """The /hb directory copied out of a built image holds config/ exactly and every /hb file the entrypoint names.

    copied_hb must be copied with `docker cp --follow-link`, which resolves a symlink at /hb inside the
    image; any symlink left in the copy is refused, so nothing on the host is ever read in its place.
    """
    adapter_dir, copied_hb = Path(adapter_dir), Path(copied_hb)
    _refuse_symlinked(copied_hb, copied_hb / "config")
    try:
        wanted = list_files(adapter_dir / "config", canonical_paths=False)
        found = list_files(copied_hb / "config", canonical_paths=False) if (copied_hb / "config").is_dir() else []
    except (VerificationError, ValueError) as exc:
        raise ReleaseError(f"the image's {IMAGE_HB}config: {exc}") from None
    if found != wanted:
        raise ReleaseError(f"the image's {IMAGE_HB}config is not the adapter's config/")
    for argument in harness["entrypoint"]:
        if argument.startswith(IMAGE_HB) and not _regular_file_under(copied_hb, argument[len(IMAGE_HB):]):
            raise ReleaseError(f"the entrypoint names {argument}, which the image does not have as a regular file")


def check_pins(pins) -> dict:
    """harness-pins.json, which harness.yml writes for the maintainer. Raises ReleaseError."""
    if not isinstance(pins, dict) or sorted(pins) != sorted(PINS_KEYS):
        raise ReleaseError(f"harness pins: expected exactly the keys {', '.join(PINS_KEYS)}")
    check_harness_id(pins["harness"])
    if not isinstance(pins["build_sha"], str) or not GIT_SHA.fullmatch(pins["build_sha"]):
        raise ReleaseError("harness pins: build_sha is not a full commit sha")
    check_image(pins["image"], harness_repository(pins["harness"]))
    return pins


def read_pins_file(path: Path) -> dict:
    return check_pins(load_json(Path(path), Path(path).name))


def registry_entry(harness: dict, image: str, digest: str, build_sha: str) -> dict:
    return {"id": harness["id"], "version": harness["version"], "image": image, "config_digest": digest,
            "entrypoint": harness["entrypoint"], "homepage": harness["homepage"], "build_commit": build_sha}


def entry_at(git: Git, commit: str, harness_id: str, image: str, scratch: Path) -> dict:
    """The registry entry for image, built from harness_id's adapter at commit. scratch must be a new directory."""
    sha = git.out("rev-parse", "--verify", f"{commit}^{{commit}}")
    git.export(sha, [adapter_path(harness_id)], Path(scratch))
    adapter = Path(scratch) / adapter_path(harness_id)
    return registry_entry(load_adapter(adapter, harness_id), image, config_digest(adapter), sha)


def adapter_tree(git: Git, commit: str, harness_id: str) -> str:
    """The git tree object of the adapter directory at commit: equal trees, equal files and modes."""
    return git.out("rev-parse", "--verify", f"{commit}:{adapter_path(harness_id)}")


def pin_registry(registry, entry: dict) -> dict:
    """The registry with entry added, or in place of the entry with its id; sorted by id."""
    if registry is None:
        registry = {"schema": REGISTRY_SCHEMA, "harnesses": []}
    if not isinstance(registry, dict) or registry.get("schema") != REGISTRY_SCHEMA or not isinstance(
            registry.get("harnesses"), list) or not all(isinstance(h, dict) for h in registry["harnesses"]):
        raise ReleaseError(f"registry.json is not an {REGISTRY_SCHEMA} document")
    others = [h for h in registry["harnesses"] if h.get("id") != entry["id"]]
    return {**registry, "harnesses": sorted(others + [entry], key=lambda h: str(h.get("id")))}


def check_entry(git: Git, entry: dict, scratch: Path) -> None:
    """Every field of a registry entry but the image is what its build commit says. Raises ReleaseError."""
    harness_id = check_harness_id(entry.get("id"))
    build = entry.get("build_commit")
    if not isinstance(build, str) or not GIT_SHA.fullmatch(build):
        raise ReleaseError(f"registry entry {harness_id!r} has no build_commit: it was not built by harness.yml")
    check_image(entry.get("image"), harness_repository(harness_id))
    expected = entry_at(git, build, harness_id, entry["image"], scratch)
    differ = sorted(k for k in set(expected) | set(entry) if expected.get(k) != entry.get(k))
    if differ:
        raise ReleaseError(f"registry entry {harness_id!r} is not what its build commit says: {', '.join(differ)}")


def verify_commands(entry: dict) -> list[list[str]]:
    """What proves an entry's image is public and was built by harness.yml from its build commit on main."""
    return [
        ["docker", "pull", "--quiet", entry["image"]],
        ["gh", "attestation", "verify", f"oci://{entry['image']}",
         "--repo", REPOSITORY,
         "--signer-workflow", HARNESS_WORKFLOW,
         "--source-digest", entry["build_commit"],
         "--source-ref", BUILD_REF,
         "--deny-self-hosted-runners"],
    ]


def check_pulled_image(docker, entry: dict, scratch: Path) -> None:
    """The entry's image, pulled: labelled with its id, no ENTRYPOINT, and /hb/config whose digest is config_digest.

    docker runs commands (hb.release.AnonymousDocker). scratch must be a new directory.
    """
    try:
        inspect = json.loads(docker.run(["docker", "image", "inspect", entry["image"]], capture=True))
    except ValueError:
        raise ReleaseError(f"docker image inspect {entry['image']} did not give JSON") from None
    check_image_config(inspect, entry["id"])
    container = docker.run(["docker", "create", entry["image"], "/hb-never-run"], capture=True).decode().strip()
    try:
        # --follow-link resolves a symlink at /hb/config inside the image, not on this machine.
        docker.run(["docker", "cp", "--follow-link", f"{container}:{IMAGE_HB}config", str(Path(scratch) / "config")])
    finally:
        docker.run(["docker", "rm", container], capture=True)
    _refuse_symlinked(Path(scratch) / "config")
    try:
        digest = tree_digest(Path(scratch) / "config")
    except (VerificationError, ValueError) as exc:
        raise ReleaseError(f"the image's {IMAGE_HB}config: {exc}") from None
    if digest != entry["config_digest"]:
        raise ReleaseError(f"{entry['image']}: its {IMAGE_HB}config digest is {digest}, not the entry's config_digest")
