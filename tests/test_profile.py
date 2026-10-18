"""The isolation profile is data, so it is tested by asserting on the generated
arguments. Nothing here starts a container or probes a sandbox."""

import json
from pathlib import Path

from hb import profile

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
COHORT = json.loads((EXAMPLES / "cohort.json").read_text())
NAMES = profile.rep_names("fix-pagination-off-by-one", 1, 1)
IMAGE = "ghcr.io/owner/hb-harness-baseline@sha256:" + "4" * 64
TOOLKIT = "ghcr.io/owner/hb-toolkit@sha256:" + "d" * 64


def flag_values(args, flag):
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def harness_args():
    return profile.harness_run_args(NAMES, COHORT, IMAGE, ["/hb/entrypoint"])


def test_harness_network_is_the_rep_internal_network():
    assert flag_values(harness_args(), "--network") == [NAMES["network"]]
    assert "--internal" in profile.network_create_args(NAMES)


def test_harness_has_no_ambient_authority():
    args = harness_args()
    for flag in ("--privileged", "--cap-add", "--device", "--pid", "--ipc", "--volume", "-v",
                 "--network-alias", "--add-host", "--env-file", "--userns", "--security-opt=seccomp=unconfined"):
        assert not any(a == flag or a.startswith(flag + "=") for a in args), flag
    joined = " ".join(args)
    for text in ("docker.sock", "type=bind", "MODEL_API_KEY", "ACTIONS_", "GITHUB_"):
        assert text not in joined, text
    assert flag_values(args, "--cap-drop") == ["ALL"]
    assert flag_values(args, "--security-opt") == ["no-new-privileges"]
    assert "--read-only" in args
    assert flag_values(args, "--user") == [profile.HARNESS_USER]


def test_harness_environment_is_exactly_the_allowlist():
    env = dict(e.split("=", 1) for e in flag_values(harness_args(), "--env"))
    assert set(env) == set(profile.HARNESS_ENV)
    assert env["OPENAI_API_KEY"] == profile.PLACEHOLDER_KEY
    assert env["OPENAI_BASE_URL"] == f"http://{profile.GATEWAY_ALIAS}:{profile.GATEWAY_PORT}/v1"
    assert env["HB_MODEL"] == COHORT["model"]["model_id"]


def test_harness_limits_come_from_the_cohort():
    args = harness_args()
    runtime = COHORT["runtime"]
    memory = f"{runtime['harness_memory_mib']}m"
    assert flag_values(args, "--memory") == [memory]
    assert flag_values(args, "--memory-swap") == [memory]  # no swap beyond the limit
    assert flag_values(args, "--pids-limit") == [str(runtime["harness_pids"])]
    assert flag_values(args, "--cpus") == ["2.000"]


def test_harness_mounts_are_named_volumes_and_task_is_read_only():
    mounts = flag_values(harness_args(), "--mount")
    assert mounts == [
        f"type=volume,src={NAMES['workspace']},dst=/workspace",
        f"type=volume,src={NAMES['task']},dst=/task,readonly",
    ]


def test_gateway_receives_the_key_by_name_only():
    args = profile.gateway_run_args(NAMES, COHORT, TOOLKIT, upstream_retries=3)
    assert "MODEL_API_KEY" in flag_values(args, "--env")
    assert all("=" not in e for e in flag_values(args, "--env"))
    assert flag_values(args, "--model") == [COHORT["model"]["model_id"]]
    assert flag_values(args, "--upstream") == [COHORT["model"]["base_url"]]


def test_profiles_are_pure_functions():
    assert harness_args() == harness_args()
    assert profile.gateway_run_args(NAMES, COHORT, TOOLKIT, 3) == profile.gateway_run_args(NAMES, COHORT, TOOLKIT, 3)
