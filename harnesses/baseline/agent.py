"""baseline: the reference harness (spec §6.4).

A minimal tool-calling agent, standard library only, so the leaderboard has a
floor that this project controls and the whole chain can be tested without
anyone else's harness. It follows the adapter contract (spec §6): the
instruction from $HB_TASK_FILE, the repository as the working directory, and
the OpenAI-compatible endpoint at $OPENAI_BASE_URL with model $HB_MODEL. Its
stock configuration is config/agent.json.

The loop: send the conversation with five tools (bash, read_file, write_file,
replace, finish), run every tool call the model makes, send the results back,
and stop when the model answers without a tool call, calls finish, or the step
limit is reached. An answer cut off at the length limit, with or without a tool
call in it, is not an end: the model is asked to go on in shorter replies. A budget refusal from the gateway
(HTTP 429) stops it at once; any other gateway error ends it with exit status 2.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get("HB_BASELINE_CONFIG", "/hb/config/agent.json"))


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required}}}


TEXT = {"type": "string"}
LINE = {"type": "integer"}
TOOLS = [
    _tool("bash", "Run a shell command in the repository root and return its exit status and output "
          "(stdout and stderr together, shortened if long). It has no network and no input, and "
          "whatever it starts in the background ends with it.",
          {"command": TEXT}, ["command"]),
    _tool("read_file", "Read a text file with line numbers, optionally only lines start_line to end_line.",
          {"path": TEXT, "start_line": LINE, "end_line": LINE}, ["path"]),
    _tool("write_file", "Create a file or replace its whole content; with append true, add content to its end.",
          {"path": TEXT, "content": TEXT, "append": {"type": "boolean"}}, ["path", "content"]),
    _tool("replace", "Replace one exact occurrence of old with new in a file. Fails unless old occurs exactly once.",
          {"path": TEXT, "old": TEXT, "new": TEXT}, ["path", "old", "new"]),
    _tool("finish", "Call when the task is done, with a one-line summary.", {"summary": TEXT}, ["summary"]),
]


class BudgetSpent(Exception):
    """The gateway refused the request because the rep's budget is spent."""


class GatewayError(Exception):
    """Any other failure to get an answer."""


def log(text: str) -> None:
    print(f"[baseline] {text}", flush=True)


