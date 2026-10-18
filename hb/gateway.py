"""Model gateway: the harness's only route to a model.

An OpenAI-compatible HTTP proxy with exactly one upstream and one pinned
model. Its guarantees come from what it is built to do, not from inspecting
what the harness sends:

* Every chat request goes to the pinned upstream with the pinned model id,
  whatever model the request named.
* The upstream request carries a fixed header set. Nothing the harness puts in
  headers reaches the provider, and the real API key never reaches the harness.
* Budgets are enforced here. Once the rep's token or call budget is spent,
  requests are answered locally with HTTP 429 and never reach the provider.
* Streaming requests always ask the provider for usage, so every token is
  counted. If the client did not ask for usage, the usage-only chunk is
  removed from what the client receives.
* Provider failures are retried only while no byte has reached the client and
  only when the provider cannot still be working on the request.
* An attempt is void (recorded as `upstream_failed`; the toolkit re-runs it)
  only for provider-side or setup failures: retries exhausted, setup errors
  (401/403/404), a broken stream, usage that cannot be counted, a gateway
  fault after admission, or a record that cannot be written. A void is a
  re-roll, so nothing that depends only on the harness's own behaviour voids:
  malformed requests are `bad_request`, and requests the gateway stops waiting
  for (a read timeout after sending, before or after the response headers, or
  shutdown) are `usage_unknown`.

One record per POST is appended to a JSONL log
(schemas/gateway-record.schema.json). Bodies are stored only as hashes.
Standard library only, so it runs in the toolkit image without extra
dependencies. Exit status: 0 after a clean shutdown, 1 if a record could not
be written (the attempt must then be treated as void), 2 for bad settings.

Usage:
    MODEL_API_KEY=... python -m hb.gateway --listen 0.0.0.0:8080 \\
        --upstream https://api.tokenfactory.nebius.com/v1/ --model <id> \\
        --max-total-tokens N --max-calls N --upstream-retries N --log /log/gateway.jsonl
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import http.client
import ipaddress
import json
import os
import re
import signal
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RECORD_SCHEMA = "hb/gateway-record/1"
CHAT_PATH = "/v1/chat/completions"
MODELS_PATH = "/v1/models"
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_SSE_LINE_BYTES = 8 * 1024 * 1024
MAX_UPSTREAM_RETRIES = 5
USER_AGENT = "hb-gateway/1"
FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "content_filter"})
REJECTED_STATUSES = frozenset({400, 413, 422})  # the provider refused this request; the harness can react
RETRYABLE_STATUSES = frozenset({408, 429})  # plus every 5xx
ZERO_USAGE = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
_CHUNK_SIZE = re.compile(rb"[0-9A-Fa-f]{1,8}")


class ConfigError(ValueError):
    """The gateway was started with settings it refuses to run with."""


class UpstreamError(Exception):
    """The provider's response could not be read completely."""


