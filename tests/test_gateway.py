"""Functional tests for the model gateway.

The provider is a local stub that replays scripted responses (JSON, chunked
server-sent events, errors, slow answers, dropped connections), so no real
model is called and no tokens are spent. Every record the gateway writes is
validated against schemas/gateway-record.schema.json.
"""

import email.utils
import hashlib
import http.client
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hb import gateway
from hb.schemas import validate

ROOT = Path(__file__).resolve().parent.parent
MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"
KEY = "test-provider-key-7f3a"
POLL = 0.05


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


# ------------------------------------------------------------------ stub provider


class StubProvider:
    """Scripted OpenAI-compatible provider. Each POST consumes one scripted action."""

    def __init__(self, tls_context=None, host="127.0.0.1"):
        self.lock = threading.Lock()
        self.script = []
        self.requests = []  # (path, headers, body)
        self.sent = []  # response bodies as sent (dechunked)
        stub = self

        class StubHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                with stub.lock:
                    stub.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                    action = stub.script.pop(0) if stub.script else ("json", 500, {"error": "script empty"})
                self.close_connection = True
                getattr(self, "_" + action[0])(*action[1:])

            def _json(self, status, payload, headers=None):
                data = json.dumps(payload).encode()
                stub.sent.append(data)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def _slow(self, delay, status, payload):
                time.sleep(delay)
                try:
                    self._json(status, payload)
                except OSError:
                    pass

            def _raw_sse(self, chunks, truncate=False, pause_after_first=0.0):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                sent = b""
                try:
                    for position, data in enumerate(chunks):
                        sent += data
                        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                        self.wfile.flush()
                        if position == 0 and pause_after_first:
                            time.sleep(pause_after_first)
                    if not truncate:
                        self.wfile.write(b"0\r\n\r\n")
                except OSError:
                    pass  # the gateway stopped waiting and closed the connection
                finally:
                    stub.sent.append(sent)

            def _stall_json(self, delay, status, payload):
                """Send the headers and half the body, then go silent."""
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data[:len(data) // 2])
                self.wfile.flush()
                time.sleep(delay)
                try:
                    self.wfile.write(data[len(data) // 2:])
                except OSError:
                    pass

            def _sse(self, events, truncate=False, pause_after_first=0.0):
                chunks = [f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n".encode() for e in events]
                self._raw_sse(chunks, truncate, pause_after_first)

            def _drop(self):
                pass  # close without any response

        self.server = ThreadingHTTPServer((host, 0), StubHandler)
        if tls_context is not None:
            self.server.socket = tls_context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, args=(POLL,), daemon=True)
        self.thread.start()
        scheme = "https" if tls_context is not None else "http"
        name = "localhost" if tls_context is not None else host
        self.url = f"{scheme}://{name}:{self.server.server_address[1]}/v1/"

    def push(self, *actions):
        with self.lock:
            self.script.extend(actions)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def completion(prompt=100, completion_tokens=20, cached=None, tool_args=None, finish="stop"):
    message = {"role": "assistant", "content": "ok"}
    if tool_args is not None:
        message["tool_calls"] = [
            {"id": f"c{i}", "type": "function", "function": {"name": "edit", "arguments": a}}
            for i, a in enumerate(tool_args)
        ]
    usage = {"prompt_tokens": prompt, "completion_tokens": completion_tokens,
             "total_tokens": prompt + completion_tokens}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return {"id": "x", "object": "chat.completion", "model": MODEL,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}


def chunk(delta=None, finish=None, index=0):
    return {"id": "x", "object": "chat.completion.chunk",
            "choices": [{"index": index, "delta": delta or {}, "finish_reason": finish}]}


USAGE_CHUNK = {"id": "x", "object": "chat.completion.chunk", "choices": [],
               "usage": {"prompt_tokens": 50, "completion_tokens": 7, "total_tokens": 57,
                         "prompt_tokens_details": {"cached_tokens": 30}}}

STREAM = [
    chunk({"role": "assistant", "content": ""}),
    chunk({"content": "Looking at pager.py"}),
    chunk({"tool_calls": [{"index": 0, "id": "c0", "type": "function",
                           "function": {"name": "edit", "arguments": '{"pa'}}]}),
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'th": "pager.py"}'}}]}),
    chunk(finish="tool_calls"),
    USAGE_CHUNK,
    "[DONE]",
]

CHAT = {"model": "gpt-4o", "messages": [{"role": "user", "content": "fix the bug"}], "temperature": 0.2}


# ---------------------------------------------------------------------- gateway


