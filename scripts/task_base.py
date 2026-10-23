#!/usr/bin/env python3
"""Task base steps (spec §6.5). Every command exits 0 on success, 1 when the step cannot go ahead.

Run by .github/workflows/task-base.yml, with the standard library only:
  names --build-sha SHA     print the image name and tag as key=value lines for $GITHUB_OUTPUT
  pins --build-sha SHA --image REF --out FILE
                            write task-base-pins.json

Run by a maintainer, in a full clone at main with the gh CLI logged in:
  pin task-base-pins.json   write task-base.json, if the build files at main are the ones
                            the image was built from, and set the suite's runtime.task_base
                            to the same image
  verify                    check task-base.json, that the build files have not changed since
                            its build commit and that the suite pins the same image, then pull
                            the image anonymously and verify that task-base.yml built it from
                            that commit on main; --offline checks the files only

The release workflow's images job runs `verify` too (spec §6.3).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hb.release import AnonymousDocker, Git, ReleaseError, load_json  # noqa: E402
from hb.taskbase import (  # noqa: E402
    SUITE_FILE, TASK_BASE_DOCKERFILE, TASK_BASE_FILE, TASK_BASE_REPOSITORY, check_build_files_unchanged, check_pins,
    check_suite_task_base, image_tag, pin_suite_text,
    read_pins_file, read_task_base, task_base_document, verify_commands,
)


def cmd_names(args) -> None:
    tag = image_tag(args.build_sha)
    if not (args.root / TASK_BASE_DOCKERFILE).is_file():
        raise ReleaseError(f"{TASK_BASE_DOCKERFILE} is missing")
    attributes = [p for _, _, p in Git(args.root).tree("HEAD") if PurePosixPath(p).name == ".gitattributes"]
    if attributes:
        raise ReleaseError(f"{attributes}: a .gitattributes file can make the checkout the image is built from "
                           "differ from the build commit's files")
    print(f"image={TASK_BASE_REPOSITORY}\ntag={tag}")


def cmd_pins(args) -> None:
    pins = check_pins({"build_sha": args.build_sha, "image": args.image})
    Path(args.out).write_text(json.dumps(pins, indent=2) + "\n", encoding="utf-8")


def cmd_pin(args) -> None:
    pins = read_pins_file(args.pins_file)
    git = Git(args.root)
    if not git.is_clean():
        raise ReleaseError("the working tree has uncommitted changes")
    check_build_files_unchanged(git, pins["build_sha"])
    document = task_base_document(pins)
    suite = args.root / SUITE_FILE
    suite_text = pin_suite_text(suite.read_text(encoding="utf-8"), document) if suite.is_file() else None
    (args.root / TASK_BASE_FILE).write_bytes((json.dumps(document, indent=2) + "\n").encode("utf-8"))
    changed = [TASK_BASE_FILE]
    if suite_text is not None:
        suite.write_bytes(suite_text.encode("utf-8"))
        changed.append(SUITE_FILE)  # a new task base is a new cohort: the suite names it (spec §6.5)
    print(f"{' and '.join(changed)} now pin {document['image']}, built from {document['build_commit']}. Next:\n"
          f"  python3 scripts/task_base.py verify\n"
          f"  git add {' '.join(changed)}\n"
          f"  git commit --message 'Pin the task base built from {image_tag(document['build_commit'])}'\n"
          f"  then open a pull request")


def cmd_verify(args) -> None:
    document = read_task_base(args.root)
    check_build_files_unchanged(Git(args.root), document["build_commit"])
    print(f"{TASK_BASE_FILE}: the build files are those of its build commit {document['build_commit']}")
    if (args.root / SUITE_FILE).is_file():
        check_suite_task_base(load_json(args.root / SUITE_FILE, SUITE_FILE), document)
        print(f"{SUITE_FILE}: runtime.task_base is the same image")
    if args.offline:
        return
    with AnonymousDocker() as docker:
        for command in verify_commands(document["image"], document["build_commit"]):
            docker.run(command)
    print(f"{document['image']} pulls anonymously and was built by task-base.yml from "
          f"{document['build_commit']} on main")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository checkout (default: this one)")
    commands = parser.add_subparsers(dest="command", required=True)
    names = commands.add_parser("names")
    names.add_argument("--build-sha", required=True)
    names.set_defaults(run=cmd_names)
    pins = commands.add_parser("pins")
    for name in ("--build-sha", "--image", "--out"):
        pins.add_argument(name, required=True)
    pins.set_defaults(run=cmd_pins)
    pin = commands.add_parser("pin")
    pin.add_argument("pins_file", type=Path)
    pin.set_defaults(run=cmd_pin)
    verify = commands.add_parser("verify")
    verify.add_argument("--offline", action="store_true", help="check task-base.json and the build files only")
    verify.set_defaults(run=cmd_verify)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except ReleaseError as exc:
        print(f"task base: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