class BadRequest(Exception):
    """The harness's request cannot be handled. Carries the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- configuration


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_decimal(text: str) -> bool:
    return text.isascii() and text.isdecimal()


@dataclass(frozen=True)
class Upstream:
    scheme: str
    host: str
    port: int
    chat_path: str

    @classmethod
    def parse(cls, base_url: str) -> "Upstream":
        """Parse a provider base URL such as https://api.tokenfactory.nebius.com/v1/.

        Plain http is accepted only for a loopback host (local stubs in tests),
        so the API key is never sent over an unencrypted link.
        """
        parts = urllib.parse.urlsplit(base_url)
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ConfigError("upstream URL must not contain a query, fragment or credentials")
        if not parts.hostname:
            raise ConfigError("upstream URL has no host")
        if parts.scheme == "http":
            if not _is_loopback(parts.hostname):
                raise ConfigError("plain http is only allowed for a loopback upstream")
        elif parts.scheme != "https":
            raise ConfigError("upstream URL must use https")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError as exc:
            raise ConfigError(f"upstream URL has an invalid port: {exc}") from None
        return cls(parts.scheme, parts.hostname, port, parts.path.rstrip("/") + "/chat/completions")


@dataclass(frozen=True)
class Config:
    upstream: Upstream
    model: str
    api_key: str
    max_total_tokens: int
    max_calls: int
    upstream_retries: int
    retry_base_delay_s: float = 1.0
    max_retry_delay_s: float = 30.0
    connect_timeout_s: float = 30.0
    read_timeout_s: float = 600.0  # silence from the provider before the gateway stops waiting
    client_timeout_s: float = 60.0  # silence from the harness while sending a request, or not reading
    upstream_ca_file: str | None = None  # extra trust anchors (tests, corporate TLS proxies)

    def __post_init__(self):
        if not self.model:
            raise ConfigError("model id is empty")
        if not self.api_key:
            raise ConfigError("MODEL_API_KEY is not set")
        if self.max_total_tokens < 1 or self.max_calls < 1:
            raise ConfigError("budgets must be at least 1")
        if not 0 <= self.upstream_retries <= MAX_UPSTREAM_RETRIES:
            raise ConfigError(f"upstream retries must be between 0 and {MAX_UPSTREAM_RETRIES}")
        if min(self.connect_timeout_s, self.read_timeout_s, self.client_timeout_s) <= 0:
            raise ConfigError("timeouts must be positive")


# ---------------------------------------------------------------------- state


class Ledger:
    """Rep-wide counters and the void latch. Every change happens under one lock."""

    def __init__(self, max_total_tokens: int, max_calls: int):
        self._lock = threading.Lock()
        self._max_tokens = max_total_tokens
        self._max_calls = max_calls
        self._next_seq = 0
        self._tokens = 0
        self._calls = 0
        self._void = False

    def next_seq(self) -> int:
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
            return seq

    def admit(self) -> str:
        """Return 'admitted' (and count the call), 'exhausted' or 'void'.

        Budgets are checked when a request arrives: a request already in flight
        when the token budget is crossed completes, so the overshoot is bounded
        by the number of concurrent requests.
        """
        with self._lock:
            if self._void:
                return "void"
            if self._tokens >= self._max_tokens or self._calls >= self._max_calls:
                return "exhausted"
            self._calls += 1
            return "admitted"

    def add_tokens(self, usage: dict) -> None:
        with self._lock:
            self._tokens += usage["input_tokens"] + usage["output_tokens"]

    def mark_void(self) -> None:
        with self._lock:
            self._void = True

    @property
    def void(self) -> bool:
        with self._lock:
            return self._void

    def totals(self) -> dict:
        with self._lock:
            return {"tokens": self._tokens, "calls": self._calls}


class RecordLog:
    """Append-only JSONL log. Each record is flushed and synced before the next."""

    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8")

    def write(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        with self._lock:
            self._fh.close()


# ------------------------------------------------------------ response parsing


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _is_uint(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _loads(data):
    """json.loads that returns None for anything that is not valid, bounded JSON."""
    try:
        return json.loads(data)
    except (ValueError, RecursionError):
        return None


def parse_usage(usage) -> dict | None:
    """OpenAI-style usage -> record usage, or None if it is missing or malformed."""
    if not isinstance(usage, dict):
        return None
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not (_is_uint(prompt) and _is_uint(completion)):
        return None
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    cached = min(cached, prompt) if _is_uint(cached) else 0
    return {"input_tokens": prompt, "cached_input_tokens": cached, "output_tokens": completion}


def map_finish_reason(value) -> str:
    if value is None:
        return "none"
    if not isinstance(value, str):
        return "other"
    if value == "function_call":
        return "tool_calls"
    return value if value in FINISH_REASONS else "other"


def _arguments_valid(text) -> bool:
    return isinstance(text, str) and isinstance(_loads(text), dict)


def analyze_completion(obj) -> tuple[dict | None, str, tuple[int, int]]:
    """Usage, finish reason of the first choice, and (tool calls, valid) of a completion."""
    if not isinstance(obj, dict):
        return None, "none", (0, 0)
    choices = obj.get("choices") if isinstance(obj.get("choices"), list) else []
    finish, count, valid = "none", 0, 0
    for position, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        if position == 0:
            finish = map_finish_reason(choice.get("finish_reason"))
        message = choice.get("message")
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        for call in calls if isinstance(calls, list) else []:
            count += 1
            function = call.get("function") if isinstance(call, dict) else None
            valid += _arguments_valid(function.get("arguments") if isinstance(function, dict) else None)
    return parse_usage(obj.get("usage")), finish, (count, valid)


class StreamState:
    """Accumulates usage, finish reasons and tool-call arguments across SSE chunks."""

    def __init__(self):
        self.usage = None
        self._finish = {}
        self._tool_args = {}

    def feed(self, obj) -> bool:
        """Consume one chunk. Returns True for a usage-only chunk: usage and no choices."""
        if not isinstance(obj, dict):
            return False
        usage = parse_usage(obj.get("usage"))
        if usage is not None:
            self.usage = usage
        choices = obj.get("choices")
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict):
                continue
            index = choice.get("index", 0) if _is_uint(choice.get("index", 0)) else 0
            if choice.get("finish_reason") is not None:
                self._finish[index] = choice["finish_reason"]
            delta = choice.get("delta")
            calls = delta.get("tool_calls") if isinstance(delta, dict) else None
            for call in calls if isinstance(calls, list) else []:
                if not isinstance(call, dict):
                    continue
                call_index = call.get("index", 0) if _is_uint(call.get("index", 0)) else 0
                parts = self._tool_args.setdefault((index, call_index), [])
                function = call.get("function")
                if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                    parts.append(function["arguments"])
        return isinstance(obj.get("usage"), dict) and (choices is None or choices == [])

    def finish_reason(self) -> str:
        if not self._finish:
            return "none"
        return map_finish_reason(self._finish.get(0, self._finish[min(self._finish)]))

    def tool_calls(self) -> tuple[int, int]:
        return len(self._tool_args), sum(_arguments_valid("".join(p)) for p in self._tool_args.values())


def iter_sse_events(resp, on_bytes):
    """Yield (raw_event_bytes, data_or_None) for each server-sent event.

    raw_event_bytes is the event exactly as received, including its blank-line
    terminator, so it can be forwarded unchanged. Multi-line data fields are
    joined with newlines; comment lines are kept in raw form and carry no data.
    on_bytes sees every byte read.
    """
    raw, data = [], []
    while True:
        line = resp.readline(MAX_SSE_LINE_BYTES + 1)
        if len(line) > MAX_SSE_LINE_BYTES:
            raise UpstreamError("server-sent event line too long")
        on_bytes(line)
        if not line:
            if raw:
                yield b"".join(raw), _event_data(data)
            return
        raw.append(line)
        content = line.rstrip(b"\r\n")
        if not content:
            yield b"".join(raw), _event_data(data)
            raw, data = [], []
        elif content.startswith(b"data:"):
            value = content[5:]
            data.append(value[1:] if value.startswith(b" ") else value)


def _event_data(data: list[bytes]) -> str | None:
    return b"\n".join(data).decode("utf-8", errors="replace") if data else None


def _read_capped(resp) -> bytes:
    try:
        data = resp.read(MAX_RESPONSE_BYTES + 1)
    except TimeoutError:
        raise  # the provider went silent: not a broken response (Exchange._stopped_waiting)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise UpstreamError(f"reading the response failed: {exc}") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise UpstreamError("response too large")
    return data


def retry_after_seconds(retry_after_ms: str | None, retry_after: str | None) -> float | None:
    """The provider's requested wait: retry-after-ms, or Retry-After in seconds or as an HTTP date."""
    if retry_after_ms and _is_decimal(retry_after_ms.strip()):
        return int(retry_after_ms.strip()) / 1000
    if not retry_after:
        return None
    value = retry_after.strip()
    if _is_decimal(value):
        return float(int(value))
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _error_body(kind: str, message: str, code: str | None = None) -> bytes:
    error = {"type": kind, "message": message}
    if code:
        error["code"] = code
    return json.dumps({"error": error}).encode("utf-8")


