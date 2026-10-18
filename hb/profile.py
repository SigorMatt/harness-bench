"""Container profiles for one rep, as plain argument lists.

The isolation guarantees are properties of these arguments: the harness gets
an internal-only network whose one other member is the gateway, a placeholder
credential, no Docker socket, a read-only root, no capabilities and fixed
resource limits. Because the profile is data, it is verified by asserting on
the generated arguments (tests/test_profile.py), not by probing a container.
"""

from __future__ import annotations

GATEWAY_ALIAS = "gateway"
GATEWAY_PORT = 8080
HARNESS_USER = "10001:10001"
GATEWAY_USER = "10002:10002"
PLACEHOLDER_KEY = "hb-no-credential"
HARNESS_ENV = ("HOME", "OPENAI_BASE_URL", "OPENAI_API_KEY", "HB_MODEL", "HB_TASK_FILE")


def rep_names(task_id: str, rep: int, attempt: int) -> dict:
    stem = f"hb-{task_id}-r{rep}a{attempt}"
    return {
        "network": f"{stem}-net",
        "gateway": f"{stem}-gw",
        "harness": f"{stem}-harness",
        "workspace": f"{stem}-ws",
        "task": f"{stem}-task",
        "gateway_log": f"{stem}-gwlog",
    }


def _cpus(milli: int) -> str:
    return f"{milli // 1000}.{milli % 1000:03d}"


def network_create_args(names: dict) -> list[str]:
    # --internal: containers on this network have no route outside it.
    return ["docker", "network", "create", "--internal", names["network"]]


def gateway_run_args(names: dict, cohort: dict, toolkit_image: str, upstream_retries: int) -> list[str]:
    budget, model = cohort["budget"], cohort["model"]
    return [
        "docker", "run", "--detach", "--name", names["gateway"],
        "--network", "bridge",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", GATEWAY_USER,
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


def harness_run_args(names: dict, cohort: dict, harness_image: str, entrypoint: list[str]) -> list[str]:
    runtime = cohort["runtime"]
    memory = f"{runtime['harness_memory_mib']}m"
    args = [
        "docker", "run", "--rm", "--name", names["harness"],
        "--network", names["network"],
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=1024m",
        "--user", HARNESS_USER,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--cpus", _cpus(runtime["harness_cpus_milli"]),
        "--memory", memory, "--memory-swap", memory,
        "--pids-limit", str(runtime["harness_pids"]),
        "--mount", f"type=volume,src={names['workspace']},dst=/workspace",
        "--mount", f"type=volume,src={names['task']},dst=/task,readonly",
        "--workdir", "/workspace",
    ]
    env = harness_env(cohort)
    for key in HARNESS_ENV:
        args += ["--env", f"{key}={env[key]}"]
    return args + [harness_image, *entrypoint]
