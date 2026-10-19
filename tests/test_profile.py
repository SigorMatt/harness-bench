"""The isolation profile is data, so it is tested by asserting on the generated
arguments. Nothing here starts a container or probes a sandbox."""

import json
from pathlib import Path

from hb import profile

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
COHORT = json.loads((EXAMPLES / "cohort.json").read_text())
NAMES = profile.rep_names("fix-pagination-off-by-one", 1, 1)
IMAGE = "ghcr.io/sigormatt/hb-harness-baseline@sha256:" + "4" * 64
TOOLKIT = "ghcr.io/sigormatt/hb-toolkit@sha256:" + "d" * 64


def flag_values(args, flag):
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def harness_args():
    return profile.harness_create_args(NAMES, COHORT, IMAGE, ["/hb/entrypoint"])


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


def test_harness_is_created_not_auto_removed_and_its_log_is_capped():
    args = harness_args()
    assert args[:3] == ["docker", "create", "--name"]
    assert "--rm" not in args and "--detach" not in args
    assert flag_values(args, "--log-driver") == ["json-file"]
    assert sorted(flag_values(args, "--log-opt")) == ["max-file=2", "max-size=8m"]


def test_helper_holds_the_volumes_and_has_no_network():
    args = profile.helper_create_args(NAMES, TOOLKIT)
    assert args[:2] == ["docker", "create"]
    assert flag_values(args, "--network") == ["none"]
    assert flag_values(args, "--mount") == [
        f"type=volume,src={NAMES['workspace']},dst=/workspace",
        f"type=volume,src={NAMES['task']},dst=/task",
    ]
    assert args[-1] == TOOLKIT


def test_gateway_has_limits_and_drains_for_the_planned_time():
    from hb.plan import GATEWAY_DRAIN_SECONDS
    args = profile.gateway_run_args(NAMES, COHORT, TOOLKIT, upstream_retries=3)
    assert flag_values(args, "--memory") == flag_values(args, "--memory-swap") == [profile.GATEWAY_MEMORY]
    assert flag_values(args, "--pids-limit") == [str(profile.GATEWAY_PIDS)]
    assert flag_values(args, "--drain-s") == [str(GATEWAY_DRAIN_SECONDS)]
    assert flag_values(args, "--ulimit") == [f"nofile={profile.GATEWAY_NOFILE}:{profile.GATEWAY_NOFILE}"]
    # each connection: one thread and at most three descriptors, so threads always run out first
    assert profile.GATEWAY_NOFILE >= 4 * profile.GATEWAY_PIDS
    assert flag_values(args, "--log-driver") == ["json-file"]
    assert sorted(flag_values(args, "--log-opt")) == ["max-file=2", "max-size=8m"]
    assert flag_values(args, "--mount") == [f"type=volume,src={NAMES['gateway_log']},dst=/log"]


def probe_args():
    return profile.probe_create_args(profile.probe_names("fix-pagination-off-by-one", 1), TOOLKIT,
                                     "probe.py", "inputs.json")


def test_probe_sandbox_has_no_network_and_a_read_only_tree():
    args = probe_args()
    names = profile.probe_names("fix-pagination-off-by-one", 1)
    assert flag_values(args, "--network") == ["none"]
    assert flag_values(args, "--mount") == [f"type=volume,src={names['volume']},dst=/probe,readonly"]
    assert "--read-only" in args
    assert flag_values(args, "--cap-drop") == ["ALL"]
    assert flag_values(args, "--security-opt") == ["no-new-privileges"]
    assert flag_values(args, "--user") == [profile.PROBE_USER]
    assert flag_values(args, "--env") == ["HOME=/tmp"]
    assert flag_values(args, "--log-driver") == ["none"]
    assert flag_values(args, "--memory") == flag_values(args, "--memory-swap") == [profile.PROBE_MEMORY]
    joined = " ".join(args)
    for text in ("docker.sock", "type=bind", "MODEL_API_KEY", "--privileged", "--cap-add"):
        assert text not in joined, text
    assert args[args.index(TOOLKIT) + 1:] == [
        "-I", "-B", "/probe/program/probe.py", "/probe/tree", "/probe/inputs/inputs.json"]


def test_names_are_distinct_per_rep_and_attempt():
    a, b, c = profile.rep_names("t", 1, 1), profile.rep_names("t", 1, 2), profile.rep_names("t", 2, 1)
    every = list(a.values()) + list(b.values()) + list(c.values())
    assert len(every) == len(set(every))
    assert set(profile.probe_names("t", 1).values()).isdisjoint(every)
