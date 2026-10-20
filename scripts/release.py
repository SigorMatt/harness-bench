#!/usr/bin/env python3
"""Release steps (spec §6.3). Every command exits 0 on success, 1 when the release cannot go ahead.

Run by .github/workflows/release.yml:
  inputs --version vX.Y.Z   (unprivileged job) check the version, the release suite and
                            registry against their schemas and plan; needs jsonschema
  names --version vX.Y.Z    (release job, standard library only) print the image names
                            and inputs as key=value lines for $GITHUB_OUTPUT
  pins --version ... --build-sha ... --toolkit-image ... --suite-image ... --out FILE
                            (release job, standard library only) write release-pins.json

Run by a maintainer, in a full clone with the gh CLI logged in:
  pin release-pins.json     at the build commit: pin the release images in bench.yml
  verify [COMMIT]           check the release commit (default HEAD), then pull each
                            pinned image anonymously and verify its provenance
  entry [COMMIT]            print the release table entry for the release commit
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hb.release import (  # noqa: E402
    BENCH_WORKFLOW, REGISTRY_FILE, SLUG, SUITE_DIR, TOOLKIT_REPOSITORY, Git, ReleaseError, load_json, parse_json,
    check_image, check_inputs, check_pins, check_release_commit, check_version, pin_workflow, read_pins_file,
    release_entry_at, suite_id_of, suite_repository, verify_commands,
)


def _names(root: Path, version: str) -> dict:
    """The workflow's values, read with the standard library only."""
    suite_id = suite_id_of(load_json(root / SUITE_DIR / "suite.json", f"{SUITE_DIR}/suite.json"))
    registry = load_json(root / REGISTRY_FILE, REGISTRY_FILE)
    harnesses = registry.get("harnesses") if isinstance(registry, dict) else None
    first = harnesses[0].get("id") if isinstance(harnesses, list) and harnesses and isinstance(harnesses[0], dict) \
        else None
    if not isinstance(first, str) or not SLUG.fullmatch(first):
        raise ReleaseError(f"{REGISTRY_FILE} lists no harness")
    return {"version": check_version(version), "toolkit": TOOLKIT_REPOSITORY, "suite": suite_repository(suite_id),
            "suite_dir": SUITE_DIR, "registry": REGISTRY_FILE, "harness": first}


def _print_lines(values: dict) -> None:
    print("\n".join(f"{key}={value}" for key, value in values.items()))


def cmd_inputs(args) -> None:
    check_version(args.version)
    git = Git(args.root)
    attributes = [p for _, _, p in git.tree("HEAD") if PurePosixPath(p).name == ".gitattributes"]
    if attributes:
        raise ReleaseError(f"{attributes}: a .gitattributes file can make a checkout differ from the committed files")
    check_inputs(args.root / SUITE_DIR, args.root / REGISTRY_FILE)
    _print_lines(_names(args.root, args.version))


def cmd_names(args) -> None:
    _print_lines(_names(args.root, args.version))


def cmd_pins(args) -> None:
    pins = check_pins({"version": args.version, "build_sha": args.build_sha,  # what `pin` will accept
                       "toolkit_image": args.toolkit_image, "suite_image": args.suite_image})
    Path(args.out).write_text(json.dumps(pins, indent=2) + "\n", encoding="utf-8")


def cmd_pin(args) -> None:
    pins = read_pins_file(args.pins_file)
    git = Git(args.root)
    if git.head() != pins["build_sha"]:
        raise ReleaseError(f"HEAD is not the build commit: run git switch --detach {pins['build_sha']} first")
    if not git.is_clean():
        raise ReleaseError("the working tree has uncommitted changes")
    suite_id = suite_id_of(parse_json(git.text("HEAD", f"{SUITE_DIR}/suite.json"), f"{SUITE_DIR}/suite.json"))
    check_image(pins["suite_image"], suite_repository(suite_id))
    pinned = pin_workflow(git.text("HEAD", BENCH_WORKFLOW), toolkit_image=pins["toolkit_image"],
                          suite_image=pins["suite_image"])
    # Bytes, not text: text mode on Windows would end every line in "\r\n", which verify refuses.
    (args.root / BENCH_WORKFLOW).write_bytes(pinned.encode("utf-8"))
    version = pins["version"]
    print(f"{BENCH_WORKFLOW} now pins the images of {version}. Next:\n"
          f"  git commit --all --message 'Release {version}'\n"
          f"  python3 scripts/release.py verify\n"
          f"  git tag --annotate {version} --message 'Release {version}'\n"
          f"  git push origin {version}")


def _docker_host() -> str | None:
    """The daemon the current Docker context points at, so an empty DOCKER_CONFIG keeps it.

    Only the address: a context that needs TLS material makes the pull fail, visibly.
    """
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    try:
        proc = subprocess.run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                              capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def cmd_verify(args) -> None:
    info = check_release_commit(Git(args.root), args.commit)
    print(f"{info['release_sha']}: one parent, the build commit {info['build_sha']}; "
          f"only the two image pins in {BENCH_WORKFLOW} change")
    if args.offline:
        return
    host = _docker_host()
    with tempfile.TemporaryDirectory() as empty_config:
        env = {**os.environ, "DOCKER_CONFIG": empty_config}  # anonymous: only a public image pulls
        if host:
            env["DOCKER_HOST"] = host
        for command in verify_commands(info):
            try:
                proc = subprocess.run(command, env=env, check=False)
            except OSError as exc:
                raise ReleaseError(f"{command[0]}: {exc}") from None
            if proc.returncode != 0:
                raise ReleaseError(f"failed (exit status {proc.returncode}): {' '.join(command)}")
    print("both images pull anonymously and were built by release.yml from the build commit on main")


def cmd_entry(args) -> None:
    try:
        from jsonschema import ValidationError

        from hb.schemas import validate
    except ImportError as exc:
        raise ReleaseError(f"entry validates its output with jsonschema: {exc}") from None
    with tempfile.TemporaryDirectory() as scratch:
        entry = release_entry_at(Git(args.root), args.commit, Path(scratch))
    try:
        validate("release", entry)
    except ValidationError as exc:
        raise ReleaseError(f"the release entry does not match its schema: {exc.message}") from None
    print(json.dumps(entry, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository checkout (default: this one)")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, run in (("inputs", cmd_inputs), ("names", cmd_names)):
        command = commands.add_parser(name)
        command.add_argument("--version", required=True)
        command.set_defaults(run=run)
    pins = commands.add_parser("pins")
    for name in ("--version", "--build-sha", "--toolkit-image", "--suite-image", "--out"):
        pins.add_argument(name, required=True)
    pins.set_defaults(run=cmd_pins)
    pin = commands.add_parser("pin")
    pin.add_argument("pins_file", type=Path)
    pin.set_defaults(run=cmd_pin)
    verify = commands.add_parser("verify")
    verify.add_argument("commit", nargs="?", default="HEAD")
    verify.add_argument("--offline", action="store_true", help="check the commit only")
    verify.set_defaults(run=cmd_verify)
    entry = commands.add_parser("entry")
    entry.add_argument("commit", nargs="?", default="HEAD")
    entry.set_defaults(run=cmd_entry)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
