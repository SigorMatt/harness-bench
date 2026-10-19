"""The toolkit command line: inputs come from the environment and are checked before anything runs."""

import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from hb import cli

ROOT = Path(__file__).resolve().parent.parent
SUITE_DIR = ROOT / "examples" / "suite"
REGISTRY = ROOT / "examples" / "registry.json"
ENV = {
    "HARNESS_ID": "baseline",
    "TASK_ID": "fix-pagination-off-by-one",
    "HB_REPOSITORY": "example-user/hb-runs",
    "HB_RUN_ID": "18000000001",
    "HB_RUN_ATTEMPT": "1",
    "HB_TOOLKIT_IMAGE": "ghcr.io/sigormatt/hb-toolkit@sha256:" + "d" * 64,
    "MODEL_API_KEY": "secret",
}


def test_job_from_env():
    job = cli.job_from_env(ENV, SUITE_DIR, REGISTRY)
    registry = json.loads(REGISTRY.read_text())
    assert job.harness == registry["harnesses"][0]
    assert job.task_id == ENV["TASK_ID"]
    assert job.run == {"repository": "example-user/hb-runs", "run_id": 18000000001, "run_attempt": 1}
    assert job.toolkit_image == ENV["HB_TOOLKIT_IMAGE"]
    assert job.suite_dir == SUITE_DIR.resolve()


@pytest.mark.parametrize("name,value", [
    ("HARNESS_ID", ""), ("HARNESS_ID", "not-registered"), ("HARNESS_ID", "Bad Id"),
    ("TASK_ID", "no-such-task"), ("TASK_ID", "../etc"),
    ("HB_REPOSITORY", "no-slash"), ("HB_RUN_ID", "0"), ("HB_RUN_ID", "12x"), ("HB_RUN_ATTEMPT", ""),
    ("HB_TOOLKIT_IMAGE", "ghcr.io/sigormatt/hb-toolkit:latest"), ("MODEL_API_KEY", ""),
])
def test_bad_inputs_are_refused(name, value):
    with pytest.raises(cli.InputError):
        cli.job_from_env({**ENV, name: value}, SUITE_DIR, REGISTRY)


def test_unknown_command_and_bad_inputs_exit_2(tmp_path, capsys):
    assert cli.main([]) == 2
    assert cli.main(["seal"]) == 2
    env_free = subprocess.run(
        [sys.executable, "-m", "hb", "run-task", "--suite", str(SUITE_DIR), "--out", str(tmp_path),
         "--registry", str(REGISTRY)],
        capture_output=True, text=True, cwd=ROOT, env={"PATH": "/usr/bin:/bin"}, check=False)
    assert env_free.returncode == 2
    assert "HARNESS_ID is missing or malformed" in env_free.stderr


def test_gateway_command_reaches_the_gateway():
    proc = subprocess.run([sys.executable, "-m", "hb", "gateway", "--help"], capture_output=True, text=True,
                          cwd=ROOT, check=False)
    assert proc.returncode == 0
    assert "harnessbench model gateway" in proc.stdout


def test_termination_signal_raises_once_so_cleanup_can_finish():
    saved = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        with pytest.raises(SystemExit) as exit_info:
            cli._terminate(signal.SIGTERM, None)
        assert exit_info.value.code == 128 + signal.SIGTERM
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_IGN
        assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTERM, saved[0])
        signal.signal(signal.SIGINT, saved[1])
