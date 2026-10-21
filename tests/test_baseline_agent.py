"""The baseline harness (harnesses/baseline/agent.py) against a scripted OpenAI-compatible stub.

The agent runs as a subprocess, as in its image, in a temporary repository; the
stub answers each request with the next scripted reply and records what it got.
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "harnesses" / "baseline" / "agent.py"
CONFIG = ROOT / "harnesses" / "baseline" / "config" / "agent.json"


def tool_call(name: str, arguments, call_id: str = "call-1") -> dict:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def reply(*calls, content=None, finish_reason=None) -> tuple[int, dict]:
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = list(calls)
    finish_reason = finish_reason or ("tool_calls" if calls else "stop")
    return 200, {"choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]}


class Stub:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                status, payload = stub.replies.pop(0) if stub.replies else (500, {"error": "no reply left"})
                if payload == "broken body":  # promises more bytes than it sends, then hangs up
                    self.send_response(200)
                    self.send_header("Content-Length", "1000")
                    self.end_headers()
                    self.wfile.write(b'{"choices"')
                    self.close_connection = True
                    return
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"


@pytest.fixture
def run(tmp_path):
    servers = []

    def run_agent(replies, *, config_changes=None, timeout=60):
        stub = Stub(replies)
        servers.append(stub)
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        task = tmp_path / "instruction.md"
        task.write_text("Fix the bug.\n")
        config = {**json.loads(CONFIG.read_text()), **(config_changes or {})}
        config_path = tmp_path / "agent.json"
        config_path.write_text(json.dumps(config))
        env = {"PATH": "/usr/bin:/bin", "HB_TASK_FILE": str(task), "HB_MODEL": "the-model",
               "OPENAI_BASE_URL": stub.url, "OPENAI_API_KEY": "placeholder", "HB_BASELINE_CONFIG": str(config_path)}
        # Its stdin is a pipe held open and never written: a command that inherited it would wait forever.
        read_end, write_end = os.pipe()
        try:
            proc = subprocess.run([sys.executable, str(AGENT)], cwd=repo, env=env, stdin=read_end,
                                  capture_output=True, text=True, timeout=timeout, check=False)
        finally:
            os.close(read_end)
            os.close(write_end)
        return proc, stub, repo

    yield run_agent
    for stub in servers:
        stub.server.shutdown()


def tool_results(stub, n: int) -> list[str]:
    """The tool messages the agent sent with request n (0-based)."""
    return [m["content"] for m in stub.requests[n]["body"]["messages"] if m["role"] == "tool"]


def test_the_request_follows_the_adapter_contract_and_the_stock_configuration(run):
    proc, stub, _ = run([reply(content="Nothing to do.")])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    request = stub.requests[0]
    config = json.loads(CONFIG.read_text())
    assert request["path"] == "/v1/chat/completions" and request["auth"] == "Bearer placeholder"
    body = request["body"]
    assert body["model"] == "the-model" and body["tool_choice"] == "auto"
    assert (body["temperature"], body["top_p"], body["max_tokens"]) == (config["temperature"], config["top_p"],
                                                                         config["max_tokens"])
    assert [t["function"]["name"] for t in body["tools"]] == ["bash", "read_file", "write_file", "replace", "finish"]
    assert body["messages"] == [{"role": "system", "content": config["system_prompt"]},
                                {"role": "user", "content": "Fix the bug.\n"}]
    assert "answered without a tool call" in proc.stdout


def test_tools_change_the_repository_and_their_results_go_back(run):
    proc, stub, repo = run([
        reply(tool_call("write_file", {"path": "pkg/mod.py", "content": "x = 1\n"})),
        reply(tool_call("replace", {"path": "pkg/mod.py", "old": "x = 1", "new": "x = 2"}, "call-2"),
              tool_call("read_file", {"path": "pkg/mod.py"}, "call-3")),
        reply(tool_call("bash", {"command": "cat pkg/mod.py; echo err >&2; exit 3"}, "call-4")),
        reply(tool_call("finish", {"summary": "set x"}, "call-5")),
    ])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (repo / "pkg" / "mod.py").read_text() == "x = 2\n"
    assert len(stub.requests) == 4  # finish ends the run: no fifth request
    assert tool_results(stub, 1) == ["wrote 6 characters to pkg/mod.py"]
    assert tool_results(stub, 2)[1:] == ["replaced one occurrence in pkg/mod.py", "     1  x = 2"]
    assert tool_results(stub, 3)[-1] == "exit status 3\nx = 2\nerr\n"
    second = stub.requests[1]["body"]["messages"]
    assert second[2]["role"] == "assistant" and second[2]["tool_calls"][0]["id"] == "call-1"
    assert second[3] == {"role": "tool", "tool_call_id": "call-1", "content": "wrote 6 characters to pkg/mod.py"}
    assert "finish: set x" in proc.stdout


def test_tool_errors_are_reported_to_the_model_not_raised(run):
    proc, stub, repo = run([
        reply(tool_call("replace", {"path": "missing.py", "old": "a", "new": "b"}),
              tool_call("read_file", "{not json", "call-2"),
              tool_call("write_file", {"path": "only-path.py"}, "call-3"),
              tool_call("delete_everything", {}, "call-4"),
              tool_call("read_file", "[1, 2]", "call-5")),
        reply(tool_call("write_file", {"path": "twice.py", "content": "a\na\n"}, "call-6")),
        reply(tool_call("replace", {"path": "twice.py", "old": "a", "new": "b"}, "call-7"),
              tool_call("replace", {"path": "twice.py", "old": "", "new": "b"}, "call-8")),
        reply(content="Giving up."),
    ])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    first = tool_results(stub, 1)
    assert first[0].startswith("error: ") and "missing.py" in first[0]
    assert first[1:] == ["error: the arguments are not valid JSON", "error: missing argument 'content'",
                         "error: there is no tool named 'delete_everything'",
                         "error: the arguments must be a JSON object"]
    assert tool_results(stub, 3)[-2:] == ["error: old occurs 2 times in twice.py; it must occur exactly once",
                                          "error: old occurs 0 times in twice.py; it must occur exactly once"]
    assert (repo / "twice.py").read_text() == "a\na\n"


def test_long_output_is_shortened_from_the_middle(run):
    proc, stub, _ = run([reply(tool_call("bash", {"command": "python3 -c \"print('a' * 600 + 'b' * 600)\""})),
                         reply(content="done")], config_changes={"max_tool_output_chars": 200})
    assert proc.returncode == 0
    result = tool_results(stub, 1)[0]
    assert result.startswith("exit status 0\n" + "a" * 100) and result.rstrip().endswith("b" * 99)
    assert "[... 1001 bytes left out ...]" in result and len(result) < 300


def test_a_command_that_hangs_is_killed_with_everything_it_started(run):
    proc, stub, repo = run([reply(tool_call("bash", {"command": "sleep 60 & echo $! > bg.pid; sleep 60"})),
                            reply(content="done")], config_changes={"command_timeout_s": 1})
    assert proc.returncode == 0
    assert tool_results(stub, 1)[0].startswith("killed after 1 seconds")
    background = int((repo / "bg.pid").read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and Path(f"/proc/{background}").exists():
        time.sleep(0.1)  # killed with its group, so it is gone (and reaped by init) at once
    assert not Path(f"/proc/{background}").exists() or "Z" in Path(f"/proc/{background}/stat").read_text().split()[2]


def test_a_spent_budget_ends_the_run_at_once(run):
    proc, stub, _ = run([reply(tool_call("bash", {"command": "true"})), (429, {"error": "budget"})])
    assert proc.returncode == 0
    assert len(stub.requests) == 2 and "budget spent" in proc.stdout


@pytest.mark.parametrize("status", [400, 500, 502, 504])
def test_any_other_gateway_error_ends_the_run_with_status_2(run, status):
    proc, stub, _ = run([(status, {"error": "x"})])
    assert proc.returncode == 2 and len(stub.requests) == 1
    assert f"HTTP {status}" in proc.stdout


def test_a_malformed_answer_ends_the_run_with_status_2(run):
    proc, _, _ = run([(200, {"choices": []})])
    assert proc.returncode == 2 and "no message" in proc.stdout


def test_the_step_limit_ends_the_run(run):
    proc, stub, _ = run([reply(tool_call("bash", {"command": "true"}, f"call-{n}")) for n in range(5)],
                        config_changes={"max_steps": 3})
    assert proc.returncode == 0 and len(stub.requests) == 3
    assert "stopped after 3 steps" in proc.stdout


def test_the_assistant_turn_is_repeated_with_null_content_and_its_tool_calls(run):
    proc, stub, _ = run([reply(tool_call("bash", {"command": "true"})), reply(content="done")])
    assert proc.returncode == 0
    assistant = stub.requests[1]["body"]["messages"][2]
    assert assistant == {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call-1", "type": "function", "function": {"name": "bash", "arguments": '{"command": "true"}'}}]}


def test_malformed_tool_calls_are_answered_and_repeated_well_formed(run):
    no_id = {"type": "function", "function": {"name": "write_file", "arguments": {"path": "a.txt", "content": "A"}}}
    number = {"id": 7, "type": "function", "function": {"arguments": 5}}
    bad_json = tool_call("bash", "{oops", "call-3")
    proc, stub, repo = run([reply(no_id, number, bad_json, "not a call"), reply(content="done")])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (repo / "a.txt").read_text() == "A"  # arguments sent as an object are taken as they are
    messages = stub.requests[1]["body"]["messages"]
    echoed = messages[2]["tool_calls"]
    assert [c["id"] for c in echoed] == ["call_1_1", "call_1_2", "call-3", "call_1_4"]
    assert [json.loads(c["function"]["arguments"]) for c in echoed] == [{"path": "a.txt", "content": "A"}, {}, {}, {}]
    assert [c["function"]["name"] for c in echoed] == ["write_file", "unknown", "bash", "unknown"]
    results = [(m["tool_call_id"], m["content"]) for m in messages[3:]]
    assert results == [("call_1_1", "wrote 1 characters to a.txt"),
                       ("call_1_2", "error: the arguments must be a JSON object"),
                       ("call-3", "error: the arguments are not valid JSON"),
                       ("call_1_4", "error: malformed tool call")]


def test_an_answer_cut_off_at_the_length_limit_is_not_the_end(run):
    proc, stub, _ = run([reply(content='{"name": "write_fi', finish_reason="length"), reply(content="done")])
    assert proc.returncode == 0 and len(stub.requests) == 2
    config = json.loads(CONFIG.read_text())
    assert stub.requests[1]["body"]["messages"][-1] == {"role": "user", "content": config["cut_off_prompt"]}
    assert "cut off at the length limit" in proc.stdout


def test_a_large_file_can_be_written_in_parts(run):
    proc, _, repo = run([reply(tool_call("write_file", {"path": "big.py", "content": "a = 1\n"}),
                               tool_call("write_file", {"path": "big.py", "content": "b = 2\n", "append": True},
                                         "call-2")),
                         reply(content="done")])
    assert proc.returncode == 0 and (repo / "big.py").read_text() == "a = 1\nb = 2\n"


def test_read_file_takes_a_line_range(run):
    proc, stub, _ = run([reply(tool_call("write_file", {"path": "f.txt", "content": "one\ntwo\nthree\n"})),
                            reply(tool_call("read_file", {"path": "f.txt", "start_line": 2, "end_line": 3},
                                            "call-2")),
                            reply(content="done")])
    assert proc.returncode == 0
    assert tool_results(stub, 2)[-1] == "     2  two\n     3  three"


def test_commands_get_no_input(run):
    proc, stub, _ = run([reply(tool_call("bash", {"command": "read x; echo \"got:[$x]\""})), reply(content="done")],
                        config_changes={"command_timeout_s": 10})
    assert proc.returncode == 0
    assert tool_results(stub, 1)[0] == "exit status 0\ngot:[]\n"  # read meets end of input at once


def test_runaway_output_is_read_and_dropped_not_kept(run):
    proc, stub, _ = run([reply(tool_call("bash", {"command": "head -c 50000000 /dev/zero | tr \"\\\\0\" x"})),
                         reply(content="done")], config_changes={"max_tool_output_chars": 100})
    assert proc.returncode == 0
    result = tool_results(stub, 1)[0]
    assert result == "exit status 0\n" + "x" * 50 + "\n[... 49999900 bytes left out ...]\n" + "x" * 50


def test_a_process_that_left_the_group_does_not_hold_the_agent(run):
    started = time.monotonic()
    proc, stub, _ = run([reply(tool_call("bash", {"command": "setsid sleep 60 & sleep 60"})), reply(content="done")],
                        config_changes={"command_timeout_s": 1})
    assert proc.returncode == 0
    assert time.monotonic() - started < 30  # the timeout, then at most a short wait for the pipe
    assert tool_results(stub, 1)[0].startswith("killed after 1 seconds")


def test_a_broken_answer_body_ends_the_run_with_status_2(run):
    proc, _, _ = run([(200, "broken body")])
    assert proc.returncode == 2 and "Traceback" not in proc.stderr
    assert "no answer from the gateway" in proc.stdout


def test_the_models_own_arguments_are_repeated_as_it_wrote_them(run):
    raw = '{"path":  "café.txt", "content": "é"}'  # odd spacing and non-ASCII, as a model may write
    proc, stub, repo = run([reply(tool_call("write_file", raw)), reply(content="done")])
    assert proc.returncode == 0 and (repo / "café.txt").read_text() == "é"
    assert stub.requests[1]["body"]["messages"][2]["tool_calls"][0]["function"]["arguments"] == raw


def test_a_tool_call_cut_off_at_the_length_limit_gets_the_hint_too(run):
    cut = tool_call("write_file", '{"path": "big.py", "content": "def f():\\n    re')
    proc, stub, _ = run([reply(cut, finish_reason="length"), reply(content="done")])
    assert proc.returncode == 0
    messages = stub.requests[1]["body"]["messages"]
    assert messages[-2]["content"] == "error: the arguments are not valid JSON"
    assert messages[-1] == {"role": "user", "content": json.loads(CONFIG.read_text())["cut_off_prompt"]}


def test_output_is_cut_on_utf8_boundaries(run):
    proc, stub, _ = run([reply(tool_call("bash", {"command": "python3 -c \"print('é' * 5000)\""})),
                         reply(content="done")], config_changes={"max_tool_output_chars": 101})
    assert proc.returncode == 0
    result = tool_results(stub, 1)[0]
    assert "�" not in result and "bytes left out" in result
    assert result.startswith("exit status 0\n" + "é" * 25 + "\n[")
