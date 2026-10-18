import io
import json
import os
import shutil
import tarfile
from pathlib import Path

import pytest

from hb.manifest import (
    MANIFEST_NAME, VerificationError, build_task_manifest, expected_paths, extract_bundle,
    list_files, pack_bundle, verify_task_bundle,
)

EXAMPLE_BUNDLE = Path(__file__).resolve().parent.parent / "examples/run/task-bundles/fix-pagination-off-by-one"
RUN = {"repository": "example-user/hb-runs", "run_id": 1, "run_attempt": 1}


def make_bundle(root: Path, reps: int = 2) -> dict:
    for rel in expected_paths(reps):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content of {rel}\n")
    manifest = build_task_manifest(root, run=RUN, task_id="t", reps=reps)
    (root / MANIFEST_NAME).write_text(json.dumps(manifest))
    return manifest


def test_a_fresh_bundle_verifies(tmp_path):
    manifest = make_bundle(tmp_path)
    verify_task_bundle(tmp_path, manifest)


def test_a_changed_byte_fails_verification_and_names_the_file(tmp_path):
    manifest = make_bundle(tmp_path)
    (tmp_path / "reps/1/workspace.patch").write_text("content of reps/1/workspace.patcH\n")
    with pytest.raises(VerificationError, match="reps/1/workspace.patch"):
        verify_task_bundle(tmp_path, manifest)


def test_an_extra_file_fails_verification(tmp_path):
    manifest = make_bundle(tmp_path)
    (tmp_path / "reps/1/notes.txt").write_text("extra\n")
    with pytest.raises(VerificationError, match="unexpected"):
        verify_task_bundle(tmp_path, manifest)


def test_a_missing_file_fails_verification(tmp_path):
    manifest = make_bundle(tmp_path)
    (tmp_path / "reps/2/grade.json").unlink()
    with pytest.raises(VerificationError, match="missing"):
        verify_task_bundle(tmp_path, manifest)


def test_building_a_manifest_requires_the_fixed_layout(tmp_path):
    (tmp_path / "reps/1").mkdir(parents=True)
    (tmp_path / "reps/1/grade.json").write_text("{}")
    with pytest.raises(VerificationError, match="layout"):
        build_task_manifest(tmp_path, run=RUN, task_id="t", reps=1)


def test_only_regular_files_are_listed(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    os.symlink("a.txt", tmp_path / "link.txt")
    with pytest.raises(VerificationError, match="regular"):
        list_files(tmp_path)


def test_packing_is_deterministic(tmp_path):
    copy_a, copy_b = tmp_path / "a", tmp_path / "b"
    shutil.copytree(EXAMPLE_BUNDLE, copy_a)
    shutil.copytree(EXAMPLE_BUNDLE, copy_b)
    os.utime(copy_b / "reps/1/harness.log", (0, 12345))  # mtimes must not matter
    assert pack_bundle(copy_a, tmp_path / "a.tar") == pack_bundle(copy_b, tmp_path / "b.tar")


def test_pack_then_extract_round_trips(tmp_path):
    pack_bundle(EXAMPLE_BUNDLE, tmp_path / "bundle.tar")
    extract_bundle(tmp_path / "bundle.tar", tmp_path / "out")
    assert list_files(tmp_path / "out") == list_files(EXAMPLE_BUNDLE)


def test_extraction_accepts_regular_files_only(tmp_path):
    archive = tmp_path / "with-link.tar"
    with tarfile.open(archive, "w") as tar:
        data = b"hello\n"
        info = tarfile.TarInfo("reps/1/harness.log")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("reps/1/grade.json")
        link.type = tarfile.SYMTYPE
        link.linkname = "harness.log"
        tar.addfile(link)
    with pytest.raises(VerificationError, match="not a regular file"):
        extract_bundle(archive, tmp_path / "out")


def test_extraction_rejects_directory_members(tmp_path):
    archive = tmp_path / "with-dir.tar"
    with tarfile.open(archive, "w") as tar:
        extra = tarfile.TarInfo("reps/1/extra")
        extra.type = tarfile.DIRTYPE
        tar.addfile(extra)
    with pytest.raises(VerificationError, match="not a regular file"):
        extract_bundle(archive, tmp_path / "out")


def test_extraction_requires_an_empty_directory(tmp_path):
    pack_bundle(EXAMPLE_BUNDLE, tmp_path / "bundle.tar")
    (tmp_path / "out").mkdir()
    (tmp_path / "out/leftover").write_text("x")
    with pytest.raises(VerificationError, match="empty"):
        extract_bundle(tmp_path / "bundle.tar", tmp_path / "out")
