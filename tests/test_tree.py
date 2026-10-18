import os
import stat

import pytest

from hb.tree import glob_to_regex, materialize, out_of_scope, restore_protected, tree_changes


@pytest.mark.parametrize("pattern,path,expected", [
    ("tests/**", "tests/test_a.py", True),
    ("tests/**", "tests/unit/test_a.py", True),
    ("tests/**", "src/tests.py", False),
    ("**/conftest.py", "conftest.py", True),
    ("**/conftest.py", "a/b/conftest.py", True),
    ("**/conftest.py", "a/b/myconftest.py", False),
    ("*.py", "pager.py", True),
    ("*.py", "pkg/pager.py", False),
    ("**/*.pth", "site/evil.pth", True),
    ("pager.py", "pager.py", True),
    ("pager.?y", "pager.py", True),
])
def test_glob_semantics(pattern, path, expected):
    assert (glob_to_regex(pattern).fullmatch(path) is not None) is expected


def write(root, rel, text, mode=0o644):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.chmod(path, mode)


def test_materialize_keeps_regular_files_and_normalises_modes(tmp_path):
    src = tmp_path / "src"
    write(src, "pkg/mod.py", "x = 1\n", 0o600)
    write(src, "run.sh", "echo hi\n", 0o700)
    write(src, ".git/config", "[core]\n")
    write(src, "vendor/.git/HEAD", "ref\n")
    os.symlink("pkg/mod.py", src / "alias.py")
    os.symlink("pkg", src / "pkg-link")

    dropped = materialize(src, tmp_path / "dst")

    dst = tmp_path / "dst"
    assert sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file()) == ["pkg/mod.py", "run.sh"]
    assert dropped == ["alias.py", "pkg-link"]
    assert stat.S_IMODE(os.stat(dst / "pkg/mod.py").st_mode) == 0o644
    assert stat.S_IMODE(os.stat(dst / "run.sh").st_mode) == 0o755


def test_restore_protected_makes_protected_paths_match_pristine(tmp_path):
    pristine, tree = tmp_path / "pristine", tmp_path / "tree"
    write(pristine, "pager.py", "fixed? no\n")
    write(pristine, "tests/test_pager.py", "original test\n")
    write(pristine, "tests/test_other.py", "other test\n")
    write(tree, "pager.py", "fixed\n")
    write(tree, "tests/test_pager.py", "edited test\n")  # modified
    write(tree, "conftest.py", "added\n")  # added, protected by **/conftest.py
    # tests/test_other.py deleted in tree

    changed = restore_protected(tree, pristine, ["tests/**", "**/conftest.py"])

    assert changed == ["conftest.py", "tests/test_other.py", "tests/test_pager.py"]
    assert (tree / "tests/test_pager.py").read_text() == "original test\n"
    assert (tree / "tests/test_other.py").read_text() == "other test\n"
    assert not (tree / "conftest.py").exists()
    assert (tree / "pager.py").read_text() == "fixed\n"  # unprotected edits survive


def test_restore_protected_replaces_a_directory_or_file_in_the_way(tmp_path):
    pristine, tree = tmp_path / "pristine", tmp_path / "tree"
    write(pristine, "tests/test_a.py", "a\n")
    write(pristine, "tests/data/case.txt", "case\n")
    write(tree, "tests/test_a.py/inner.txt", "dir where a file belongs\n")
    write(tree, "tests/data", "file where a dir belongs\n")

    restore_protected(tree, pristine, ["tests/**"])

    assert (tree / "tests/test_a.py").read_text() == "a\n"
    assert (tree / "tests/data/case.txt").read_text() == "case\n"


def test_tree_changes_and_scope(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    write(before, "pager.py", "a\n")
    write(before, "README.md", "r\n")
    write(after, "pager.py", "b\n")
    write(after, "README.md", "r\n")
    write(after, "notes.txt", "n\n")
    changed = tree_changes(before, after)
    assert changed == ["notes.txt", "pager.py"]
    assert out_of_scope(changed, ["pager.py"]) == ["notes.txt"]
