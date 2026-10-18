import os
import subprocess
from pathlib import Path

from hb.manifest import list_files
from hb.patch import apply_patch, workspace_diff


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_diff_then_apply_reproduces_the_workspace(tmp_path):
    pristine, workspace = tmp_path / "pristine", tmp_path / "workspace"
    write(pristine, "pager.py", "start = number * size\n")
    write(pristine, "old.txt", "to be deleted\n")
    write(pristine, "README.md", "unchanged\n")
    write(workspace, "pager.py", "start = (number - 1) * size\n")
    write(workspace, "README.md", "unchanged\n")
    write(workspace, "pkg/new_module.py", "x = 1\n")
    write(workspace, ".git/config", "[core]\n\tfsmonitor = anything\n")  # never consulted, never diffed

    patch = workspace_diff(pristine, workspace, tmp_path / "diff")
    assert b".git" not in patch
    tree = apply_patch(pristine, patch, tmp_path / "apply")

    expected = {f["path"]: f["sha256"] for f in list_files(workspace, canonical_paths=False) if not f["path"].startswith(".git/")}
    assert {f["path"]: f["sha256"] for f in list_files(tree)} == expected


def test_symlinks_in_the_workspace_never_reach_the_patch(tmp_path):
    pristine, workspace = tmp_path / "pristine", tmp_path / "workspace"
    write(pristine, "pager.py", "a\n")
    write(workspace, "pager.py", "a\n")
    os.symlink("pager.py", workspace / "alias.py")
    assert workspace_diff(pristine, workspace, tmp_path / "diff") == b""


def test_empty_patch_yields_pristine(tmp_path):
    pristine = tmp_path / "pristine"
    write(pristine, "pager.py", "a\n")
    tree = apply_patch(pristine, b"", tmp_path / "apply")
    assert list_files(tree) == list_files(pristine)


def test_relative_paths_inside_a_git_checkout_still_work_outside_any_repo(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    monkeypatch.chdir(checkout)
    write(Path("pristine"), "pager.py", "start = number * size\n")
    write(Path("workspace"), "pager.py", "start = (number - 1) * size\n")

    patch = workspace_diff(Path("pristine"), Path("workspace"), Path("diff"))
    tree = apply_patch(Path("pristine"), patch, Path("apply"))

    assert (tree / "pager.py").read_text() == "start = (number - 1) * size\n"


def test_ignored_paths_never_enter_the_patch(tmp_path):
    pristine, workspace = tmp_path / "pristine", tmp_path / "workspace"
    write(pristine, "pager.py", "a\n")
    write(workspace, "pager.py", "a\n")
    write(workspace, "pkg/__pycache__/m.cpython-313.pyc", "bytecode")
    write(workspace, ".pytest_cache/v/cache/lastfailed", "{}")
    ignore = ["**/__pycache__/**", "**/*.pyc", ".pytest_cache/**"]
    assert workspace_diff(pristine, workspace, tmp_path / "diff", ignore) == b""