# ------------------------------------------------------------------- gateway


class Gateway:
    def __init__(self, config: Config, log_path: Path):
        self.config = config
        self.ledger = Ledger(config.max_total_tokens, config.max_calls)
        self.log = RecordLog(log_path)
        self.fatal = False
        self.on_fatal = lambda: None
        self._ssl = (ssl.create_default_context(cafile=config.upstream_ca_file)
                     if config.upstream.scheme == "https" else None)
        self._inflight = set()
        self._inflight_cond = threading.Condition()
        self._closed = False

    def connection(self) -> http.client.HTTPConnection:
        up, timeout = self.config.upstream, self.config.connect_timeout_s
        if up.scheme == "https":
            return http.client.HTTPSConnection(up.host, up.port, timeout=timeout, context=self._ssl)
        return http.client.HTTPConnection(up.host, up.port, timeout=timeout)

    def upstream_headers(self) -> dict:
        # Fixed set. Host, Content-Length and Accept-Encoding: identity are added by http.client.
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
        }

    def retry_delay(self, attempt: int, requested: float | None) -> float:
        cfg = self.config
        delay = requested if requested is not None else cfg.retry_base_delay_s * (2 ** (attempt - 1))
        return min(delay, cfg.max_retry_delay_s)

    def write_record(self, record: dict) -> None:
        """Append a record. If that fails, the attempt cannot be trusted: void it and stop."""
        try:
            self.log.write(record)
        except (OSError, ValueError):  # ValueError: the log is already closed
            traceback.print_exc(file=sys.stderr)
            self.ledger.mark_void()
            self.fatal = True
            self.on_fatal()

    def register(self, exchange: "Exchange") -> bool:
        """Track a request. False once shutdown has closed the gateway to new requests."""
        with self._inflight_cond:
            if self._closed:
                return False
            self._inflight.add(exchange)
            return True

    def unregister(self, exchange: "Exchange") -> None:
        with self._inflight_cond:
            self._inflight.discard(exchange)
            self._inflight_cond.notify_all()

    def drain(self, timeout_s: float) -> int:
        """Wait for requests in flight, then close to new ones and abandon the rest.

        Abandoned requests are recorded (as usage_unknown, or bad_request if they
        were never admitted) while the log is still open. Returns how many.
        """
        deadline = time.monotonic() + timeout_s
        with self._inflight_cond:
            while self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._inflight_cond.wait(remaining)
            self._closed = True
            left = list(self._inflight)
        for exchange in left:
            exchange.abandon()
        return len(left)


