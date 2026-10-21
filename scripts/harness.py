#!/usr/bin/env python3
"""Harness image steps (spec §6.4). Every command exits 0 on success, 1 when the step cannot go ahead.

Run by .github/workflows/harness.yml:
  inputs --harness ID       (unprivileged job) check the adapter, harness.json against its
                            schema; needs jsonschema
  names --harness ID --build-sha SHA
                            (build and image jobs, standard library only) print the image name, tag and
                            adapter directory as key=value lines for $GITHUB_OUTPUT
  check-image --harness ID --copied DIR --inspect FILE
                            (image job) DIR is /hb copied out of the built image, FILE its
                            `docker image inspect`: /hb/config must be the build commit's config/,
                            every /hb file the entrypoint names must be there, the image must
                            be labelled with the id and have no ENTRYPOINT
  pins --harness ID --build-sha SHA --image REF --out FILE
                            (image job) write harness-pins.json

Run by a maintainer, in a full clone at main with the gh CLI logged in:
  pin harness-pins.json     add or replace the harness's registry entry in registry.json
  verify [ID ...]           check registry entries against their build commits (all when no
                            id is given), then pull each image anonymously, verify its
                            provenance and check its label, entrypoint and /hb/config;
                            --offline checks the entries only

The release workflow's images job runs `verify` too, for every registry entry (spec §6.3).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hb.harness import (  # noqa: E402
    adapter_path, adapter_tree, check_entry, check_image_config, check_image_files, check_inputs, check_pins,
    check_pulled_image, entry_at, harness_repository, image_tag, load_adapter, pin_registry, read_pins_file,
    verify_commands,
)
from hb.release import GIT_SHA, REGISTRY_FILE, AnonymousDocker, Git, ReleaseError, load_json  # noqa: E402


def _print_lines(values: dict) -> None:
    print("\n".join(f"{key}={value}" for key, value in values.items()))


def cmd_inputs(args) -> None:
    harness = check_inputs(args.root / adapter_path(args.harness), args.harness)
    print(f"{adapter_path(args.harness)}: {harness['id']} {harness['version']}, config/ and Dockerfile present")


def cmd_names(args) -> None:
    harness = load_adapter(args.root / adapter_path(args.harness), args.harness)
    if not GIT_SHA.fullmatch(args.build_sha):
        raise ReleaseError("--build-sha is not a full commit sha")
    attributes = [p for _, _, p in Git(args.root).tree("HEAD") if PurePosixPath(p).name == ".gitattributes"]
    if attributes:
        raise ReleaseError(f"{attributes}: a .gitattributes file can make the checkout the image is built from "
                           "differ from the committed files the registry entry is computed from")
    _print_lines({"id": harness["id"], "version": harness["version"], "dir": adapter_path(args.harness),
                  "image": harness_repository(args.harness), "tag": image_tag(harness["version"], args.build_sha)})


def cmd_check_image(args) -> None:
    # Against the committed files at HEAD (the build commit), which the registry entry is computed from.
    with tempfile.TemporaryDirectory() as scratch:
        Git(args.root).export("HEAD", [adapter_path(args.harness)], Path(scratch))
        adapter = Path(scratch) / adapter_path(args.harness)
        check_image_files(adapter, args.copied, load_adapter(adapter, args.harness))
    check_image_config(load_json(args.inspect, "the image inspection"), args.harness)
    print(f"the image holds the committed {adapter_path(args.harness)}/config/ at /hb/config and the entrypoint's "
          f"files, is labelled {args.harness} and has no ENTRYPOINT of its own")


def cmd_pins(args) -> None:
    pins = check_pins({"harness": args.harness, "build_sha": args.build_sha, "image": args.image})
    Path(args.out).write_text(json.dumps(pins, indent=2) + "\n", encoding="utf-8")


def _load_registry(root: Path):
    path = root / REGISTRY_FILE
    return load_json(path, REGISTRY_FILE) if path.exists() else None


def cmd_pin(args) -> None:
    pins = read_pins_file(args.pins_file)
    git = Git(args.root)
    if not git.is_clean():
        raise ReleaseError("the working tree has uncommitted changes")
    harness_id, build = pins["harness"], pins["build_sha"]
    if adapter_tree(git, "HEAD", harness_id) != adapter_tree(git, build, harness_id):
        raise ReleaseError(f"{adapter_path(harness_id)} has changed since the build commit {build}: "
                           "run harness.yml again from main")
    with tempfile.TemporaryDirectory() as scratch:
        entry = entry_at(git, build, harness_id, pins["image"], Path(scratch))
    registry = pin_registry(_load_registry(args.root), entry)
    try:
        from jsonschema import ValidationError

        from hb.schemas import validate
        validate("registry", registry)
    except ImportError as exc:
        raise ReleaseError(f"pin validates registry.json with jsonschema: {exc}") from None
    except ValidationError as exc:
        raise ReleaseError(f"registry.json would not match its schema: {exc.message}") from None
    (args.root / REGISTRY_FILE).write_bytes((json.dumps(registry, indent=2) + "\n").encode("utf-8"))
    print(f"{REGISTRY_FILE} now has {harness_id} {entry['version']} ({entry['image']}). Next:\n"
          f"  python3 scripts/harness.py verify {harness_id}\n"
          f"  git add {REGISTRY_FILE}\n"
          f"  git commit --message 'Register {harness_id} {entry['version']}'\n"
          f"  then open a pull request")


def cmd_verify(args) -> None:
    registry = _load_registry(args.root)
    entries = registry.get("harnesses") if isinstance(registry, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ReleaseError(f"{REGISTRY_FILE} is missing or lists no harness")
    if args.ids:
        ids = list(dict.fromkeys(args.ids))
        by_id = {e.get("id"): e for e in entries if isinstance(e, dict)}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise ReleaseError(f"{REGISTRY_FILE} has no entry for {', '.join(missing)}")
        entries = [by_id[i] for i in ids]
    git = Git(args.root)
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReleaseError(f"{REGISTRY_FILE} has an entry that is not an object")
        with tempfile.TemporaryDirectory() as scratch:
            check_entry(git, entry, Path(scratch))
        print(f"{entry['id']}: the entry is what its build commit {entry['build_commit']} says")
    if args.offline:
        return
    with AnonymousDocker() as docker:
        for entry in entries:
            for command in verify_commands(entry):
                docker.run(command)
            with tempfile.TemporaryDirectory() as scratch:
                check_pulled_image(docker, entry, Path(scratch))
            print(f"{entry['id']}: {entry['image']} pulls anonymously, was built by harness.yml from "
                  f"{entry['build_commit']} on main, and holds config_digest's /hb/config")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository checkout (default: this one)")
    commands = parser.add_subparsers(dest="command", required=True)
    inputs = commands.add_parser("inputs")
    inputs.add_argument("--harness", required=True)
    inputs.set_defaults(run=cmd_inputs)
    names = commands.add_parser("names")
    names.add_argument("--harness", required=True)
    names.add_argument("--build-sha", required=True)
    names.set_defaults(run=cmd_names)
    check_image = commands.add_parser("check-image")
    check_image.add_argument("--harness", required=True)
    check_image.add_argument("--copied", type=Path, required=True)
    check_image.add_argument("--inspect", type=Path, required=True)
    check_image.set_defaults(run=cmd_check_image)
    pins = commands.add_parser("pins")
    for name in ("--harness", "--build-sha", "--image", "--out"):
        pins.add_argument(name, required=True)
    pins.set_defaults(run=cmd_pins)
    pin = commands.add_parser("pin")
    pin.add_argument("pins_file", type=Path)
    pin.set_defaults(run=cmd_pin)
    verify = commands.add_parser("verify")
    verify.add_argument("ids", nargs="*")
    verify.add_argument("--offline", action="store_true", help="check the entries only")
    verify.set_defaults(run=cmd_verify)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except ReleaseError as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
