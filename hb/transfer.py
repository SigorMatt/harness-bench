"""Moving trees into and out of containers as tar streams.

Sibling containers are created through the host's Docker daemon, so they
cannot see the toolkit's own files. Trees therefore travel as tar archives:
into named volumes with `docker cp --archive - <helper>:<dir>` (the helper is a
container that mounts the volumes and is created but never started), and the
workspace comes back with `docker cp <helper>:/workspace -`.

Going in, write_tar() packs toolkit-made trees (directories and regular files
only) with fixed owners and normalised modes.

Coming out, extract_snapshot() reads the workspace as a stream. It keeps
directories, regular files and hard links to files it already kept, and skips
everything else (symlinks, FIFOs, sockets, devices): the same rule as
hb.tree.materialize. Names are checked by the standard library's `data`
filter, modes are normalised, and the total size and number of entries are
capped. workspace_diff() then materialises the result again, which drops .git
and the suite's ignore_paths, and diffs it against pristine.
"""

from __future__ import annotations

import os
import stat
import tarfile
from pathlib import Path

DIR_MODE = 0o755


class TransferError(Exception):
    """A tar stream did not have the expected shape. Fails the task job."""


class LimitExceeded(TransferError):
    """A workspace or log was larger than the toolkit accepts. Fails the task job."""


def _file_mode(mode: int) -> int:
    return 0o755 if mode & 0o111 else 0o644


def tree_entries(root: Path, prefix: str = "") -> list[tuple[str, Path]]:
    """(archive name, path) for every directory and regular file under root, sorted.

    Raises TransferError for anything else: the trees packed here are made by
    the toolkit (hb.tree.materialize), so they never hold other kinds of entry.
    """
    root = Path(root)
    entries = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        rel_dir = Path(dirpath).relative_to(root)
        for name in sorted(dirnames) + sorted(filenames):
            full = Path(dirpath, name)
            mode = os.lstat(full).st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise TransferError(f"not a directory or regular file: {(rel_dir / name).as_posix()}")
            rel = (rel_dir / name).as_posix()
            entries.append((f"{prefix}{rel}", full))
    return sorted(entries)


def write_tar(tar_path: Path, entries: list[tuple[str, Path]], *, uid: int, gid: int) -> None:
    """Pack (archive name, path) entries: directories 0755, files 0644 or 0755, mtime 0."""
    with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as tar:
        for name, path in entries:
            st = os.lstat(path)
            info = tarfile.TarInfo(name)
            info.uid, info.gid, info.uname, info.gname, info.mtime = uid, gid, "", "", 0
            if stat.S_ISDIR(st.st_mode):
                info.type, info.mode = tarfile.DIRTYPE, DIR_MODE
                tar.addfile(info)
            elif stat.S_ISREG(st.st_mode):
                info.mode, info.size = _file_mode(st.st_mode), st.st_size
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as fh:
                    tar.addfile(info, fh)
            else:
                raise TransferError(f"not a directory or regular file: {name}")


def extract_snapshot(stream, dest: Path, *, root: str, max_bytes: int, max_entries: int) -> dict:
    """Extract a `docker cp <container>:/<root> -` stream into dest.

    Every member must be `root` or lie under it. Returns counts of the
    regular files kept and the entries skipped. Raises LimitExceeded when the
    files kept total more than max_bytes (a hard link counts its target's size
    again, since it becomes a copy) or the stream has more than max_entries
    members, and TransferError for a malformed stream.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    kept_files: dict[str, int] = {}  # archive name -> size, for hard links to them
    counts = {"entries": 0, "bytes": 0, "files": 0, "skipped": 0}

    def kind(member: tarfile.TarInfo) -> str | None:
        if member.isdir():
            return "dir"
        if member.isreg():
            return "file"
        if member.islnk() and member.linkname in kept_files:
            return "link"
        return None  # symlinks, FIFOs, devices, links to something skipped

    def admitted(tar: tarfile.TarFile):
        """Members in stream order, counted and checked once each."""
        for member in tar:
            counts["entries"] += 1
            if counts["entries"] > max_entries:
                raise LimitExceeded(f"the workspace has more than {max_entries} entries")
            if member.name != root and not member.name.startswith(root + "/"):
                raise TransferError(f"unexpected entry in the snapshot: {member.name!r}")
            what = kind(member)
            if what is None:
                counts["skipped"] += 1
                continue
            if what != "dir":
                counts["files"] += 1
                counts["bytes"] += member.size if what == "file" else kept_files[member.linkname]
                if counts["bytes"] > max_bytes:
                    raise LimitExceeded(f"the workspace holds more than {max_bytes} bytes of files")
            if what == "file":
                kept_files[member.name] = member.size
            yield member

    def normalise(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
        # A pure function of the member: extractall applies it to directories a second time.
        try:
            checked = tarfile.data_filter(member, path)
        except tarfile.FilterError as exc:
            raise TransferError(f"unsafe entry in the snapshot: {exc}") from None
        if member.isdir():
            return checked.replace(mode=DIR_MODE, deep=False)
        if member.isreg():
            return checked.replace(mode=_file_mode(member.mode), deep=False)
        return checked  # a hard link to a regular file kept earlier

    try:
        with tarfile.open(fileobj=stream, mode="r|") as tar:
            tar.extractall(dest, members=admitted(tar), filter=normalise)
    except (tarfile.ReadError, tarfile.StreamError, EOFError) as exc:
        raise TransferError(f"malformed snapshot stream: {exc}") from None
    return {"files": counts["files"], "skipped": counts["skipped"]}


def read_single_file(stream, *, max_bytes: int) -> bytes:
    """Content of the one regular file in a `docker cp <container>:<file> -` stream."""
    data = None
    try:
        with tarfile.open(fileobj=stream, mode="r|") as tar:
            for member in tar:
                if data is not None or not member.isreg():
                    raise TransferError(f"expected exactly one regular file, got {member.name!r}")
                if member.size > max_bytes:
                    raise LimitExceeded(f"{member.name} is larger than {max_bytes} bytes")
                data = tar.extractfile(member).read()
    except (tarfile.ReadError, tarfile.StreamError, EOFError) as exc:
        raise TransferError(f"malformed file stream: {exc}") from None
    if data is None:
        raise TransferError("the stream holds no file")
    return data
