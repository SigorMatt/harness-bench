"""Bundle layout, hashing, deterministic packing and verification.

A task bundle has a fixed layout: task-manifest.json at the root plus exactly
five files per rep. Because the layout is fixed, verification is a set and
hash comparison -- there is nothing to interpret.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import tarfile
from pathlib import Path

from .canonical import content_id

MANIFEST_NAME = "task-manifest.json"
TASK_MANIFEST_SCHEMA = "hb/task-manifest/1"
REP_FILES = ("gateway.jsonl", "grade.json", "harness.log", "lifecycle.json", "workspace.patch")

_RELPATH = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*")
_CHUNK = 1 << 20


class VerificationError(Exception):
    """A bundle does not match its manifest or the fixed layout."""


def is_canonical_relpath(path: str) -> bool:
    return len(path) <= 255 and _RELPATH.fullmatch(path) is not None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def list_files(root: Path, exclude: frozenset[str] = frozenset(), canonical_paths: bool = True) -> list[dict]:
    """Return [{path, sha256, size}] for every file under root, sorted by path.

    Only regular files are allowed; anything else (symlink, socket, device)
    raises. `exclude` names root-relative paths to leave out. Bundles use
    canonical_paths=True; repository trees (which may hold dotfiles) use False.
    """
    root = Path(root)
    entries = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in list(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                rel = Path(dirpath, name).relative_to(root).as_posix()
                raise VerificationError(f"not a regular file: {rel}")
        for name in sorted(filenames):
            full = Path(dirpath, name)
            rel = full.relative_to(root).as_posix()
            if rel in exclude:
                continue
            mode = os.lstat(full).st_mode
            if not stat.S_ISREG(mode):
                raise VerificationError(f"not a regular file: {rel}")
            if canonical_paths and not is_canonical_relpath(rel):
                raise VerificationError(f"non-canonical path: {rel!r}")
            entries.append({"path": rel, "sha256": sha256_file(full), "size": os.lstat(full).st_size})
    entries.sort(key=lambda e: e["path"])
    return entries


def tree_digest(root: Path) -> str:
    """Digest of a whole tree (used for the suite): content_id of its file list.

    Paths must be printable ASCII (content_id enforces it); dotfiles are fine.
    """
    return content_id(list_files(root, canonical_paths=False))


def expected_paths(reps: int) -> list[str]:
    return sorted(f"reps/{rep}/{name}" for rep in range(1, reps + 1) for name in REP_FILES)


def build_task_manifest(root: Path, *, run: dict, task_id: str, reps: int) -> dict:
    files = list_files(root, exclude=frozenset({MANIFEST_NAME}))
    _check_layout([f["path"] for f in files], reps)
    return {"schema": TASK_MANIFEST_SCHEMA, "run": run, "task_id": task_id, "reps": reps, "files": files}


def verify_task_bundle(root: Path, manifest: dict) -> None:
    """Raise VerificationError unless the tree under root matches manifest exactly."""
    actual = list_files(root, exclude=frozenset({MANIFEST_NAME}))
    _check_layout([f["path"] for f in actual], manifest["reps"])
    declared = {f["path"]: f for f in manifest["files"]}
    if [f["path"] for f in manifest["files"]] != sorted(declared):
        raise VerificationError("manifest file list is not sorted or has duplicates")
    mismatched = [f["path"] for f in actual if declared.get(f["path"]) != f]
    missing = sorted(set(declared) - {f["path"] for f in actual})
    if mismatched or missing:
        raise VerificationError(f"files differ from manifest: {sorted(mismatched + missing)}")


def _check_layout(paths: list[str], reps: int) -> None:
    expected = expected_paths(reps)
    if paths != expected:
        missing = sorted(set(expected) - set(paths))
        unexpected = sorted(set(paths) - set(expected))
        raise VerificationError(f"layout differs: missing={missing} unexpected={unexpected}")


def pack_bundle(root: Path, tar_path: Path) -> str:
    """Write a deterministic ustar archive of root; return its sha256.

    Same content always gives the same bytes: sorted members, mtime 0,
    uid/gid 0, mode 0644, no directory entries.
    """
    with tarfile.open(tar_path, "w", format=tarfile.USTAR_FORMAT) as tar:
        for entry in list_files(root):
            info = tarfile.TarInfo(entry["path"])
            info.size = entry["size"]
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            data = Path(root, entry["path"]).read_bytes()
            tar.addfile(info, io.BytesIO(data))
    return sha256_file(tar_path)


def _regular_only(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo:
    member = tarfile.data_filter(member, dest)  # stdlib: rejects absolute and escaping paths
    if not member.isfile():  # pack_bundle never writes directory entries either
        raise VerificationError(f"archive member is not a regular file: {member.name}")
    return member


def extract_bundle(tar_path: Path, dest: Path) -> None:
    """Extract an archive into an empty directory, accepting regular-file members only."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise VerificationError("extraction directory must be empty")
    with tarfile.open(tar_path, "r:") as tar:
        tar.extractall(dest, filter=_regular_only)
