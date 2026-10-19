"""Tar streams into and out of containers. Fixture archives stand in for `docker cp` output."""

import io
import os
import stat
import tarfile

import pytest

from hb.transfer import LimitExceeded, TransferError, extract_snapshot, read_single_file, tree_entries, write_tar


def archive(members) -> io.BytesIO:
    """members: (name, kind, payload) with kind in dir, file, exec, symlink, hardlink, fifo."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, kind, payload in members:
            info = tarfile.TarInfo(name)
            info.uid = info.gid = 10001
            if kind == "dir":
                info.type, info.mode = tarfile.DIRTYPE, 0o700
                tar.addfile(info)
            elif kind in ("file", "exec"):
                info.mode = 0o700 if kind == "exec" else 0o000
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, payload
                tar.addfile(info)
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, payload
                tar.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                tar.addfile(info)
    buf.seek(0)
    return buf


def extract(tmp_path, members, **limits):
    limits = {"max_bytes": 1 << 20, "max_entries": 1000, **limits}
    dest = tmp_path / "raw"
    counts = extract_snapshot(archive(members), dest, root="workspace", **limits)
    return dest / "workspace", counts


def test_keeps_directories_files_and_hard_links_to_kept_files(tmp_path):
    tree, counts = extract(tmp_path, [
        ("workspace", "dir", None),
        ("workspace/src", "dir", None),
        ("workspace/src/a.py", "file", b"a\n"),
        ("workspace/run.sh", "exec", b"#!/bin/sh\n"),
        ("workspace/src/b.py", "hardlink", "workspace/src/a.py"),
    ])
    assert (tree / "src/a.py").read_bytes() == b"a\n"
    assert (tree / "src/b.py").read_bytes() == b"a\n"
    assert stat.S_IMODE(os.stat(tree / "src/a.py").st_mode) == 0o644  # mode 000 in the archive
    assert stat.S_IMODE(os.stat(tree / "run.sh").st_mode) == 0o755
    assert stat.S_IMODE(os.stat(tree / "src").st_mode) == 0o755  # 0700 in the archive
    assert counts == {"files": 3, "skipped": 0}


def test_skips_symlinks_fifos_and_links_to_skipped_entries(tmp_path):
    tree, counts = extract(tmp_path, [
        ("workspace", "dir", None),
        ("workspace/out", "symlink", "/etc/passwd"),
        ("workspace/up", "symlink", "../.."),
        ("workspace/pipe", "fifo", None),
        ("workspace/pipe2", "hardlink", "workspace/pipe"),
        ("workspace/ok.txt", "file", b"ok"),
    ])
    assert sorted(p.name for p in tree.iterdir()) == ["ok.txt"]
    assert counts == {"files": 1, "skipped": 4}


def test_names_outside_the_root_are_rejected(tmp_path):
    with pytest.raises(TransferError):
        extract(tmp_path, [("workspace", "dir", None), ("other/x", "file", b"x")])
    with pytest.raises(TransferError):
        extract(tmp_path / "b", [("workspace", "dir", None), ("workspace/../../x", "file", b"x")])


def test_size_and_entry_limits(tmp_path):
    members = [("workspace", "dir", None)] + [(f"workspace/f{i}", "file", b"12345") for i in range(4)]
    extract(tmp_path / "a", members, max_bytes=20, max_entries=5)
    with pytest.raises(LimitExceeded):
        extract(tmp_path / "b", members, max_bytes=19)
    with pytest.raises(LimitExceeded):
        extract(tmp_path / "c", members, max_entries=4)


def test_hard_links_count_their_target_size_again(tmp_path):
    # Each kept link becomes a full copy when the tree is materialised.
    members = [("workspace", "dir", None), ("workspace/big", "file", b"x" * 10)]
    members += [(f"workspace/link{i}", "hardlink", "workspace/big") for i in range(3)]
    tree, counts = extract(tmp_path / "a", members, max_bytes=40)
    assert counts == {"files": 4, "skipped": 0}
    assert (tree / "link2").read_bytes() == b"x" * 10
    with pytest.raises(LimitExceeded):
        extract(tmp_path / "b", members, max_bytes=39)


def test_malformed_stream(tmp_path):
    with pytest.raises(TransferError):
        extract_snapshot(io.BytesIO(b"not a tar archive" * 64), tmp_path / "raw", root="workspace",
                         max_bytes=10, max_entries=10)


def test_write_tar_round_trip_with_owner_and_modes(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "m.py").write_text("x = 1\n")
    (src / "tool").write_text("#!/bin/sh\n")
    os.chmod(src / "tool", 0o700)
    entries = tree_entries(src, prefix="tree/")
    assert [name for name, _ in entries] == ["tree/pkg", "tree/pkg/m.py", "tree/tool"]
    tar_path = tmp_path / "t.tar"
    write_tar(tar_path, [("tree", src)] + entries, uid=10001, gid=10001)
    with tarfile.open(tar_path) as tar:
        got = {m.name: (m.type, m.mode, m.uid, m.gid, m.mtime) for m in tar}
    assert got == {
        "tree": (tarfile.DIRTYPE, 0o755, 10001, 10001, 0),
        "tree/pkg": (tarfile.DIRTYPE, 0o755, 10001, 10001, 0),
        "tree/pkg/m.py": (tarfile.REGTYPE, 0o644, 10001, 10001, 0),
        "tree/tool": (tarfile.REGTYPE, 0o755, 10001, 10001, 0),
    }


def test_tree_entries_refuses_other_kinds(tmp_path):
    (tmp_path / "t").mkdir()
    os.symlink("/etc", tmp_path / "t" / "link")
    with pytest.raises(TransferError):
        tree_entries(tmp_path / "t")


def test_read_single_file():
    assert read_single_file(archive([("gateway.jsonl", "file", b"{}\n")]), max_bytes=10) == b"{}\n"
    with pytest.raises(LimitExceeded):
        read_single_file(archive([("gateway.jsonl", "file", b"{}\n")]), max_bytes=2)
    with pytest.raises(TransferError):
        read_single_file(archive([("a", "file", b"1"), ("b", "file", b"2")]), max_bytes=10)
    with pytest.raises(TransferError):
        read_single_file(archive([("d", "dir", None)]), max_bytes=10)
