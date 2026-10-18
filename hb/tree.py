"""Normalising a repository tree before it is diffed or graded.

The harness controls everything in its workspace, including `.git` (whose
config can name programs that git runs). So the toolkit never runs git inside
the workspace: it copies regular files only, skips `.git` at any depth, and
diffs the copy against the pristine tree from outside any repository.
Before grading, protected paths are made identical to pristine, so edits to
them cannot reach the verdict.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path

from .manifest import list_files

EXCLUDED_NAMES = frozenset({".git"})


def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a path glob. '**/' matches zero or more directories, a trailing
    '**' matches anything, '*' and '?' stay within one path component."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def matcher(patterns: list[str]):
    compiled = [glob_to_regex(p) for p in patterns]
    return lambda rel: any(rx.fullmatch(rel) for rx in compiled)


def materialize(src: Path, dst: Path, ignore: list[str] = ()) -> list[str]:
    """Copy regular files from src into a new dst, normalising modes to 0644/0755.

    Skips `.git` at any depth and files matching the `ignore` globs (caches and
    other leftovers that are not edits). Returns the sorted relative paths of
    entries dropped because they are not regular files (symlinks, devices, ...).
    """
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=False)
    skip = matcher(list(ignore))
    dropped = []
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        rel_dir = Path(dirpath).relative_to(src)
        for name in sorted(dirnames):
            if name in EXCLUDED_NAMES:
                dirnames.remove(name)
            elif os.path.islink(os.path.join(dirpath, name)):
                dropped.append((rel_dir / name).as_posix())
                dirnames.remove(name)
        dirnames.sort()
        for name in sorted(filenames):
            rel = (rel_dir / name).as_posix()
            if name in EXCLUDED_NAMES or skip(rel):
                continue
            full = os.path.join(dirpath, name)
            mode = os.lstat(full).st_mode
            if not stat.S_ISREG(mode):
                dropped.append(rel)
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as fin, open(target, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            os.chmod(target, 0o755 if mode & 0o111 else 0o644)
    return sorted(dropped)


def _make_room(tree: Path, rel: str) -> None:
    """Ensure every parent of rel is a directory and rel itself is not one."""
    current = tree
    for part in rel.split("/")[:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            current.unlink()
        current.mkdir(exist_ok=True)
    target = tree / rel
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)


def restore_protected(tree: Path, pristine: Path, patterns: list[str]) -> list[str]:
    """Make every path matching `patterns` identical to pristine.

    Protected files the tree added are removed; protected files that differ or
    are missing are copied from pristine. Returns the sorted paths changed.
    `tree` must come from materialize() (regular files only).
    """
    tree, pristine = Path(tree), Path(pristine)
    protected = matcher(patterns)
    in_tree = {f["path"]: f["sha256"] for f in list_files(tree, canonical_paths=False) if protected(f["path"])}
    in_pristine = {
        f["path"]: f["sha256"] for f in list_files(pristine, canonical_paths=False) if protected(f["path"])
    }
    changed = set()
    for rel in sorted(in_tree.keys() - in_pristine.keys()):
        (tree / rel).unlink()
        changed.add(rel)
    for rel, digest in sorted(in_pristine.items()):
        if in_tree.get(rel) != digest:
            _make_room(tree, rel)
            shutil.copyfile(pristine / rel, tree / rel)
            os.chmod(tree / rel, 0o644)
            changed.add(rel)
    return sorted(changed)


def tree_changes(before: Path, after: Path) -> list[str]:
    """Paths added, removed or modified between two materialised trees."""
    old = {f["path"]: f["sha256"] for f in list_files(before, canonical_paths=False)}
    new = {f["path"]: f["sha256"] for f in list_files(after, canonical_paths=False)}
    return sorted(p for p in old.keys() | new.keys() if old.get(p) != new.get(p))


def out_of_scope(changed_paths: list[str], allowed_patterns: list[str]) -> list[str]:
    """Paths a patch touched that the task's scope does not allow."""
    allowed = matcher(allowed_patterns)
    return sorted(p for p in changed_paths if not allowed(p))
