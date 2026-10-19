"""Container profiles for one rep, as plain argument lists.

The isolation guarantees are properties of these arguments: the harness gets
an internal-only network whose one other member is the gateway, a placeholder
credential, no Docker socket, a read-only root, no capabilities and fixed
resource limits. The probe sandbox gets no network at all and a read-only copy
of the patched tree. Because the profile is data, it is verified by asserting
on the generated arguments (tests/test_profile.py), not by probing a container.

Sibling containers are created through the host's Docker daemon, so data moves
through named volumes. A helper container mounts them and is created but never
started: trees are copied into it with `docker cp`, and the workspace is copied
out of it after the harness has gone (hb/transfer.py).
"""

from __future__ import annotations

from .plan import GATEWAY_DRAIN_SECONDS

GATEWAY_ALIAS = "gateway"
GATEWAY_PORT = 8080
HARNESS_USER = "10001:10001"
GATEWAY_USER = "10002:10002"
PROBE_USER = "10003:10003"
HARNESS_UID = 10001
PLACEHOLDER_KEY = "hb-no-credential"
HARNESS_ENV = ("HOME", "OPENAI_BASE_URL", "OPENAI_API_KEY", "HB_MODEL", "HB_TASK_FILE")

# Limits for the gateway. A harness can make it work hard (many connections,
# large bodies); these keep that from taking the runner down. Every connection
# holds one thread (counted by --pids-limit) and at most three descriptors
# (client, upstream, a DNS lookup). The descriptor limit is pinned well above
# what the thread limit allows, so a flood of connections only ever meets the
# thread limit, which turns new connections away before any request is
# admitted, never a failed upstream connect, which would void the attempt.
GATEWAY_MEMORY = "2048m"
GATEWAY_PIDS = 1024
GATEWAY_NOFILE = 8192
# Container output kept by Docker (harness.log, the gateway's stderr). Capped,
# so it cannot fill the runner's disk.
CAPPED_LOG_OPTIONS = ("--log-driver", "json-file", "--log-opt", "max-size=8m", "--log-opt", "max-file=2")
# Probes run the patched code under these limits, the same in the run and at the aggregator.
PROBE_CPUS = "1.000"
PROBE_MEMORY = "2048m"
PROBE_PIDS = 256


def rep_names(task_id: str, rep: int, attempt: int) -> dict:
    stem = f"hb-{task_id}-r{rep}a{attempt}"
    return {
        "network": f"{stem}-net",
        "gateway": f"{stem}-gw",
        "harness": f"{stem}-harness",
        "helper": f"{stem}-helper",
        "workspace": f"{stem}-ws",
        "task": f"{stem}-task",
        "gateway_log": f"{stem}-gwlog",
    }


def probe_names(task_id: str, rep: int) -> dict:
    stem = f"hb-{task_id}-r{rep}-probe"
    return {"volume": f"{stem}-vol", "helper": f"{stem}-helper", "sandbox": f"{stem}-box"}


def _cpus(milli: int) -> str:
    return f"{milli // 1000}.{milli % 1000:03d}"


def network_create_args(names: dict) -> list[str]:
    # --internal: containers on this network have no route outside it.
    return ["docker", "network", "create", "--internal", names["network"]]


def volume_create_args(name: str) -> list[str]:
    return ["docker", "volume", "create", name]


def helper_create_args(names: dict, toolkit_image: str) -> list[str]:
    """Holds the rep's workspace and task volumes for copying. Never started.

    A new named volume is initialised from the image's directory at its mount
    point, so the toolkit image's /workspace (owned by the harness user) gives
    the workspace volume its owner.
    """
    return [
        "docker", "create", "--name", names["helper"], "--network", "none",
        "--mount", f"type=volume,src={names['workspace']},dst=/workspace",
        "--mount", f"type=volume,src={names['task']},dst=/task",
        toolkit_image,
    ]