class Exchange:
    """One POST from the harness: exactly one record, whatever happens."""

    def __init__(self, handler: BaseHTTPRequestHandler, gateway: Gateway):
        self.h = handler
        self.gw = gateway
        self.seq = gateway.ledger.next_seq()
        self.started_ms = time.time_ns() // 1_000_000
        self.t0 = time.monotonic()
        self.stream = False
        self.model_rewritten = False
        self.admitted = False
        self.attempts = 0
        self.request_bytes = b""
        self.response_sha = _sha256(b"")
        self.status_sent = None
        self.counted_usage = None
        self.stream_state = None
        self.upstream_conn = None
        self.abandoned = False
        self.decided = None  # the record, once the answer is decided
        self._done = False
        # Admission, counting, voiding, deciding the answer and abandoning exclude each other.
        self._lock = threading.Lock()

    # -- lifecycle

    def run(self) -> None:
        if not self.gw.register(self):
            # The log is closing: this request never reached the provider and is not recorded.
            self._send(503, _error_body("unavailable", "the gateway is shutting down"))
            return
        try:
            try:
                record = self._handle()
            except Exception:
                traceback.print_exc(file=sys.stderr)
                record = self._fault()
            if record is not None:
                self._finish(record)
        finally:
            self.gw.unregister(self)

    def abandon(self) -> None:
        """Shutdown reached this request before it finished. Record it now; never void.

        An answer already decided (a void included) keeps its record. Otherwise the
        record holds exactly the usage counted so far. From here on nothing more is
        counted, voided or sent upstream for this request.
        """
        with self._lock:
            if self._done:
                return
            self._done = True
            self.abandoned = True
            conn = self.upstream_conn
            if conn is not None and conn.sock is not None:
                try:
                    conn.sock.shutdown(socket.SHUT_RDWR)  # nothing more is sent; a pending read is unblocked
                except OSError:
                    pass
            if self.decided is not None:
                record = self.decided
            elif not self.admitted:
                record = self._record("bad_request", self.status_sent or 503)
            else:
                record = self._unanswered(self.status_sent or 503)
        self.gw.write_record(record)

    def _finish(self, record: dict) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
        self.gw.write_record(record)

    def _fault(self) -> dict | None:
        """A gateway fault. Before admission nothing was forwarded or counted, so the
        harness gets a bad_request; after admission the attempt is void."""
        if self.abandoned:
            return None
        if not self.admitted:
            if self.status_sent is not None:
                return self._decide("bad_request", self.status_sent)
            return self._local(400, "bad_request", _error_body("bad_request", "the gateway could not handle this"))
        state = self.stream_state
        if state is None:
            return self._fail(self.counted_usage)
        try:
            finish, tools = state.finish_reason(), state.tool_calls()
        except Exception:  # the fault may be in the stream bookkeeping itself
            finish, tools = "none", (0, 0)
        return self._fail(self.counted_usage, finish, tools)

    # -- request side

    def _handle(self) -> dict | None:
        try:
            forwarded, client_wants_usage = self._parse()
        except BadRequest as exc:
            kind = "not_found" if exc.status == 404 else "bad_request"
            return self._local(exc.status, "bad_request", _error_body(kind, str(exc)))

        with self._lock:
            if self._done:
                return None
            decision = self.gw.ledger.admit()
            self.admitted = decision == "admitted"
        if decision == "void":
            return self._local(502, "upstream_failed",
                               _error_body("upstream_failed", "an earlier provider failure ended this attempt"),
                               {"x-should-retry": "false"})
        if decision == "exhausted":
            return self._local(429, "budget_exhausted",
                               _error_body("budget_exhausted", "the token or call budget for this run is spent",
                                           "insufficient_quota"),
                               {"x-should-retry": "false"})
        self.request_bytes = forwarded
        return self._forward(forwarded, client_wants_usage)

    def _parse(self) -> tuple[bytes, bool]:
        """Read and check the request; return the bytes to forward. Raises BadRequest."""
        self.request_bytes = self._read_body()
        path = urllib.parse.urlsplit(self.h.path).path.rstrip("/")
        if path != CHAT_PATH:
            raise BadRequest(404, f"only POST {CHAT_PATH} is served")
        try:
            request = json.loads(self.request_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            request = None
        if not isinstance(request, dict):
            raise BadRequest(400, "the body must be a JSON object")
        options = request.get("stream_options")
        if options is not None and not isinstance(options, dict):
            raise BadRequest(400, "stream_options must be an object")

        self.stream = request.get("stream") is True
        self.model_rewritten = request.get("model") != self.gw.config.model
        client_wants_usage = isinstance(options, dict) and options.get("include_usage") is True
        request["model"] = self.gw.config.model
        if self.stream:
            request["stream_options"] = {**(options or {}), "include_usage": True}
        try:  # ensure_ascii keeps lone surrogates (valid JSON escapes) encodable
            forwarded = json.dumps(request, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        except (ValueError, RecursionError):
            raise BadRequest(400, "the body cannot be re-encoded") from None
        return forwarded, client_wants_usage

    def _read_body(self) -> bytes:
        try:
            return self._read_body_framed()
        except TimeoutError:
            raise BadRequest(408, "the body did not arrive in time") from None

    def _read_body_framed(self) -> bytes:
        headers = self.h.headers
        encoding = (headers.get("Transfer-Encoding") or "").strip().lower()
        if encoding == "chunked":
            return self._read_chunked()
        if encoding:
            raise BadRequest(400, "unsupported Transfer-Encoding")
        length = headers.get("Content-Length")
        if length is None:
            raise BadRequest(411, "a Content-Length or chunked body is required")
        if not _is_decimal(length.strip()):
            raise BadRequest(400, "invalid Content-Length")
        size = int(length.strip())
        if size > MAX_REQUEST_BYTES:
            raise BadRequest(413, "the body is larger than 32 MiB")
        body = self.h.rfile.read(size)
        if len(body) < size:
            raise BadRequest(400, "the body ended early")
        return body

    def _read_chunked(self) -> bytes:
        parts, total = [], 0
        while True:
            size_text = self.h.rfile.readline(1024).split(b";", 1)[0].strip()
            if not _CHUNK_SIZE.fullmatch(size_text):
                raise BadRequest(400, "invalid chunked body")
            size = int(size_text, 16)
            if size == 0:
                while self.h.rfile.readline(8192) not in (b"\r\n", b"\n", b""):
                    pass  # trailers are ignored
                return b"".join(parts)
            total += size
            if total > MAX_REQUEST_BYTES:
                raise BadRequest(413, "the body is larger than 32 MiB")
            data = self.h.rfile.read(size)
            if len(data) < size:
                raise BadRequest(400, "the body ended early")
            parts.append(data)
            self.h.rfile.readline(8)  # the CRLF after each chunk

    # -- upstream side

    def _forward(self, payload: bytes, client_wants_usage: bool) -> dict | None:
        conn, resp, problem = self._open_upstream(payload)
        if self.abandoned:
            if conn is not None:
                conn.close()
            return None
        if problem == "timeout":
            return self._stopped_waiting()
        if problem is not None:
            return self._fail()
        try:
            if resp.status == 200:
                return self._relay_stream(resp, client_wants_usage) if self.stream else self._relay_json(resp)
            if resp.status in REJECTED_STATUSES:
                return self._relay_rejected(resp)
            # Retries exhausted, or a status that means the setup is wrong (bad key, model, path).
            try:
                self.response_sha = _sha256(_read_capped(resp))
            except (UpstreamError, TimeoutError):  # the status already decided the void
                pass
            return self._fail()
        finally:
            conn.close()

    def _open_upstream(self, payload: bytes):
        """Send the request, retrying where that is safe.

        Returns (conn, resp, None), or (None, None, "timeout" | "failed").
        Retried: connection and TLS failures, a connection closed without any
        response, and 408/429/5xx answers. Not retried: a timeout after the
        request was sent, because the provider may still be generating (and
        billing) an answer the gateway would never count.
        """
        cfg = self.gw.config
        while not self.abandoned:
            self.attempts += 1
            conn = self.gw.connection()
            sent = False
            try:
                conn.connect()
                conn.sock.settimeout(cfg.read_timeout_s)
                with self._lock:  # abandon() either came first, or sees this socket and shuts it
                    if self.abandoned:
                        conn.close()
                        return None, None, "failed"
                    self.upstream_conn = conn
                # If request() fails, the body never fully left, so the provider cannot hold
                # a complete request and retrying is safe.
                conn.request("POST", cfg.upstream.chat_path, body=payload, headers=self.gw.upstream_headers())
                sent = True
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                if sent and isinstance(exc, TimeoutError):
                    return None, None, "timeout"
                if self.attempts > cfg.upstream_retries:
                    return None, None, "failed"
                time.sleep(self.gw.retry_delay(self.attempts, None))
                continue
            if ((resp.status in RETRYABLE_STATUSES or resp.status >= 500)
                    and self.attempts <= cfg.upstream_retries):
                requested = retry_after_seconds(resp.getheader("retry-after-ms"), resp.getheader("Retry-After"))
                try:
                    resp.read(MAX_RESPONSE_BYTES)
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                conn.close()
                time.sleep(self.gw.retry_delay(self.attempts, requested))
                continue
            return conn, resp, None
        return None, None, "failed"

    def _relay_json(self, resp) -> dict | None:
        try:
            data = _read_capped(resp)
        except TimeoutError:
            return self._stopped_waiting()
        except UpstreamError:
            return self._fail()
        self.response_sha = _sha256(data)
        usage, finish, tools = analyze_completion(_loads(data))
        if usage is None:  # usage that cannot be counted would break the budget
            return self._fail()
        self._count(usage)
        return self._answer("ok", 200, data, resp.getheader("Content-Type") or "application/json",
                            usage, finish, tools)

    def _relay_rejected(self, resp) -> dict | None:
        try:
            data = _read_capped(resp)
        except TimeoutError:
            return self._stopped_waiting()
        except UpstreamError:
            return self._fail()
        self.response_sha = _sha256(data)
        return self._answer("upstream_rejected", resp.status, data,
                            resp.getheader("Content-Type") or "application/json")

    def _relay_stream(self, resp, client_wants_usage: bool) -> dict | None:
        """Relay events as they arrive. Headers go out with the first forwarded event,
        so a stream that fails before producing anything gets a plain 502."""
        state, digest = StreamState(), hashlib.sha256()
        self.stream_state = state
        client_ok, totals = True, None
        try:
            for raw, data in iter_sse_events(resp, digest.update):
                if data is not None and data != "[DONE]":
                    chunk = _loads(data)
                    with self._lock:  # abandon() reads the stream state
                        usage_only = state.feed(chunk)
                        if usage_only and state.usage is not None and self.counted_usage is None:
                            # Counted, and totals taken, before [DONE] reaches the client.
                            self._count_locked(state.usage)
                            totals = self.gw.ledger.totals()
                    if usage_only and not client_wants_usage:
                        continue
                if self.status_sent is None:
                    client_ok = self._start_stream(resp)
                if client_ok:
                    client_ok = self._write(raw)  # if the client left, keep reading: tokens are billed anyway
        except TimeoutError:  # client writes never raise (_write), so the provider went silent
            self.response_sha = "sha256:" + digest.hexdigest()
            return self._stopped_waiting(client_ok)
        except (OSError, ValueError, http.client.HTTPException, UpstreamError):
            self.response_sha = "sha256:" + digest.hexdigest()
            return self._fail(state.usage, state.finish_reason(), state.tool_calls(), client_ok)
        self.response_sha = "sha256:" + digest.hexdigest()
        if self.abandoned:
            return None
        if state.usage is None:
            return self._fail(None, state.finish_reason(), state.tool_calls(), client_ok)
        if self.counted_usage is None:  # usage rode on a chunk that also had choices
            self._count(state.usage)
            totals = self.gw.ledger.totals()
        if self.status_sent is None:
            self._start_stream(resp)
        return self._decide("ok", 200, self.counted_usage, state.finish_reason(), state.tool_calls(), totals)

    # -- results

    def _count(self, usage: dict) -> None:
        with self._lock:
            self._count_locked(usage)

    def _count_locked(self, usage: dict) -> None:
        """Count a request's usage once. The record reports exactly what was counted."""
        if self.counted_usage is None and not self.abandoned:
            self.gw.ledger.add_tokens(usage)
            self.counted_usage = usage

    def _decide(self, outcome: str, http_status: int, usage=None, finish="none", tools=(0, 0),
                totals: dict | None = None, void: bool = False) -> dict | None:
        """Fix this request's record, unless shutdown abandoned it first.

        A void and the counting of usage already known happen in the same step,
        so shutdown either keeps them or prevents them.
        """
        with self._lock:
            if self.abandoned:
                return None
            if void:
                self.gw.ledger.mark_void()
                if usage is not None:
                    self._count_locked(usage)
                usage = self.counted_usage
            self.decided = self._record(outcome, http_status, usage, finish, tools, totals)
            return self.decided

    def _unanswered(self, http_status: int) -> dict:
        """The record of an admitted request the gateway stopped waiting for: ok if its
        usage was already counted, else usage_unknown. Called with the lock held."""
        state = self.stream_state
        finish, tools = (state.finish_reason(), state.tool_calls()) if state else ("none", (0, 0))
        outcome = "ok" if self.counted_usage is not None else "usage_unknown"
        return self._record(outcome, http_status, self.counted_usage, finish, tools)

    def _stopped_waiting(self, client_ok: bool = True) -> dict | None:
        """The provider sent nothing for the read timeout after the request was sent,
        before or after its response headers or first events. It may still be
        generating, and billing, so this is usage_unknown and not a void: how long a
        request takes depends on what the harness asked for. Never retried."""
        with self._lock:
            if self.abandoned:
                return None
            self.decided = self._unanswered(self.status_sent or 504)
            record = self.decided
        message = "the provider sent nothing within the read timeout"
        if self.status_sent is None:
            self._send(504, _error_body("upstream_timeout", message))
        elif self.stream and client_ok:
            # A stream already started ends with an error event, so standard OpenAI
            # clients raise instead of taking the cut-off answer as complete.
            self._write(b"data: " + _error_body("upstream_timeout", message) + b"\n\n")
        return record

    def _fail(self, usage=None, finish="none", tools=(0, 0), client_ok=True) -> dict | None:
        """Void the attempt. Usage already known is still counted, before anything is sent."""
        record = self._decide("upstream_failed", self.status_sent or 502, usage, finish, tools, void=True)
        if record is None:
            return None
        if self.status_sent is None:
            self._send(502, _error_body("upstream_failed", "the provider failed or could not be accounted for"),
                       extra={"x-should-retry": "false"})
            return record
        # Mid-stream: the client already has a 200. The record is complete before the
        # error event goes out, so a client reacting to it sees the void.
        if self.stream and client_ok:
            # An error event makes standard OpenAI clients raise instead of taking
            # the cut-off answer as complete.
            self._write(b"data: " + _error_body("upstream_failed", "the provider stream failed") + b"\n\n")
        return record

    def _local(self, status: int, outcome: str, body: bytes, extra: dict | None = None) -> dict | None:
        """Answer without a provider response; the response hash is of the gateway's own answer."""
        self.response_sha = _sha256(body)
        return self._answer(outcome, status, body, extra=extra)

    def _answer(self, outcome: str, status: int, body: bytes, content_type: str = "application/json",
                usage=None, finish="none", tools=(0, 0), extra: dict | None = None) -> dict:
        """Decide the record first, then send: totals must not include the client's next request."""
        record = self._decide(outcome, status, usage, finish, tools)
        if record is not None:
            self._send(status, body, content_type, extra)
        return record

    def _send(self, status: int, body: bytes, content_type: str = "application/json", extra: dict | None = None):
        if self.abandoned:
            return
        self.status_sent = status
        try:
            self.h.send_response(status)
            self.h.send_header("Content-Type", content_type)
            self.h.send_header("Content-Length", str(len(body)))
            for name, value in (extra or {}).items():
                self.h.send_header(name, value)
            self.h.end_headers()
            self.h.wfile.write(body)
            self.h.wfile.flush()
        except OSError:
            pass  # the client went away; the record still stands

    def _start_stream(self, resp) -> bool:
        if self.abandoned:
            return False
        self.status_sent = 200
        try:
            self.h.send_response(200)
            self.h.send_header("Content-Type", resp.getheader("Content-Type") or "text/event-stream")
            self.h.send_header("Cache-Control", "no-cache")
            self.h.end_headers()
            return True
        except OSError:
            return False

    def _write(self, data: bytes) -> bool:
        if self.abandoned:
            return False
        try:
            self.h.wfile.write(data)
            self.h.wfile.flush()
            return True
        except OSError:
            return False

    def _record(self, outcome: str, http_status: int, usage=None, finish="none", tools=(0, 0),
                totals: dict | None = None) -> dict:
        return {
            "schema": RECORD_SCHEMA,
            "seq": self.seq,
            "started_at_ms": self.started_ms,
            "duration_ms": int((time.monotonic() - self.t0) * 1000),
            "outcome": outcome,
            "http_status": http_status,
            "upstream_attempts": self.attempts,
            "stream": self.stream,
            "model_rewritten": self.model_rewritten,
            "usage": dict(usage or ZERO_USAGE),
            "finish_reason": finish,
            "tool_calls": {"count": tools[0], "arguments_valid_json": tools[1]},
            "request_sha256": _sha256(self.request_bytes),
            "response_sha256": self.response_sha,
            "totals": totals if totals is not None else self.gw.ledger.totals(),
        }


# -------------------------------------------------------------------- server


class Handler(BaseHTTPRequestHandler):
    server_version = "hb-gateway"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # one request per connection; responses end when the connection closes

    def setup(self):
        self.timeout = self.server.gateway.config.client_timeout_s  # applied to the socket by setup()
        super().setup()

    def log_message(self, format, *args):  # noqa: A002 - the records are the log
        pass

    def do_POST(self):  # noqa: N802
        Exchange(self, self.server.gateway).run()

    def do_GET(self):  # noqa: N802
        model = self.server.gateway.config.model
        entry = {"id": model, "object": "model", "created": 0, "owned_by": "hb-gateway"}
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).rstrip("/")
        if path == MODELS_PATH:
            self._json(200, {"object": "list", "data": [entry]})
        elif path == f"{MODELS_PATH}/{model}":
            self._json(200, entry)
        else:
            self._json(404, {"error": {"type": "not_found", "message": "unknown path"}})

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True  # shutdown records stragglers itself (Gateway.drain), then exits
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], gateway: Gateway):
        self.gateway = gateway
        self._stopping = threading.Event()
        gateway.on_fatal = self.stop_async
        super().__init__(address, Handler)

    def stop_async(self) -> None:
        """Stop serving from any thread, including a signal handler."""
        if not self._stopping.is_set():
            self._stopping.set()
            threading.Thread(target=self.shutdown, daemon=True).start()