class RunningGateway:
    def __init__(self, tmp_path, stub, **overrides):
        settings = dict(upstream=gateway.Upstream.parse(stub.url), model=MODEL, api_key=KEY,
                        max_total_tokens=1_000_000, max_calls=100, upstream_retries=2,
                        retry_base_delay_s=0.01)
        settings.update(overrides)
        self.log_path = tmp_path / "gateway.jsonl"
        self.server = gateway.build_server(gateway.Config(**settings), self.log_path, "127.0.0.1:0")
        self.gateway = self.server.gateway
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, args=(POLL,), daemon=True)
        self.thread.start()
        self.closed = False

    def request(self, method="POST", path="/v1/chat/completions", body=None, raw=None, headers=None,
                chunked=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        send_headers = {"Content-Type": "application/json", "Authorization": "Bearer hb-no-credential",
                        "X-Harness-Trace": "trace-123", **(headers or {})}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if chunked:
            conn.request(method, path, body=iter([data[:10], data[10:]]), headers=send_headers,
                         encode_chunked=True)
        else:
            conn.request(method, path, body=data, headers=send_headers)
        resp = conn.getresponse()
        out = resp.read()
        conn.close()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, out

    def send_raw(self, data: bytes) -> int:
        """Send hand-built request bytes; return the response status."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(data)
            response = b""
            while chunk_ := sock.recv(65536):
                response += chunk_
        return int(response.split(b" ", 2)[1])

    def lines(self):
        return self.log_path.read_text().splitlines() if self.log_path.exists() else []

    def records(self, expected):
        wait_for(lambda: len(self.lines()) >= expected)
        records = [json.loads(line) for line in self.lines()]
        assert len(records) == expected, records
        for record in records:
            validate("gateway-record", record)
        return sorted(records, key=lambda r: r["seq"])

    def close(self, drain_s=0.0):
        if self.closed:
            return 0
        self.closed = True
        return gateway.shutdown(self.server, drain_s)


@pytest.fixture
def stub():
    provider = StubProvider()
    yield provider
    provider.close()


@pytest.fixture
def make_gateway(tmp_path, stub):
    started = []

    def make(provider=None, **overrides):
        gw = RunningGateway(tmp_path, provider or stub, **overrides)
        started.append(gw)
        return gw

    yield make
    for gw in started:
        gw.close()


# ------------------------------------------------------------- forwarding


def test_request_is_forwarded_with_the_pinned_model_and_fixed_headers(stub, make_gateway):
    gw = make_gateway()
    reply = completion(prompt=100, completion_tokens=20, cached=40,
                       tool_args=['{"path": "pager.py"}', "{not json"], finish="tool_calls")
    stub.push(("json", 200, reply))

    status, _, body = gw.request(body=CHAT)

    assert status == 200 and json.loads(body) == reply
    path, headers, sent = stub.requests[0]
    assert path == "/v1/chat/completions"
    assert json.loads(sent) == {**CHAT, "model": MODEL}
    assert set(headers) == {"host", "accept-encoding", "content-length", "authorization", "content-type",
                            "accept", "user-agent"}
    assert headers["authorization"] == f"Bearer {KEY}" and headers["accept-encoding"] == "identity"
    [record] = gw.records(1)
    assert record["outcome"] == "ok" and record["http_status"] == 200
    assert record["model_rewritten"] is True and record["stream"] is False
    assert record["usage"] == {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}
    assert record["finish_reason"] == "tool_calls"
    assert record["tool_calls"] == {"count": 2, "arguments_valid_json": 1}
    assert record["upstream_attempts"] == 1
    assert record["totals"] == {"tokens": 120, "calls": 1}
    assert record["request_sha256"] == sha(sent)
    assert record["response_sha256"] == sha(stub.sent[0])


def test_non_ascii_and_lone_surrogates_are_forwarded_faithfully(stub, make_gateway):
    gw = make_gateway()
    stub.push(("json", 200, completion()))
    raw = b'{"model": "x", "messages": [{"role": "tool", "content": "caf\\u00e9 \\ud83d cut"}]}'

    assert gw.request(raw=raw)[0] == 200

    assert json.loads(stub.requests[0][2])["messages"][0]["content"] == "café \ud83d cut"
    assert gw.records(1)[0]["outcome"] == "ok"


def test_a_trailing_slash_and_a_chunked_body_are_accepted(stub, make_gateway):
    gw = make_gateway()
    stub.push(("json", 200, completion()))

    assert gw.request(path="/v1/chat/completions/", body=CHAT, chunked=True)[0] == 200
    assert json.loads(stub.requests[0][2])["model"] == MODEL


# --------------------------------------------------------------- streaming


def test_streaming_always_counts_usage_and_hides_it_when_not_requested(stub, make_gateway):
    gw = make_gateway()
    stub.push(("sse", STREAM))

    status, headers, body = gw.request(body={**CHAT, "model": MODEL, "stream": True})

    assert status == 200 and headers["content-type"].startswith("text/event-stream")
    assert json.loads(stub.requests[0][2])["stream_options"] == {"include_usage": True}
    events = [e for e in body.decode().split("\n\n") if e]
    assert len(events) == len(STREAM) - 1  # the usage-only chunk is removed
    assert '"usage"' not in body.decode() and events[-1] == "data: [DONE]"
    [record] = gw.records(1)
    assert record["outcome"] == "ok" and record["stream"] is True and record["model_rewritten"] is False
    assert record["usage"] == {"input_tokens": 50, "cached_input_tokens": 30, "output_tokens": 7}
    assert record["finish_reason"] == "tool_calls"
    assert record["tool_calls"] == {"count": 1, "arguments_valid_json": 1}
    assert record["response_sha256"] == sha(stub.sent[0])


def test_streaming_passes_usage_through_when_requested(stub, make_gateway):
    gw = make_gateway()
    stub.push(("sse", STREAM))
    options = {"include_usage": True, "continuous_usage_stats": False}

    _, _, body = gw.request(body={**CHAT, "stream": True, "stream_options": options})

    assert json.loads(stub.requests[0][2])["stream_options"] == options
    assert '"usage"' in body.decode()
    assert gw.records(1)[0]["usage"]["output_tokens"] == 7


def test_sse_framing_variants_are_relayed_and_counted(stub, make_gateway):
    gw = make_gateway()
    content = json.dumps(chunk({"content": "hi"}, finish="stop"))
    head, tail = content[:20], content[20:]
    stub.push(("raw_sse", [
        b": keep-alive\r\n\r\n",
        f"data: {head}\r\ndata: {tail}\r\n\r\n".encode(),  # one JSON value split over two data lines
        f"data: {json.dumps(USAGE_CHUNK)}\r\n".encode(),  # no blank line and no [DONE] before the end
    ]))

    status, _, body = gw.request(body={**CHAT, "stream": True})

    assert status == 200
    assert body.startswith(b": keep-alive\r\n\r\n") and b'"usage"' not in body
    [record] = gw.records(1)
    assert record["outcome"] == "ok" and record["finish_reason"] == "stop"
    assert record["usage"]["output_tokens"] == 7


def test_usage_on_the_last_content_chunk_is_counted_and_kept(stub, make_gateway):
    gw = make_gateway()
    final = chunk({"content": "done"}, finish="stop")
    final["usage"] = USAGE_CHUNK["usage"]
    stub.push(("sse", [chunk({"content": "hi"}), final, "[DONE]"]))

    _, _, body = gw.request(body={**CHAT, "stream": True})

    assert b'"usage"' in body  # it rides on a content chunk, so it is not removed
    [record] = gw.records(1)
    assert record["outcome"] == "ok" and record["usage"]["output_tokens"] == 7


def test_a_usage_chunk_without_a_choices_field_is_treated_as_usage_only(stub, make_gateway):
    gw = make_gateway()
    usage_only = {"id": "x", "object": "chat.completion.chunk", "usage": USAGE_CHUNK["usage"]}
    stub.push(("sse", [chunk({"content": "hi"}, finish="stop"), usage_only, "[DONE]"]))

    _, _, body = gw.request(body={**CHAT, "stream": True})

    assert b'"usage"' not in body
    assert gw.records(1)[0]["totals"]["tokens"] == 57


def test_a_gateway_fault_mid_stream_keeps_the_stream_intact(stub, make_gateway, monkeypatch):
    gw = make_gateway()
    stub.push(("sse", STREAM))

    def broken(self):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(gateway.StreamState, "finish_reason", broken)

    status, _, body = gw.request(body={**CHAT, "stream": True})

    assert status == 200 and b"HTTP/1." not in body  # no second status line inside the stream
    [record] = gw.records(1)
    assert record["outcome"] == "upstream_failed" and record["http_status"] == 200
    assert record["usage"]["output_tokens"] == 7  # what was counted is reported


def test_tokens_are_counted_when_the_client_leaves_mid_stream(stub, make_gateway):
    gw = make_gateway()
    stub.push(("sse", STREAM, False, 0.3))
    body = json.dumps({**CHAT, "stream": True}).encode()
    request = (b"POST /v1/chat/completions HTTP/1.1\r\nHost: gateway\r\nContent-Type: application/json\r\n"
               b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)

    with socket.create_connection(("127.0.0.1", gw.port), timeout=10) as sock:
        sock.sendall(request)
        received = b""
        while b"data:" not in received:
            received += sock.recv(4096)

    [record] = gw.records(1)
    assert record["outcome"] == "ok"
    assert record["usage"]["output_tokens"] == 7 and record["totals"]["tokens"] == 57


# --------------------------------------------------------- provider failures


def test_provider_failures_are_retried_before_any_byte_reaches_the_client(stub, make_gateway):
    gw = make_gateway(upstream_retries=3)
    stub.push(("json", 503, {"error": "busy"}), ("json", 408, {"error": "timeout"}),
              ("json", 429, {"error": "slow down"}, {"retry-after-ms": "20"}), ("json", 200, completion()))

    assert gw.request(body=CHAT)[0] == 200

    assert len(stub.requests) == 4
    [record] = gw.records(1)
    assert record["outcome"] == "ok" and record["upstream_attempts"] == 4


def test_a_connection_lost_after_the_full_request_is_a_void_without_a_retry(stub, make_gateway):
    gw = make_gateway(upstream_retries=3)
    stub.push(("drop",), ("json", 200, completion()))  # the provider read the whole request, then closed

    status, headers, _ = gw.request(body=CHAT)
    later = gw.request(body=CHAT)

    assert status == 502 and headers["x-should-retry"] == "false"
    assert len(stub.requests) == 1  # never sent twice: the provider may already be generating
    assert later[0] == 502 and len(stub.requests) == 1
    failed, local = gw.records(2)
    assert failed["outcome"] == "upstream_failed" and failed["upstream_attempts"] == 1
    assert local["outcome"] == "upstream_failed" and local["upstream_attempts"] == 0
    assert gw.close() == gateway.EXIT_OK  # it reached the provider: a void


def refused_port() -> int:
    """A local port with nothing listening: every connect to it is refused."""
    with socket.socket() as placeholder:
        placeholder.bind(("127.0.0.1", 0))
        return placeholder.getsockname()[1]


def test_a_provider_that_cannot_be_reached_fails_the_job_instead_of_voiding(tmp_path, capsys):
    # A connect that fails before the request is sent can be the harness's doing (it can
    # exhaust host resources the connection needs), so it must never become a re-roll.
    config = gateway.Config(upstream=gateway.Upstream.parse(f"http://127.0.0.1:{refused_port()}/v1/"), model=MODEL,
                            api_key=KEY, max_total_tokens=1000, max_calls=10, upstream_retries=2,
                            retry_base_delay_s=0.01)
    server = gateway.build_server(config, tmp_path / "gateway.jsonl", "127.0.0.1:0")
    threading.Thread(target=server.serve_forever, args=(POLL,), daemon=True).start()
    statuses = []
    try:
        for _ in range(2):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("POST", "/v1/chat/completions", body=json.dumps(CHAT),
                         headers={"Content-Type": "application/json"})
            statuses.append(conn.getresponse().status)
            conn.close()
    finally:
        exit_status = gateway.shutdown(server, 0)
    assert statuses == [502, 502]
    assert exit_status == gateway.EXIT_UNREACHABLE  # the toolkit fails the job on it
    first, later = sorted((json.loads(line) for line in (tmp_path / "gateway.jsonl").read_text().splitlines()),
                          key=lambda r: r["seq"])
    for record in (first, later):
        validate("gateway-record", record)
    assert first["outcome"] == "upstream_failed" and first["upstream_attempts"] == 3  # 1 + 2 retries
    assert later["outcome"] == "upstream_failed" and later["upstream_attempts"] == 0  # answered locally
    assert "the provider could not be reached: ConnectionRefusedError" in capsys.readouterr().err


def first_attempt_unreachable(gw):
    """Make the gateway's first upstream connection fail before anything is sent."""
    real, calls = gw.gateway.connection, []

    def connection():
        calls.append(1)
        if len(calls) == 1:
            return http.client.HTTPConnection("127.0.0.1", refused_port(), timeout=5)
        return real()

    gw.gateway.connection = connection


@pytest.mark.parametrize("then", [
    ("json", 503, {"error": "busy"}),    # retries run out on a provider answer
    ("json", 429, {"error": "slow down"}),
    ("json", 401, {"error": "bad key"}),  # a setup error
    ("drop",),                             # a connection lost after the full request was sent
])
def test_a_void_after_an_attempt_that_never_reached_the_provider_fails_the_job(stub, make_gateway, then):
    # The whole request counts: no void ever follows a connection the harness could have stopped.
    gw = make_gateway(upstream_retries=1)
    first_attempt_unreachable(gw)
    stub.push(then)

    assert gw.request(body=CHAT)[0] == 502

    [record] = gw.records(1)
    assert record["outcome"] == "upstream_failed" and record["upstream_attempts"] == 2
    assert len(stub.requests) == 1
    assert gw.close() == gateway.EXIT_UNREACHABLE


def test_a_failed_tls_handshake_is_a_provider_that_cannot_be_reached(stub, make_gateway):
    port = stub.server.server_address[1]  # a plain-http stub: every TLS handshake fails
    gw = make_gateway(upstream=gateway.Upstream.parse(f"https://localhost:{port}/v1/"), upstream_retries=1)

    assert gw.request(body=CHAT)[0] == 502

    assert gw.records(1)[0]["upstream_attempts"] == 2
    assert gw.close() == gateway.EXIT_UNREACHABLE


def test_the_exit_status_is_set_before_the_void_is_recorded(stub, make_gateway, monkeypatch):
    # A toolkit that sees the record and stops the gateway must always find status 3.
    gw = make_gateway(upstream_retries=0)
    first_attempt_unreachable(gw)
    seen, real_decide = [], gateway.Exchange._decide

    def decide(self, outcome, *args, **kwargs):
        if outcome == "upstream_failed":
            seen.append(self.gw.unreachable)
        return real_decide(self, outcome, *args, **kwargs)

    monkeypatch.setattr(gateway.Exchange, "_decide", decide)
    assert gw.request(body=CHAT)[0] == 502
    assert seen == [True]


def test_shutdown_during_the_backoff_after_an_unreachable_attempt_is_not_a_void(stub, make_gateway):
    gw = make_gateway(upstream_retries=1, retry_base_delay_s=10.0)
    first_attempt_unreachable(gw)
    worker, _ = background_request(gw, body=CHAT)
    assert wait_for(lambda: any(e.unreached for e in list(gw.gateway._inflight)))

    assert gw.close() == gateway.EXIT_OK  # the harness had gone; nothing was decided, nothing voids
    worker.join(timeout=5)
    [record] = gw.records(1)
    assert record["outcome"] == "usage_unknown" and len(stub.requests) == 0


def test_an_attempt_that_never_reached_the_provider_is_harmless_when_a_retry_succeeds(stub, make_gateway):
    gw = make_gateway(upstream_retries=1)
    first_attempt_unreachable(gw)
    stub.push(("json", 200, completion()))

    assert gw.request(body=CHAT)[0] == 200

    [record] = gw.records(1)
    assert record["outcome"] == "ok" and record["upstream_attempts"] == 2
    assert gw.close() == gateway.EXIT_OK


def test_streaming_requests_are_retried_too(stub, make_gateway):
    gw = make_gateway()
    stub.push(("json", 503, {"error": "busy"}), ("sse", STREAM))

    assert gw.request(body={**CHAT, "stream": True})[0] == 200
    record = gw.records(1)[0]
    assert record["outcome"] == "ok" and record["upstream_attempts"] == 2


@pytest.mark.parametrize("status", [503, 429])
def test_exhausted_retries_void_the_attempt(stub, make_gateway, status):
    gw = make_gateway(upstream_retries=1)
    stub.push(("json", status, {"error": "busy"}), ("json", status, {"error": "busy"}))

    first_status, headers, _ = gw.request(body=CHAT)
    later_status, _, later_body = gw.request(body=CHAT)

    assert first_status == 502 and headers["x-should-retry"] == "false"
    assert later_status == 502 and json.loads(later_body)["error"]["type"] == "upstream_failed"
    assert len(stub.requests) == 2  # the later request never reached the provider
    first, later = gw.records(2)
    assert first["outcome"] == "upstream_failed" and first["upstream_attempts"] == 2
    assert later["outcome"] == "upstream_failed" and later["upstream_attempts"] == 0
    assert gw.close() == gateway.EXIT_OK  # every attempt reached the provider: a void, re-run by the toolkit


def test_a_timeout_after_sending_is_neither_retried_nor_a_void(stub, make_gateway):
    gw = make_gateway(read_timeout_s=0.3)
    stub.push(("slow", 1.0, 200, completion()), ("json", 200, completion()))

    assert gw.request(body=CHAT)[0] == 504
    assert len(stub.requests) == 1  # not retried: the provider may still be generating
    assert gw.request(body=CHAT)[0] == 200  # how long a request takes is up to the harness: no void

    timed_out, ok = gw.records(2)
    assert timed_out["outcome"] == "usage_unknown" and timed_out["upstream_attempts"] == 1
    assert timed_out["usage"] == gateway.ZERO_USAGE and timed_out["response_sha256"] == sha(b"")
    assert ok["outcome"] == "ok"


@pytest.mark.parametrize("status", [200, 400])
def test_a_timeout_in_the_response_body_is_usage_unknown_not_a_void(stub, make_gateway, status):
    gw = make_gateway(read_timeout_s=0.3)
    stub.push(("stall_json", 1.0, status, completion()), ("json", 200, completion()))

    assert gw.request(body=CHAT)[0] == 504
    assert len(stub.requests) == 1  # not retried
    assert gw.request(body=CHAT)[0] == 200  # no void

    timed_out, ok = gw.records(2)
    assert timed_out["outcome"] == "usage_unknown" and timed_out["http_status"] == 504
    assert timed_out["usage"] == gateway.ZERO_USAGE and timed_out["totals"] == {"tokens": 0, "calls": 1}
    assert ok["outcome"] == "ok"


def test_a_stream_that_goes_silent_before_its_first_event_is_usage_unknown(stub, make_gateway):
    gw = make_gateway(read_timeout_s=0.3)
    stub.push(("raw_sse", [b"data: {", b'"choices": []}\n\n'], False, 1.0), ("json", 200, completion()))

    assert gw.request(body={**CHAT, "stream": True})[0] == 504
    assert gw.request(body=CHAT)[0] == 200

    timed_out, ok = gw.records(2)
    assert timed_out["outcome"] == "usage_unknown" and timed_out["http_status"] == 504
    assert ok["outcome"] == "ok"


def test_a_stream_that_goes_silent_mid_way_ends_with_an_error_and_is_usage_unknown(stub, make_gateway):
    gw = make_gateway(read_timeout_s=0.3)
    stub.push(("sse", STREAM, False, 1.0), ("json", 200, completion()))

    status, _, body = gw.request(body={**CHAT, "stream": True})

    assert status == 200 and len(stub.requests) == 1
    events = [e for e in body.decode().split("\n\n") if e]
    assert len(events) == 2  # the first event, then the error event
    assert json.loads(events[-1].removeprefix("data: "))["error"]["type"] == "upstream_timeout"
    assert gw.request(body=CHAT)[0] == 200  # no void
    timed_out, ok = gw.records(2)
    assert timed_out["outcome"] == "usage_unknown" and timed_out["http_status"] == 200
    assert timed_out["usage"] == gateway.ZERO_USAGE and not gw.gateway.ledger.void
    assert ok["outcome"] == "ok"


def test_a_stream_cut_off_mid_way_voids_the_attempt_without_a_retry(stub, make_gateway):
    gw = make_gateway()
    stub.push(("sse", STREAM[:3], True))

    status, _, body = gw.request(body={**CHAT, "stream": True})
    follow_up = gw.request(body=CHAT)  # sent as soon as the error event arrived

    assert status == 200 and len(stub.requests) == 1
    last_event = [e for e in body.decode().split("\n\n") if e][-1]
    assert json.loads(last_event.removeprefix("data: "))["error"]["type"] == "upstream_failed"
    assert follow_up[0] == 502 and len(stub.requests) == 1  # the void was in place first
    record, later = gw.records(2)
    assert record["outcome"] == "upstream_failed" and record["http_status"] == 200
    assert record["response_sha256"] == sha(stub.sent[0])
    assert record["totals"]["calls"] == 1 and later["upstream_attempts"] == 0


def test_a_stream_without_usage_voids_the_attempt(stub, make_gateway):
    gw = make_gateway()
    stub.push(("sse", [chunk({"content": "hi"}), chunk(finish="stop"), "[DONE]"]))

    assert gw.request(body={**CHAT, "stream": True})[0] == 200
    [record] = gw.records(1)
    assert record["outcome"] == "upstream_failed" and record["http_status"] == 200
    assert gw.request(body=CHAT)[0] == 502


def test_a_completion_without_usage_voids_the_attempt(stub, make_gateway):
    gw = make_gateway()
    reply = completion()
    del reply["usage"]
    stub.push(("json", 200, reply))

    assert gw.request(body=CHAT)[0] == 502
    assert gw.records(1)[0]["outcome"] == "upstream_failed"


@pytest.mark.parametrize("status", [400, 413, 422])
def test_provider_rejections_are_passed_through_and_count_as_calls(stub, make_gateway, status):
    gw = make_gateway()
    rejection = {"error": {"message": "This model's maximum context length is 262144 tokens"}}
    stub.push(("json", status, rejection), ("json", 200, completion()))

    returned, _, body = gw.request(body=CHAT)
    assert (returned, json.loads(body)) == (status, rejection)
    assert gw.request(body=CHAT)[0] == 200  # a rejection does not void the attempt

    rejected, ok = gw.records(2)
    assert rejected["outcome"] == "upstream_rejected" and rejected["http_status"] == status
    assert rejected["usage"] == gateway.ZERO_USAGE and rejected["totals"] == {"tokens": 0, "calls": 1}
    assert ok["totals"]["calls"] == 2


@pytest.mark.parametrize("status", [401, 403, 404])
def test_setup_errors_void_the_attempt_instead_of_scoring_the_harness(stub, make_gateway, status):
    gw = make_gateway()
    stub.push(("json", status, {"error": "invalid api key or model"}))

    assert gw.request(body=CHAT)[0] == 502
    assert gw.records(1)[0]["outcome"] == "upstream_failed"


# ----------------------------------------------------------------- budgets


def test_the_call_budget_is_enforced_locally(stub, make_gateway):
    gw = make_gateway(max_calls=2)
    stub.push(("json", 200, completion()), ("json", 200, completion()))

    responses = [gw.request(body=CHAT) for _ in range(3)]

    assert [s for s, _, _ in responses] == [200, 200, 429]
    _, headers, body = responses[2]
    assert headers["x-should-retry"] == "false"
    assert json.loads(body)["error"] == {"type": "budget_exhausted", "code": "insufficient_quota",
                                         "message": "the token or call budget for this run is spent"}
    assert len(stub.requests) == 2
    record = gw.records(3)[2]
    assert record["outcome"] == "budget_exhausted" and record["upstream_attempts"] == 0
    assert record["totals"]["calls"] == 2


def test_the_token_budget_is_enforced_locally(stub, make_gateway):
    gw = make_gateway(max_total_tokens=100)
    stub.push(("json", 200, completion(prompt=80, completion_tokens=30)))

    assert [gw.request(body=CHAT)[0] for _ in range(2)] == [200, 429]
    assert len(stub.requests) == 1
    assert gw.records(2)[1]["totals"] == {"tokens": 110, "calls": 1}


def test_the_token_budget_applies_to_streams(stub, make_gateway):
    gw = make_gateway(max_total_tokens=50)
    stub.push(("sse", STREAM))

    assert [gw.request(body={**CHAT, "stream": True})[0] for _ in range(2)] == [200, 429]
    assert gw.records(2)[1]["totals"] == {"tokens": 57, "calls": 1}


def test_concurrent_requests_respect_the_call_budget(stub, make_gateway):
    gw = make_gateway(max_calls=5)
    stub.push(*[("json", 200, completion()) for _ in range(5)])
    results = []
    threads = [threading.Thread(target=lambda: results.append(gw.request(body=CHAT)[0])) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [200] * 5 + [429] * 3
    records = gw.records(8)
    assert [r["seq"] for r in records] == list(range(8))
    assert max(r["totals"]["calls"] for r in records) == 5


# --------------------------------------------------- what the harness controls


@pytest.mark.parametrize("kwargs,status", [
    ({"raw": b"not json"}, 400),
    ({"raw": b"[1, 2]"}, 400),
    ({"raw": b"[" * 100_000 + b"]" * 100_000}, 400),  # too deep to parse
    ({"body": {**CHAT, "stream": True, "stream_options": "yes"}}, 400),
    ({"body": CHAT, "path": "/v1/completions"}, 404),
])
def test_malformed_requests_never_reach_the_provider_or_void_the_attempt(stub, make_gateway, kwargs, status):
    gw = make_gateway()
    stub.push(("json", 200, completion()))

    assert gw.request(**kwargs)[0] == status
    assert stub.requests == []
    assert gw.request(body=CHAT)[0] == 200  # the attempt goes on

    bad, ok = gw.records(2)
    assert bad["outcome"] == "bad_request" and bad["http_status"] == status
    assert bad["totals"] == {"tokens": 0, "calls": 0}
    assert ok["outcome"] == "ok"


@pytest.mark.parametrize("head,status", [
    (b"POST /v1/chat/completions HTTP/1.1\r\nHost: g\r\n\r\n", 411),
    (b"POST /v1/chat/completions HTTP/1.1\r\nHost: g\r\nContent-Length: \xb2\r\n\r\n", 400),
    (b"POST /v1/chat/completions HTTP/1.1\r\nHost: g\r\nTransfer-Encoding: gzip\r\n\r\n", 400),
    (b"POST /v1/chat/completions HTTP/1.1\r\nHost: g\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n", 400),
    # a valid JSON object in a chunk followed by something other than the line break
    (b"POST /v1/chat/completions HTTP/1.1\r\nHost: g\r\nTransfer-Encoding: chunked\r\n\r\n"
     b"2\r\n{}XX\r\n0\r\n\r\n", 400),
])
def test_bad_framing_is_a_bad_request(stub, make_gateway, head, status):
    gw = make_gateway()

    assert gw.send_raw(head) == status

    assert stub.requests == [] and not gw.gateway.ledger.void
    assert gw.records(1)[0]["outcome"] == "bad_request"


def test_a_gateway_fault_before_admission_is_a_bad_request(stub, make_gateway, monkeypatch):
    gw = make_gateway()
    stub.push(("json", 200, completion()))
    original, calls = gateway.Exchange._parse, []

    def faulty_once(self):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("unexpected")
        return original(self)

    monkeypatch.setattr(gateway.Exchange, "_parse", faulty_once)

    assert gw.request(body=CHAT)[0] == 400
    assert gw.request(body=CHAT)[0] == 200
    assert [r["outcome"] for r in gw.records(2)] == ["bad_request", "ok"]


def test_a_gateway_fault_after_admission_voids_with_exactly_one_record(stub, make_gateway, monkeypatch):
    gw = make_gateway()
    stub.push(("json", 200, completion()))

    def broken(obj):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(gateway, "analyze_completion", broken)

    assert gw.request(body=CHAT)[0] == 502
    assert gw.request(body=CHAT)[0] == 502
    first, later = gw.records(2)
    assert first["outcome"] == "upstream_failed" and first["upstream_attempts"] == 1
    assert later["upstream_attempts"] == 0


def test_model_listing_is_answered_locally(stub, make_gateway):
    gw = make_gateway()

    status, _, body = gw.request("GET", "/v1/models")
    assert status == 200 and [m["id"] for m in json.loads(body)["data"]] == [MODEL]
    for path in (f"/v1/models/{MODEL}", "/v1/models/Qwen%2FQwen3-30B-A3B-Instruct-2507"):
        status, _, body = gw.request("GET", path)
        assert status == 200 and json.loads(body)["id"] == MODEL
    assert gw.request("GET", "/v1/models/gpt-4o")[0] == 404

    assert stub.requests == [] and gw.lines() == []


def test_the_provider_key_never_reaches_the_client_or_the_log(stub, make_gateway):
    gw = make_gateway(max_calls=2)
    stub.push(("json", 200, completion()), ("json", 400, {"error": "bad"}))
    responses = [gw.request(body=CHAT) for _ in range(3)] + [gw.request("GET", "/v1/models")]

    for _, headers, body in responses:
        assert KEY not in body.decode() and KEY not in json.dumps(headers)
    gw.records(3)
    assert KEY not in gw.log_path.read_text()


# ------------------------------------------------------- shutdown and logging


def background_request(gw, **kwargs):
    """Start a request on a thread; the result (or the exception) lands in the returned list."""
    result = []

    def run():
        try:
            result.append(gw.request(**kwargs))
        except (OSError, http.client.HTTPException) as exc:
            result.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, result


def test_shutdown_waits_for_requests_in_flight(stub, make_gateway):
    gw = make_gateway()
    stub.push(("slow", 0.3, 200, completion()))
    worker, result = background_request(gw, body=CHAT)
    assert wait_for(lambda: len(stub.requests) == 1)

    assert gw.close(drain_s=5) == 0
    worker.join()

    [line] = gw.lines()
    assert json.loads(line)["outcome"] == "ok" and result[0][0] == 200


def test_shutdown_records_requests_still_in_flight_once_and_without_a_void(stub, make_gateway):
    gw = make_gateway()
    stub.push(("slow", 1.0, 200, completion()))
    worker, result = background_request(gw, body=CHAT)
    assert wait_for(lambda: len(stub.requests) == 1)

    assert gw.close(drain_s=0.1) == 0
    worker.join()  # the abandoned exchange stops; it writes nothing more

    [line] = gw.lines()
    record = json.loads(line)
    validate("gateway-record", record)
    assert record["outcome"] == "usage_unknown" and record["http_status"] == 503
    assert not gw.gateway.ledger.void and not gw.gateway.fatal
    assert not isinstance(result[0], tuple) or result[0][0] != 200  # the harness never got an answer


def test_shutdown_does_not_void_a_request_that_was_never_admitted(stub, make_gateway):
    gw = make_gateway()
    with socket.create_connection(("127.0.0.1", gw.port), timeout=10) as sock:
        sock.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: g\r\nContent-Length: 100\r\n\r\n0123456789")
        assert wait_for(lambda: len(gw.gateway._inflight) == 1)  # stuck reading the body

        assert gw.close(drain_s=0.1) == 0

    [line] = gw.lines()
    record = json.loads(line)
    assert record["outcome"] == "bad_request" and record["http_status"] == 503
    assert stub.requests == [] and not gw.gateway.ledger.void


def drain_now(gw):
    """Run the shutdown drain on its own thread, as the signal handler would, and wait for it."""
    thread = threading.Thread(target=gw.gateway.drain, args=(0,))
    thread.start()
    thread.join(timeout=5)


@pytest.mark.parametrize("status,outcome", [(401, "upstream_failed"), (400, "upstream_rejected")])
def test_shutdown_keeps_an_answer_already_decided(stub, make_gateway, monkeypatch, status, outcome):
    gw = make_gateway()
    stub.push(("json", status, {"error": "no"}))
    original = gateway.Exchange._send

    def slow_client(self, *args, **kwargs):
        drain_now(gw)  # the drain ends while the decided answer is still being sent
        return original(self, *args, **kwargs)

    monkeypatch.setattr(gateway.Exchange, "_send", slow_client)
    worker, result = background_request(gw, body=CHAT)
    worker.join()

    [line] = gw.lines()
    record = json.loads(line)
    validate("gateway-record", record)
    assert record["outcome"] == outcome and record["http_status"] == (502 if status == 401 else status)
    assert result[0][0] == record["http_status"]  # the harness got the answer its record states
    assert gw.gateway.ledger.void is (outcome == "upstream_failed")


def test_shutdown_cuts_a_retry_backoff_short_and_sends_nothing_more(stub, make_gateway):
    gw = make_gateway(retry_base_delay_s=10.0)
    stub.push(("json", 503, {"error": "busy"}), ("json", 200, completion()))  # a 503 is retried after a backoff
    worker, _ = background_request(gw, body=CHAT)
    assert wait_for(lambda: len(stub.requests) == 1)
    time.sleep(0.2)  # the exchange is now waiting out its backoff

    started = time.monotonic()
    drain_now(gw)
    worker.join(timeout=5)

    assert time.monotonic() - started < 3 and not worker.is_alive()
    assert len(stub.requests) == 1
    assert json.loads(gw.lines()[0])["outcome"] == "usage_unknown"


def test_shutdown_records_exactly_the_usage_counted(stub, make_gateway, monkeypatch):
    gw = make_gateway()
    stub.push(("json", 200, completion(prompt=100, completion_tokens=20)))
    original = gateway.Ledger.add_tokens

    def add_tokens(self, usage):
        drain = threading.Thread(target=gw.gateway.drain, args=(0,))
        drain.start()  # shutdown arrives while the usage is being counted
        wait_for(lambda: gw.gateway._closed)
        time.sleep(0.1)
        original(self, usage)

    monkeypatch.setattr(gateway.Ledger, "add_tokens", add_tokens)
    worker, _ = background_request(gw, body=CHAT)
    worker.join()

    assert wait_for(lambda: len(gw.lines()) == 1)
    record = json.loads(gw.lines()[0])
    validate("gateway-record", record)
    assert record["outcome"] == "ok"  # http_status 503 if abandoned, 200 if the answer was decided first
    assert record["usage"]["input_tokens"] + record["usage"]["output_tokens"] == 120
    assert gw.gateway.ledger.totals()["tokens"] == 120


def test_nothing_is_sent_upstream_once_a_request_is_abandoned(stub, make_gateway, monkeypatch):
    gw = make_gateway()
    stub.push(("json", 200, completion()))
    original = gateway.Gateway.connection

    def connection(self):
        conn = original(self)
        connect = conn.connect

        def connect_during_shutdown():
            drain_now(gw)  # the drain ends while the upstream connection is being opened
            connect()

        conn.connect = connect_during_shutdown
        return conn

    monkeypatch.setattr(gateway.Gateway, "connection", connection)
    worker, _ = background_request(gw, body=CHAT)
    worker.join()

    [line] = gw.lines()
    record = json.loads(line)
    assert record["outcome"] == "usage_unknown" and record["http_status"] == 503
    assert stub.requests == [] and not gw.gateway.ledger.void


def test_a_harness_that_stops_sending_gets_a_408(stub, make_gateway):
    gw = make_gateway(client_timeout_s=0.3)
    with socket.create_connection(("127.0.0.1", gw.port), timeout=10) as sock:
        sock.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: g\r\nContent-Length: 100\r\n\r\n0123456789")
        assert sock.recv(64).split(b" ")[1] == b"408"

    [record] = gw.records(1)
    assert record["outcome"] == "bad_request" and record["http_status"] == 408
    assert not gw.gateway.ledger.void


def test_requests_after_the_drain_are_refused_without_a_record(make_gateway):
    gw = make_gateway()
    assert gw.close(drain_s=0) == 0
    assert gw.gateway.register(object()) is False
    assert gw.lines() == []


def test_a_failed_log_close_makes_the_exit_status_1(make_gateway):
    gw = make_gateway()
    real_log = gw.gateway.log

    class FailsOnClose:
        def write(self, record):
            real_log.write(record)

        def close(self):
            real_log.close()
            raise OSError(5, "Input/output error")

    gw.gateway.log = FailsOnClose()
    assert gw.close() == 1


def test_a_failed_log_write_voids_the_attempt_and_stops_the_gateway(stub, make_gateway):
    gw = make_gateway()
    stub.push(("json", 200, completion()))

    class FullDisk:
        def write(self, record):
            raise OSError(28, "No space left on device")

        def close(self):
            pass

    gw.gateway.log = FullDisk()

    gw.request(body=CHAT)

    assert wait_for(lambda: not gw.thread.is_alive())
    assert gw.gateway.fatal and gw.gateway.ledger.void
    assert gw.close() == 1


# --------------------------------------------------------- configuration


@pytest.mark.parametrize("value,expected", [
    ("stop", "stop"), ("length", "length"), ("tool_calls", "tool_calls"), ("function_call", "tool_calls"),
    ("content_filter", "content_filter"), ("eos", "other"), (None, "none"), (["stop"], "other"), (1, "other"),
])
def test_finish_reason_mapping(value, expected):
    assert gateway.map_finish_reason(value) == expected


def test_retry_after_parsing():
    assert gateway.retry_after_seconds("250", "9") == 0.25
    assert gateway.retry_after_seconds(None, "3") == 3.0
    in_ten = email.utils.formatdate(time.time() + 10, usegmt=True)
    assert 8 <= gateway.retry_after_seconds(None, in_ten) <= 10.5
    for value in ("soon", "²", ""):
        assert gateway.retry_after_seconds(None, value) is None


def test_upstream_url_rules():
    up = gateway.Upstream.parse("https://api.tokenfactory.nebius.com/v1/")
    assert (up.scheme, up.host, up.port, up.chat_path) == ("https", "api.tokenfactory.nebius.com", 443,
                                                           "/v1/chat/completions")
    assert gateway.Upstream.parse("http://127.0.0.1:9000/v1").chat_path == "/v1/chat/completions"
    for bad in ("http://api.example.com/v1", "https://user:pw@api.example.com/v1", "ftp://api.example.com",
                "https://api.example.com/v1?key=1", "https:///v1"):
        with pytest.raises(gateway.ConfigError):
            gateway.Upstream.parse(bad)


def test_config_rules():
    up = gateway.Upstream.parse("https://api.example.com/v1/")
    base = dict(upstream=up, model=MODEL, api_key=KEY, max_total_tokens=10, max_calls=10, upstream_retries=3)
    gateway.Config(**base)
    for change in ({"api_key": ""}, {"model": ""}, {"max_calls": 0}, {"upstream_retries": 6},
                   {"read_timeout_s": 0}, {"client_timeout_s": -1}):
        with pytest.raises(gateway.ConfigError):
            gateway.Config(**{**base, **change})


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
def test_https_upstream_with_a_verified_certificate(tmp_path, make_gateway):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    tls_stub = StubProvider(tls_context=context)
    try:
        tls_stub.push(("json", 200, completion()))
        gw = make_gateway(provider=tls_stub, upstream_ca_file=str(cert))
        assert gw.request(body=CHAT)[0] == 200
        assert tls_stub.requests[0][1]["authorization"] == f"Bearer {KEY}"
    finally:
        tls_stub.close()


def cli_args(stub, tmp_path):
    return [sys.executable, "-m", "hb.gateway", "--listen", "127.0.0.1:0", "--upstream", stub.url,
            "--model", MODEL, "--max-total-tokens", "1000", "--max-calls", "10",
            "--upstream-retries", "0", "--drain-s", "1", "--log", str(tmp_path / "cli.jsonl")]


def test_cli_serves_until_sigterm(stub, tmp_path):
    env = {**os.environ, "MODEL_API_KEY": KEY, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen(cli_args(stub, tmp_path), env=env, stderr=subprocess.PIPE, text=True)
    try:
        line = proc.stderr.readline()
        assert line.startswith("hb-gateway listening on 127.0.0.1:"), line
        port = int(line.rsplit(":", 1)[1])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/v1/models")
        assert conn.getresponse().status == 200
        conn.close()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_cli_refuses_to_start_without_a_key(stub, tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "MODEL_API_KEY"}
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run(cli_args(stub, tmp_path), env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2 and "MODEL_API_KEY" in proc.stderr