def chat(messages: list[dict], config: dict) -> tuple[dict, str]:
    """The assistant message and its finish reason."""
    body = {"model": os.environ["HB_MODEL"], "messages": messages, "tools": TOOLS, "tool_choice": "auto",
            "temperature": config["temperature"], "top_p": config["top_p"], "max_tokens": config["max_tokens"]}
    request = urllib.request.Request(
        os.environ["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
    try:
        with urllib.request.urlopen(request, timeout=config["request_timeout_s"]) as response:
            reply = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise BudgetSpent() from None
        raise GatewayError(f"HTTP {exc.code}") from None
    except (OSError, ValueError, http.client.HTTPException) as exc:  # connection, timeout, a broken or non-JSON body
        raise GatewayError(str(exc) or type(exc).__name__) from None
    try:
        choice = reply["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        raise GatewayError("the answer has no message") from None
    if not isinstance(message, dict):
        raise GatewayError("the answer has no message")
    return message, str(choice.get("finish_reason"))


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n[... {len(text) - 2 * half} characters left out ...]\n{text[-half:]}"


def run_bash(command: str, config: dict) -> str:
    """Run a command in its own process group, keeping only the start and the end of its output.

    The group is killed when the command ends or at the timeout, so whatever it started ends too;
    output past what is kept is read and dropped, so a runaway command cannot
    fill the agent's memory.
    """
    limit = config["max_tool_output_chars"]
    process = subprocess.Popen(["bash", "-c", command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    head, tail, total = bytearray(), bytearray(), [0]

    def drain():
        while chunk := os.read(process.stdout.fileno(), 65536):
            total[0] += len(chunk)
            room = limit - len(head)
            head.extend(chunk[:room])
            tail.extend(chunk[max(room, 0):])
            del tail[:-limit]

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        process.wait(timeout=config["command_timeout_s"])
        status = f"exit status {process.returncode}"
        try:
            os.killpg(process.pid, signal.SIGKILL)  # what it left running in the background
        except ProcessLookupError:
            pass
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        status = f"killed after {config['command_timeout_s']} seconds"
    reader.join(timeout=5)  # a process that left the group may still hold the pipe: do not wait for it
    data = head + tail if total[0] <= len(head) + len(tail) else None
    if data is not None and len(data) <= limit:
        output = data.decode(errors="replace")
    else:
        half = limit // 2
        start = _utf8_prefix(bytes(head[:half]))
        end = _utf8_suffix(bytes((data if data is not None else tail)[-half:]))
        output = f"{start.decode(errors='replace')}\n[... {total[0] - len(start) - len(end)} bytes left out ...]\n" \
                 f"{end.decode(errors='replace')}"
    return f"{status}\n{output}"


def _utf8_prefix(data: bytes) -> bytes:
    """data without a UTF-8 sequence cut off at its end."""
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte & 0xC0 != 0x80:  # the lead byte of the last sequence (or ASCII)
            needed = 1 if byte < 0x80 else 2 if byte >> 5 == 0b110 else 3 if byte >> 4 == 0b1110 else 4
            return data if needed <= back else data[:-back]
    return data


def _utf8_suffix(data: bytes) -> bytes:
    """data without the continuation bytes of a UTF-8 sequence cut off at its start."""
    skip = 0
    while skip < min(3, len(data)) and data[skip] & 0xC0 == 0x80:
        skip += 1
    return data[skip:]


def read_file(args: dict, config: dict) -> str:
    lines = Path(args["path"]).read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(int(args.get("start_line") or 1), 1)
    end = min(int(args.get("end_line") or len(lines)), len(lines))
    numbered = "\n".join(f"{n:>6}  {lines[n - 1]}" for n in range(start, end + 1))
    return shorten(numbered or "(no lines)", config["max_tool_output_chars"])


def write_file(args: dict) -> str:
    path, content = Path(args["path"]), args["content"]
    if not isinstance(content, str):
        return "error: content must be a string"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if args.get("append") is True else "w", encoding="utf-8") as handle:
        handle.write(content)
    verb = "appended" if args.get("append") is True else "wrote"
    return f"{verb} {len(content)} characters to {args['path']}"


def replace(args: dict) -> str:
    path = Path(args["path"])
    text = path.read_text(encoding="utf-8")
    count = text.count(args["old"]) if args["old"] else 0
    if count != 1:
        return f"error: old occurs {count} times in {args['path']}; it must occur exactly once"
    path.write_text(text.replace(args["old"], args["new"], 1), encoding="utf-8")
    return f"replaced one occurrence in {args['path']}"


def run_tool(name: str, args: dict, config: dict) -> str:
    try:
        if name == "bash":
            return run_bash(args["command"], config)
        if name == "read_file":
            return read_file(args, config)
        if name == "write_file":
            return write_file(args)
        if name == "replace":
            return replace(args)
        return f"error: there is no tool named {name!r}"
    except KeyError as exc:
        return f"error: missing argument {exc}"
    except (OSError, ValueError, TypeError) as exc:
        return f"error: {exc}"


UNKNOWN_TOOL = "unknown"  # the name repeated for a call that has none


def parse_call(call, fallback_id: str) -> tuple[str, str, dict | str, str]:
    """(id, name, arguments, echo): arguments, or an error string in their place, and the arguments string
    the next request repeats.

    A call without a usable id gets fallback_id, so every tool result can name its call. The echo is the
    model's own string when it is a JSON object, so its call is repeated as it made it.
    """
    call = call if isinstance(call, dict) else {}
    call_id = call.get("id") if isinstance(call.get("id"), str) and call.get("id") else fallback_id
    function = call.get("function")
    if not isinstance(function, dict):
        return call_id, UNKNOWN_TOOL, "error: malformed tool call", "{}"
    name = function.get("name") if isinstance(function.get("name"), str) and function.get("name") else UNKNOWN_TOOL
    raw = function.get("arguments")
    if isinstance(raw, dict):  # some servers send the object itself
        return call_id, name, raw, json.dumps(raw, ensure_ascii=False)
    if raw is not None and not isinstance(raw, str):
        return call_id, name, "error: the arguments must be a JSON object", "{}"
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return call_id, name, "error: the arguments are not valid JSON", "{}"
    if not isinstance(args, dict):
        return call_id, name, "error: the arguments must be a JSON object", "{}"
    return call_id, name, args, raw or "{}"


def echoed_call(call_id: str, name: str, args: dict | str, echo: str) -> dict:
    """The tool call as the next request repeats it: always a well-formed call, whatever the model sent."""
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": echo}}


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    instruction = Path(os.environ["HB_TASK_FILE"]).read_text(encoding="utf-8")
    messages = [{"role": "system", "content": config["system_prompt"]}, {"role": "user", "content": instruction}]
    for step in range(1, config["max_steps"] + 1):
        try:
            message, finish_reason = chat(messages, config)
        except BudgetSpent:
            log(f"step {step}: the gateway refused the request: budget spent")
            return 0
        except GatewayError as exc:
            log(f"step {step}: no answer from the gateway: {exc}")
            return 2
        content = message.get("content") if isinstance(message.get("content"), str) else None
        raw_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        if not raw_calls:
            messages.append({"role": "assistant", "content": content or ""})
            if finish_reason == "length":
                messages.append({"role": "user", "content": config["cut_off_prompt"]})
                log(f"step {step}: the answer was cut off at the length limit; asked to go on")
                continue
            log(f"step {step}: answered without a tool call; done")
            return 0
        calls = [parse_call(call, f"call_{step}_{n}") for n, call in enumerate(raw_calls, 1)]
        messages.append({"role": "assistant", "content": content,
                         "tool_calls": [echoed_call(*call) for call in calls]})
        for call_id, name, args, _ in calls:
            if name == "finish" and isinstance(args, dict):
                log(f"step {step}: finish: {shorten(str(args.get('summary', '')), 200)}")
                return 0
            result = args if isinstance(args, str) else run_tool(name, args, config)
            log(f"step {step}: {name}: {shorten(result.splitlines()[0] if result else '', 120)}")
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
        if finish_reason == "length":  # a tool call cut off mid-way: its arguments did not parse
            messages.append({"role": "user", "content": config["cut_off_prompt"]})
            log(f"step {step}: the answer was cut off at the length limit; asked to go on")
    log(f"stopped after {config['max_steps']} steps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