def parse_listen(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not _is_decimal(port) or not host:
        raise ConfigError(f"--listen must be host:port, got {value!r}")
    return host, int(port)


def build_server(config: Config, log_path: Path, listen: str) -> GatewayServer:
    return GatewayServer(parse_listen(listen), Gateway(config, Path(log_path)))


def shutdown(server: GatewayServer, drain_s: float) -> int:
    """Stop serving, record requests still in flight after drain_s, close the log.

    Returns the exit status: 0, or 1 if a record could not be written.
    """
    server.stop_async()  # no-op if already stopping
    server.shutdown()  # returns once serve_forever() has stopped
    server.gateway.drain(drain_s)
    server.server_close()
    try:
        server.gateway.log.close()
    except OSError:  # a buffered write failed on close
        traceback.print_exc(file=sys.stderr)
        server.gateway.fatal = True
    return 1 if server.gateway.fatal else 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="hb-gateway", description="harnessbench model gateway")
    parser.add_argument("--listen", required=True, help="host:port to listen on")
    parser.add_argument("--upstream", required=True, help="provider base URL, e.g. https://host/v1/")
    parser.add_argument("--model", required=True, help="pinned model id")
    parser.add_argument("--max-total-tokens", type=int, required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--upstream-retries", type=int, required=True)
    parser.add_argument("--retry-base-delay-ms", type=int, default=1000)
    parser.add_argument("--connect-timeout-s", type=float, default=30.0)
    parser.add_argument("--read-timeout-s", type=float, default=600.0)
    parser.add_argument("--client-timeout-s", type=float, default=60.0)
    parser.add_argument("--drain-s", type=float, default=120.0,
                        help="on SIGTERM, how long to wait for requests in flight before recording them as abandoned")
    parser.add_argument("--upstream-ca-file", default=None, help="extra CA bundle for the upstream TLS check")
    parser.add_argument("--log", required=True, type=Path, help="JSONL file to append records to")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace, api_key: str) -> Config:
    return Config(
        upstream=Upstream.parse(args.upstream),
        model=args.model,
        api_key=api_key,
        max_total_tokens=args.max_total_tokens,
        max_calls=args.max_calls,
        upstream_retries=args.upstream_retries,
        retry_base_delay_s=args.retry_base_delay_ms / 1000,
        connect_timeout_s=args.connect_timeout_s,
        read_timeout_s=args.read_timeout_s,
        client_timeout_s=args.client_timeout_s,
        upstream_ca_file=args.upstream_ca_file,
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        config = config_from_args(args, os.environ.get("MODEL_API_KEY", ""))
        server = build_server(config, args.log, args.listen)
    except (ConfigError, OSError, ssl.SSLError) as exc:
        print(f"hb-gateway: {exc}", file=sys.stderr)
        return 2
    host, port = server.server_address[:2]
    print(f"hb-gateway listening on {host}:{port}", file=sys.stderr, flush=True)
    signal.signal(signal.SIGTERM, lambda signum, frame: server.stop_async())
    signal.signal(signal.SIGINT, lambda signum, frame: server.stop_async())
    server.serve_forever()
    return shutdown(server, args.drain_s)


if __name__ == "__main__":
    raise SystemExit(main())