def gateway_run_args(names: dict, cohort: dict, toolkit_image: str, upstream_retries: int) -> list[str]:
    budget, model = cohort["budget"], cohort["model"]
    return [
        "docker", "run", "--detach", "--name", names["gateway"],
        "--network", "bridge",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", GATEWAY_USER,
        "--memory", GATEWAY_MEMORY, "--memory-swap", GATEWAY_MEMORY, "--pids-limit", str(GATEWAY_PIDS),
        "--ulimit", f"nofile={GATEWAY_NOFILE}:{GATEWAY_NOFILE}",
        *CAPPED_LOG_OPTIONS,
        "--mount", f"type=volume,src={names['gateway_log']},dst=/log",
        # Name only: Docker copies the value from the toolkit's environment,
        # so the key never appears in an argument list or a log line.
        "--env", "MODEL_API_KEY",
        toolkit_image, "gateway",
        "--listen", f"0.0.0.0:{GATEWAY_PORT}",
        "--upstream", model["base_url"],
        "--model", model["model_id"],
        "--max-total-tokens", str(budget["total_tokens"]),
        "--max-calls", str(budget["model_calls"]),
        "--upstream-retries", str(upstream_retries),
        "--drain-s", str(GATEWAY_DRAIN_SECONDS),
        "--log", "/log/gateway.jsonl",
    ]


def gateway_connect_args(names: dict) -> list[str]:
    return ["docker", "network", "connect", "--alias", GATEWAY_ALIAS, names["network"], names["gateway"]]


def harness_env(cohort: dict) -> dict:
    return {
        "HOME": "/tmp",
        "OPENAI_BASE_URL": f"http://{GATEWAY_ALIAS}:{GATEWAY_PORT}/v1",
        "OPENAI_API_KEY": PLACEHOLDER_KEY,
        "HB_MODEL": cohort["model"]["model_id"],
        "HB_TASK_FILE": "/task/instruction.md",
    }


def harness_create_args(names: dict, cohort: dict, harness_image: str, entrypoint: list[str]) -> list[str]:
    """Created, then started by the toolkit. Not removed on exit, so its exit
    status and output can be read after it stops; the toolkit removes it."""
    runtime = cohort["runtime"]
    memory = f"{runtime['harness_memory_mib']}m"
    args = [
        "docker", "create", "--name", names["harness"],
        "--network", names["network"],
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=1024m",
        "--user", HARNESS_USER,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--cpus", _cpus(runtime["harness_cpus_milli"]),
        "--memory", memory, "--memory-swap", memory,
        "--pids-limit", str(runtime["harness_pids"]),
        *CAPPED_LOG_OPTIONS,
        "--mount", f"type=volume,src={names['workspace']},dst=/workspace",
        "--mount", f"type=volume,src={names['task']},dst=/task,readonly",
        "--workdir", "/workspace",
    ]
    env = harness_env(cohort)
    for key in HARNESS_ENV:
        args += ["--env", f"{key}={env[key]}"]
    return args + [harness_image, *entrypoint]


def probe_helper_args(names: dict, toolkit_image: str) -> list[str]:
    """Holds the probe volume while the tree, probe and inputs are copied in. Never started."""
    return [
        "docker", "create", "--name", names["helper"], "--network", "none",
        "--mount", f"type=volume,src={names['volume']},dst=/probe",
        toolkit_image,
    ]


def probe_create_args(names: dict, toolkit_image: str, program: str, inputs: str) -> list[str]:
    """The grading sandbox: no network, the probe volume read-only, fixed limits.

    `program` and `inputs` are file names inside /probe/program and /probe/inputs.
    No log driver: the probe's report is read from the attached stdout, and
    whatever else the code under test writes is discarded.
    """
    return [
        "docker", "create", "--name", names["sandbox"],
        "--network", "none",
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
        "--user", PROBE_USER,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--cpus", PROBE_CPUS, "--memory", PROBE_MEMORY, "--memory-swap", PROBE_MEMORY,
        "--pids-limit", str(PROBE_PIDS),
        "--log-driver", "none",
        "--mount", f"type=volume,src={names['volume']},dst=/probe,readonly",
        "--workdir", "/tmp",
        "--env", "HOME=/tmp",
        "--entrypoint", "python3",
        toolkit_image,
        "-I", "-B", f"/probe/program/{program}", "/probe/tree", f"/probe/inputs/{inputs}",
    ]
