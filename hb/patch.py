"""Workspace diffs and patch application without trusting any .git directory.

git runs only on trees the toolkit created itself, outside any repository,
with system and global config disabled. Nothing from the harness workspace
(config, hooks, attributes) is ever consulted. All paths are resolved first:
git ignores relative entries in GIT_CEILING_DIRECTORIES, and a relative
workdir inside some checkout would otherwise be treated as part of that repo.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .tree import materialize


class PatchError(Exception):
    """A patch produced by workspace_diff() failed to apply. Indicates a toolkit fault."""


def _git_env(home: Path) -> dict:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CEILING_DIRECTORIES": str(home.parent),
    }


def workspace_diff(pristine: Path, snapshot: Path, scratch: Path, ignore: list[str] = ()) -> bytes:
    """Git-format diff from pristine to snapshot, with paths relative to the repo root.

    Both trees are materialised as scratch/a and scratch/b (regular files only,
    modes normalised, `ignore` globs dropped) and compared with
    `git diff --no-index --no-prefix`.
    """
    scratch = Path(scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    materialize(Path(pristine).resolve(), scratch / "a", ignore)
    materialize(Path(snapshot).resolve(), scratch / "b", ignore)
    proc = subprocess.run(
        ["git", "diff", "--no-index", "--no-prefix", "--binary", "--no-color",
         "--no-ext-diff", "--no-textconv", "--no-renames", "a", "b"],
        cwd=scratch, env=_git_env(scratch), capture_output=True, check=False,
    )
    if proc.returncode not in (0, 1):  # 1 means "differences found"
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))
    return proc.stdout


def apply_patch(pristine: Path, patch: bytes, workdir: Path, ignore: list[str] = ()) -> Path:
    """Apply `patch` to a copy of pristine; return a regular-files-only tree."""
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=False)
    applied = workdir / "applied"
    materialize(Path(pristine).resolve(), applied)
    if patch.strip():
        patch_file = workdir / "workspace.patch"
        patch_file.write_bytes(patch)
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_file)],
            cwd=applied, env=_git_env(workdir), capture_output=True, check=False,
        )
        if proc.returncode != 0:
            raise PatchError(proc.stderr.decode("utf-8", errors="replace"))
    final = workdir / "tree"
    materialize(applied, final, ignore)  # normalise whatever the patch created
    return final
