# Result bundle, cohort key and acceptance — spec v0.1 (draft)

Status: draft for discussion. Scope: the v1 reference setup — one model on one
pinned endpoint, GitHub-hosted runners, the `stock` track, suite `py-maint` 1.x.

Machine-readable definitions are in [`schemas/`](../../schemas). The reference
implementation of every rule below is in [`hb/`](../../hb), and
[`examples/`](../../examples) holds a complete, generated example run.

## 1. Design rule: integrity by construction

Every integrity property in this document holds because of how a run is put
together — pinned code, isolated containers, signed artifacts, derived values —
not because something watches for misbehaviour. The aggregator may perform
exactly three kinds of check:

1. **Signature verification** of attestations.
2. **Equality and set comparison** against values fixed at a release.
3. **Recomputation** of anything derivable (IDs, grades, metrics).

It never searches for evidence of tampering, so no part of the system needs
adversarial testing to be trusted. Tests are functional: determinism, schema
conformance, fixture-based negative cases for our own parsers, assertions on
generated container configuration ([`tests/test_profile.py`](../../tests/test_profile.py)),
and end-to-end runs of the toolkit against a real Docker daemon that check what
it produces ([`tests/test_run_task_docker.py`](../../tests/test_run_task_docker.py)).
Isolation is verified by asserting on configuration, never by probing a sandbox.

Where a property cannot be made true by construction, this spec says so and
bounds it (§10) instead of adding detection.

## 2. Invariants

| | Invariant | How it holds |
|---|---|---|
| I1 | Every accepted artifact was produced by the official workflow at a release commit, on a GitHub-hosted runner, in the submitter's public repository. | GitHub artifact attestations, verified with `gh attestation verify --repo <submitter> --signer-workflow <official> --signer-digest <release sha> --deny-self-hosted-runners`. |
| I2 | Model, endpoint, suite, budgets, limits and the harness are fixed by the release. | The reusable workflow pins the toolkit and suite images by digest. Its only input is a harness id, looked up in the registry baked into the toolkit image, which pins each harness image by digest. Both images are built and attested by the release workflow from the parent of the release commit, which differs from it only in those two pins (§6.3). |
| I3 | Every model request is served by the pinned model. | The gateway has exactly one upstream and overwrites the request's `model` field. The harness has no other network route (I4). |
| I4 | The harness holds no authority beyond its workspace. | Container profile (§6): internal-only network shared with the gateway alone, placeholder API key, no Docker socket, read-only root, no capabilities, non-root user, fixed CPU, memory and process limits. |
| I5 | The harness cannot write trusted records. | The gateway log lives on a volume that is never mounted into the harness container, and the lifecycle record is written by the toolkit, in no container the harness can reach. Bundles are assembled after the harness container has stopped. |
| I6 | Nothing in the workspace's `.git` is ever executed or consulted. | The snapshot copies regular files only and skips `.git` at any depth. Diffs are computed with `git diff --no-index` on toolkit-made copies, outside any repository, with system and global config disabled. |
| I7 | Scores are derived, never declared. | The aggregator re-grades every rep from its patch. The `grade.json` inside a bundle is advisory. |
| I8 | The code under test cannot decide its own verdict. | Probes run next to the patched code and only emit observations. Expected values and the comparison stay in the trusted grader process (§7). |
| I9 | Edits to tests and interpreter hook files cannot make a rep pass. | Protected paths are restored from pristine before probing. The edits still count as edits: out of scope wherever the task's scope excludes them. |
| I10 | A result is one complete, first-attempt run, accepted once, under the identity it was submitted as. | The seal job runs only if every task job succeeded. Acceptance requires the signed run identity to equal the submitted repository and run id, `run_attempt = 1` and the full task list, and deduplicates on (repository, run id). Infra re-runs happen only when the gateway voids an attempt, and it voids only on provider-side or setup failures, never on anything that depends only on the harness's own behaviour (§5.2). Failures a harness could cause, such as a gateway that exits with an error, fail the job instead (§6.1). Re-runs run inside the job and are capped per rep and per run; a task job fails at a void that would take its own re-runs past the run's cap. |
| I11 | Identifiers cannot disagree with content. | IDs are `content_id()` of canonical JSON (§3.1) and are always recomputed, never accepted as claims. |
| I12 | Known caches are never counted as edits. | The suite's `ignore_paths` (bytecode, test-runner caches) are dropped when the workspace is snapshotted and again when a patch is applied. Anything else left in `/workspace` is part of the patch, so the adapter contract keeps harness state under `$HOME` (§6). |

## 3. Identities

### 3.1 Canonical JSON and content IDs

`content_id(v) = "sha256:" + hex(sha256(canonical_bytes(v)))`, where
`canonical_bytes` is JSON with sorted keys, no whitespace, ASCII only.

It is defined only for objects with string keys, arrays, printable-ASCII
strings, booleans and integers with |n| ≤ 2^53 − 1. Floats and `null` are
rejected. Restricting the value space makes the encoding unique without a
general-purpose canonicaliser ([`hb/canonical.py`](../../hb/canonical.py)); it is
also why prices are integer micro-dollars and timestamps integer milliseconds.

### 3.2 Cohort key

Everything that must be equal for two results to be compared. It is a
projection of `suite.json` plus the digest of the whole suite tree, so it is
never written by hand ([`hb/identity.py`](../../hb/identity.py)):

```json
{
  "schema": "hb/cohort/1",
  "suite": { "id": "py-maint", "version": "1.0.0", "digest": "sha256:…" },
  "model": {
    "provider": "nebius-token-factory",
    "base_url": "https://api.tokenfactory.nebius.com/v1/",
    "model_id": "Qwen/Qwen3-30B-A3B-Instruct-2507",
    "wire": "openai-chat-completions"
  },
  "runtime": {
    "runner": "github-hosted/ubuntu-24.04/x64",
    "harness_cpus_milli": 2000, "harness_memory_mib": 8192, "harness_pids": 512,
    "network": "gateway-only"
  },
  "budget": { "wall_seconds": 1200, "total_tokens": 2000000, "model_calls": 200 },
  "track": "stock",
  "reps": 3
}
```

- `suite.digest` is the content ID of the suite tree's sorted file list
  (path, sha256, size). Any change to any task changes the cohort.
- `model.model_id` also pins the flavor: on Nebius Token Factory the Fast flavor
  is selected with a `-fast` suffix on the model id.
- `runtime.network` has exactly one allowed value.
- Results are never ranked across cohorts.

### 3.3 Entry key

The harness under comparison, projected from its registry entry: id, version,
image digest and stock-configuration digest.

### 3.4 Release table

For each release commit, maintainers compute `releases/<sha>.json` in the
results repository ([`schemas/release.schema.json`](../../schemas/release.schema.json)):
the signer workflow and commit, the toolkit and suite images, and the cohort
IDs, entry IDs and task IDs that release can produce. Because cohort and entry
are projections of pinned files, this set is known before any run happens:
`scripts/release.py entry` computes it from the release commit's committed
bytes alone (§6.3). Acceptance is membership.

### 3.5 Leaderboard cells

A cell is `(cohort_id, entry_id)`. All accepted runs for a cell are pooled. A
run is identified by `(repository, run_id)`, with the repository compared
case-insensitively (`hb.accept.run_key`). The identity signed into the run
manifest must equal the identity the run was submitted as, so copies of a
run's signed artifacts carried into another run are rejected, and a second
submission of the same run is ignored.

## 4. Bundle layout

### 4.1 Task bundle

One archive per task per run:

```
task-manifest.json
reps/<n>/lifecycle.json
reps/<n>/gateway.jsonl
reps/<n>/workspace.patch
reps/<n>/harness.log
reps/<n>/grade.json
```

Exactly five files per rep, for n = 1…reps. Anything missing or extra fails
verification. Because the layout is fixed, a file's kind follows from its name
and is never declared.

| File | Written by | Trust | Aggregator use |
|---|---|---|---|
| `lifecycle.json` | toolkit | trusted | termination, attempts, wall time |
| `gateway.jsonl` | gateway | trusted | tokens, calls, cache share, tool-call validity, model time |
| `workspace.patch` | toolkit, from the harness's workspace | content untrusted, format trusted | re-graded |
| `harness.log` | harness | untrusted | display only |
| `grade.json` | toolkit grader | advisory | ignored; recomputed |

`task-manifest.json` lists `{path, sha256, size}` for the other files, sorted by
path, plus the run identity, task id and rep count.

**Packing** is deterministic ustar: sorted members, mtime 0, uid/gid 0, mode
0644, no directory entries (`hb.manifest.pack_bundle`), so the same content
always gives the same bytes. **Extraction** goes into an empty directory and
accepts regular-file members only (the standard library's `data` filter plus a
regular-file check); directory members are rejected too, since packing never
writes them.

### 4.2 Run manifest

Written and attested by the seal job: the run identity, the release images, the
full cohort and entry keys, and for every suite task (sorted) the digest and
size of its bundle archive ([`schemas/run-manifest.schema.json`](../../schemas/run-manifest.schema.json)).
§6.2 lists what is checked before a run is sealed.

## 5. Records

### 5.1 `lifecycle.json`

`termination` is one of:

- `exited`: the harness's entrypoint exited by itself, whatever its status;
- `wall_timeout`: the toolkit killed it at the suite's wall-clock limit;
- `budget_exhausted`: the toolkit killed it 30 seconds after the gateway first
  refused one of its requests for budget (a harness that exits within that
  grace period is `exited`).

`exit_code` is the container's exit status (137 after a kill). The timestamps
are taken by the toolkit just before it starts the harness and when it sees
the harness stopped. There is no infra-error value: an attempt the gateway
voided (§5.2) is re-run with a fresh network, gateway and workspace, up to
`infra.max_reruns_per_rep` times, or the job fails. Sealed bundles therefore
cannot contain one. The job also fails, instead of re-running, at a void that
would take its own re-runs past what the whole run may have
(`infra.max_rerun_permille` of all the run's reps, rounded down), since seal
would refuse that run (§6.2). It counts only its own re-runs, so it never stops
a run that could still be accepted; re-runs spread over several task jobs are
left to seal. A run of the one-task example suite may have no re-run at all.
`attempts` records how many attempts the rep took. The task job's timeout is
derived from the suite (`hb.plan.task_job_timeout_minutes`): it allows for
every rep using every allowed attempt, as an estimate rather than a bound
(§10), and a suite that cannot fit in a GitHub-hosted job fails at planning.

### 5.2 `gateway.jsonl`

Written by the gateway ([`hb/gateway.py`](../../hb/gateway.py)): one record per
POST whose request line and headers parse. Requests the HTTP layer rejects
earlier (400, 414, 431) never reach the gateway's logic or the provider and are
not recorded. Request and response bodies are stored only as hashes. Main
fields:

- `outcome`: `ok`, `upstream_rejected` (the provider answered 400, 413 or 422, for example context too long), `budget_exhausted`, `bad_request`, `usage_unknown` or `upstream_failed`
- `upstream_attempts`: 1 plus transparent retries; 0 when the gateway answered without contacting the provider
- `usage`: input tokens (including cached), cached input tokens, output tokens
- `tool_calls`: count, and how many had arguments that parse as a JSON object
- `model_rewritten`, `stream`, `finish_reason`, `http_status` (as returned to the harness) and running `totals`

**Endpoints and checks.** `POST /v1/chat/completions` (a trailing slash is
accepted) is the only request that reaches the provider. `GET /v1/models` and
`GET /v1/models/<pinned id>` are answered locally with the pinned model only and
are not recorded. A POST is checked in this order, and the first failing check
answers it as `bad_request`: body framing (411 without a Content-Length or
chunked body, 413 over 32 MiB, 408 if the body stops arriving for the client
timeout of 60 seconds, 400 for invalid framing), path (404), JSON object (400).
These checks also answer requests that arrive after a void. Every problem found
before a request is admitted, including a fault in the gateway itself, is
answered as `bad_request` and leaves the attempt running. Request bodies are
re-encoded with ASCII escapes, so any valid JSON string, including a lone
surrogate from a string cut in the middle of an emoji, is forwarded faithfully.

**What reaches the provider.** The gateway changes requests in exactly two fixed ways:

1. `model` is set to the pinned model id.
2. Streaming requests get `stream_options.include_usage = true`, so usage is
   always counted. If the client did not ask for it, the usage-only chunk is
   removed from what the client receives.

The upstream request carries a fixed header set: the real API key, content type,
accept and user agent. No header from the harness is forwarded, and the real key
never reaches the harness. Plain http is accepted only for a loopback upstream
(test stubs).

**Budgets.** Each request is admitted only while `totals` is below the rep's
token and call budgets; the call is counted at admission. After that, requests
are answered locally with HTTP 429, error type `budget_exhausted`, code
`insufficient_quota` and `x-should-retry: false` (so standard OpenAI clients do
not retry), and recorded as `budget_exhausted`. A request already in flight when
the token budget is crossed completes, so the overshoot is bounded by the
number of concurrent requests. The toolkit then ends the rep after a 30-second
grace period.

**Provider failures.** Retries happen only before any byte has reached the
client and only when the provider cannot be working on the request: connection
and TLS failures before the request was fully sent (the provider never held a
complete request), and 408, 429 and 5xx answers. They use exponential backoff,
honouring `retry-after-ms` or `Retry-After` (seconds or an HTTP date), capped at
30 seconds, up to `infra.upstream_retries` times. Once the request was fully
sent, nothing is retried, because the provider may already be generating, and
billing, an answer the gateway would never count; a retry could bill twice while
the record shows one call. A timeout is then `usage_unknown`, and a connection
that fails or closes before any response is a void. When a request would void
and any of its attempts failed before the request was fully sent, the job fails
instead (below).

**Void attempts are a re-roll, so only provider-side and setup failures void.**
A void re-runs the rep. If anything that depends only on the harness's own
behaviour could void, a harness could re-roll reps that were going badly. The
attempt becomes void, recorded as `upstream_failed`, when:

- retries run out;
- the connection fails or closes after the request was fully sent and before any response (it is not retried, see above);
- the provider answers with any status other than 200, 400, 413, 422 or a retryable one — 401, 403 and 404 mean the key, model id or base URL is wrong, which is a setup fault, not a harness result;
- a stream breaks off (the connection fails or closes early; a read timeout is `usage_unknown` instead), or a response carries no usage the gateway can count;
- the gateway itself faults after admitting a request.

**A record that cannot be written fails the job.** The gateway then refuses
further chat requests, stops and exits with status 1. Its log is incomplete,
and the usual cause, a full disk, can be brought about by the harness, so the
toolkit does not re-run the attempt: it fails the job, and the run cannot be
sealed. The same holds for any other non-zero exit status, for example a
gateway killed at its memory limit (§6.1).

**A provider that cannot be reached fails the job.** When a request would void
for any of the reasons above and at least one of its attempts failed before
the request was fully sent (the connection, the TLS handshake or the send
failed), the gateway records and answers that request like a void, so later
requests are answered locally. It also prints the reason and exits with status
3, and the toolkit fails the job instead of re-running the attempt. Such
failures can be brought about by the harness: a kernel table that every
container on the runner shares, connection tracking, can be filled by flooding
the gateway's address, and then new connections to the provider fail. The rule
covers the whole request, not only retries running out, so no void ever
follows a connection the harness could have stopped. The exit status is set
before the record is written, so a toolkit that reacts to the record always
finds it. The cost is that a provider that stays unreachable through all of
one request's retries, or fails in another way after such an attempt, fails
the whole run instead of one rep being run again.

**`usage_unknown`: the gateway stopped waiting, and the attempt goes on.** A
forwarded request is recorded as `usage_unknown` when the gateway stops waiting
for it:

- the provider sends nothing for the read timeout (600 seconds) at any point
  after the request was sent: before the response headers, in the middle of a
  response body, or between stream events. It is never retried. If nothing has
  been sent to the harness yet, the answer is a 504; a stream that has already
  started ends with a `data: {"error": …}` event of type `upstream_timeout`;
- it is still in flight when the gateway shuts down after its drain period
  (120 seconds).

Both depend on what the harness asked for and when it exited, so neither voids.
The record holds exactly the usage counted, which is zero unless a stream's
usage had already been counted; in that case the record is `ok`. The provider
may have billed tokens that the record cannot show; the leaderboard shows the
count of these requests next to the token totals (§10).

**Shutdown.** On SIGTERM the gateway stops accepting connections and waits up to
the drain period for requests in flight. It then refuses new requests (503, not
recorded, since they never reach the provider) and records the rest. A request
whose answer was already decided keeps that record and is still sent that
answer, so a void already declared stays `upstream_failed`. Otherwise the record is `usage_unknown` if the request
was admitted (or `ok`, if a stream's usage was already counted), and
`bad_request` (HTTP 503) if not. Counting usage, voiding, deciding an answer and
abandoning a request exclude each other, so the record holds exactly the usage
the totals counted. The gateway closes their upstream connections, cuts any
retry backoff short and never sends an abandoned request upstream, so nothing
more reaches the provider for them. The orchestrator must stop the gateway with a timeout longer than the
drain period.

From then on the gateway answers every chat request that passes the checks
locally (HTTP 502, `upstream_failed`, `upstream_attempts: 0`,
`x-should-retry: false`). A stream that fails after it has started ends with a
`data: {"error": …}` event, so standard OpenAI clients raise instead of taking
the cut-off answer as complete. The void and the record are in place before that
event is sent, so a client that reacts to it is answered locally. Stream headers are sent only with the first
event, so a stream that fails before producing anything gets a plain 502. The
toolkit ends a void attempt as soon as the record appears in the log, and
re-runs it under §5.1, so sealed bundles contain no `upstream_failed` records.

Provider rejections (`upstream_rejected`) are passed through unchanged and do
not void the attempt: reacting to them, for example by compacting context, is
part of what the harness is measured on.

**Accounting.** A stream's usage is counted once, when its usage-only chunk
(usage and no choices) arrives, before `[DONE]` reaches the client. If usage
rides on a chunk that also has choices, it is counted when the stream ends. The
record reports exactly the usage that was counted. If the client disconnects
mid-stream, the gateway keeps reading to the end, because those tokens are
billed anyway. A record's `totals` are taken before its answer is complete, so
they never include the client's next request.

**State file.** Next to the log, the gateway keeps `/log/state.json`
([`schemas/gateway-state.schema.json`](../../schemas/gateway-state.schema.json)):
whether the attempt is void, and when the gateway first refused a request for
budget. It is written at startup and replaced atomically (a temporary file,
then a rename) when either fact first becomes true; the void flag is set
before the record of the request that voided the attempt is written. The
toolkit reads it while the harness runs to time its early kills (§6.1). It is
small and bounded, so reading it costs the same however many requests the
harness makes, and the toolkit never reads the growing log until the gateway
has stopped. It is not part of any bundle, and whether an attempt is void is
always decided from the records. A write that fails after startup stops the
gateway with exit status 1, as a record it cannot write does, and the job
fails: a full disk can be the harness's doing, and a refusal missing from the
file would spare the harness its grace-period kill.

**Derived efficiency metrics** (`hb/metrics.py`): tokens, cached share, model
calls (requests that reached the provider), tool-call parse failures, model
time (time spent on those requests, including retry backoff and relaying) and
non-model time (wall time minus model time). Cost is tokens times the dated
price table kept in the results repository (`schemas/prices.schema.json`),
applied at display time. Prices are deliberately not in the suite: the suite
digest is part of the cohort, so a price change would otherwise split
cohorts. Wall time is shown but not ranked, because it depends on provider
latency.

### 5.3 `grade.json`

`resolved`; `target` and `preservation` check counts; `scope` (strict flag and
out-of-scope file count); `normalization.protected_restored`; diff statistics.

## 6. Harness container profile

Implemented in [`hb/profile.py`](../../hb/profile.py) and tested as data. For each
rep attempt:

```
docker network create --internal <net>
docker volume create <ws>; docker volume create <task>; docker volume create <gwlog>
docker create --name <helper> --network none \
  --mount type=volume,src=<ws>,dst=/workspace --mount type=volume,src=<task>,dst=/task \
  <toolkit@digest>
docker run --detach --name <gw> --network bridge --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user 10002:10002 \
  --memory 2048m --memory-swap 2048m --pids-limit 1024 --ulimit nofile=8192:8192 \
  --log-driver json-file --log-opt max-size=8m --log-opt max-file=2 \
  --mount type=volume,src=<gwlog>,dst=/log --env MODEL_API_KEY \
  <toolkit@digest> gateway --upstream <base_url> --model <model_id> --drain-s 120 \
  --log /log/gateway.jsonl --state /log/state.json ...
docker network connect --alias gateway <net> <gw>
docker create --name <harness> --network <net> \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,size=1024m \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
  --cpus 2.000 --memory 8192m --memory-swap 8192m --pids-limit 512 \
  --log-driver json-file --log-opt max-size=8m --log-opt max-file=2 \
  --mount type=volume,src=<ws>,dst=/workspace \
  --mount type=volume,src=<task>,dst=/task,readonly \
  --workdir /workspace \
  --env HOME=/tmp --env OPENAI_BASE_URL=http://gateway:8080/v1 \
  --env OPENAI_API_KEY=hb-no-credential --env HB_MODEL=<model_id> \
  --env HB_TASK_FILE=/task/instruction.md \
  <harness image@digest> /hb/entrypoint
docker start <harness>
```

- `--env MODEL_API_KEY` names the variable only, so the key never appears in an
  argument list or a log line.
- Sibling containers are created through the host's daemon and cannot see the
  toolkit's files, so trees travel as tar streams (`hb/transfer.py`). The
  helper container mounts the workspace and task volumes and is never started:
  the pristine repository (owned by the harness user) and the instruction are
  copied into it with `docker cp --archive`, and the workspace is copied out of
  it after the harness has stopped. A new volume takes its owner from the
  toolkit image's directory at its mount point, which is how `/workspace`
  belongs to the harness user and `/log` to the gateway user.
- The gateway log volume, with the log and the state file, is mounted only into
  the gateway; the toolkit copies them out (I5).
- A harness can open many connections to the gateway. Each holds one thread
  and at most three file descriptors, and the descriptor limit is pinned at
  more than three times the thread limit. A flood therefore always meets the
  thread limit first, which turns new connections away before any request is
  admitted. It can never make an admitted request's upstream connection fail
  for lack of descriptors, which would fail the job (§5.2).
- The harness container is created and started rather than run with `--rm`, so
  its exit status and output can be read after it stops. Its output, like the
  gateway's, is capped by the log options, so it cannot fill the runner's disk.
- Every rep attempt gets its own network, gateway and workspace, so reps cannot
  see each other and budgets reset by construction.
- Every task runs in its own job, so on a fresh GitHub-hosted runner VM.

**Adapter contract.** A harness image reads the instruction from
`$HB_TASK_FILE`, works in `/workspace`, uses the OpenAI-compatible endpoint at
`$OPENAI_BASE_URL` with model `$HB_MODEL`, runs without prompts and exits when
done. `/workspace` holds the task's repository without a `.git` directory; a
harness that needs one creates it (`.git` is never part of the snapshot). It
keeps its own state (history, caches, config) under `$HOME`, not in
`/workspace`: anything left in `/workspace` is part of the patch unless it
matches the suite's `ignore_paths`. Telemetry and update checks are switched off
(there is no route for them anyway). That is the whole adapter: a thin image
layer and an entrypoint script.

### 6.1 Running a rep

`hb.orchestrator` (`python3 -m hb run-task` in the toolkit image) runs every rep
of one task in turn:

1. Set up the attempt as above and wait until the gateway listens.
2. Start the harness and watch it. It is killed at the wall-clock limit, 30
   seconds after the gateway first refuses one of its requests for budget, or
   as soon as the attempt is void (§5.1). The last two come from polling the
   gateway's state file (§5.2) every 2 seconds. Each read is bounded by the
   time left before the next deadline, so a slow read cannot delay a kill; a
   read that fails only delays an early kill, the grace period runs from the
   time in the file, and failures in a row are reported.
3. Stop the gateway with `docker stop --time 150`, longer than its 120-second
   drain. Copy out `gateway.jsonl`. If any record is `upstream_failed`, the
   attempt is void: it is discarded and the rep runs again in fresh containers,
   unless that would pass the rep's re-run limit or take this job's own re-runs
   past the run's cap (§5.1).
4. Copy out the harness's output and its workspace. The snapshot keeps
   directories, regular files and hard links to them, and skips everything else
   (I6). It is diffed against pristine with `hb.patch.workspace_diff`.
5. Remove the attempt's containers, network and volumes, and the toolkit's
   local copies of its files, whatever happened.
6. Grade the rep (§7), remove the grading copies, write its five files, and
   after the last rep write the manifest and the archive
   `task-bundle-<task>.tar`.

**What fails the job.** Anything the toolkit cannot trust or finish: a Docker
command that fails or times out, a gateway that does not start, stops while the
harness runs or exits with a non-zero status (a record or the state file that
could not be written, a provider that could not be reached, a kill), a malformed gateway
log, a void on every allowed attempt, a void that would take the job's own
re-runs past the run's cap, or a probe sandbox that cannot start. A harness can
cause some of these, for example by filling the disk, by filling a kernel table
the gateway's connections need, or by leaving more than the toolkit accepts:
1 GiB of files or 200,000 entries in `/workspace`, or a
gateway log over 256 MiB. That is why they fail the job, which loses the whole
run, instead of voiding the attempt, which would re-roll one rep. The gateway's
memory and process limits keep a harness that floods it from taking the runner
down.

### 6.2 Planning and sealing

Two short jobs frame the task jobs. Both run the toolkit image with no network
and the extracted suite mounted read-only.

**Plan** (`python3 -m hb plan`, `hb.plan.plan_run`) runs before any task job,
so a run that it can tell could not complete fails before any tokens are
spent; the task job timeout it writes is an estimate (§10). It refuses
a harness id that is not in the registry, a task list that is empty, unsorted,
duplicated or longer than 256 (one GitHub Actions matrix), a listed task whose
`task.json` is missing or names another task, and a suite whose task jobs
cannot fit in a hosted job (§5.1). It writes the task ids as one JSON line for
the matrix, and the task job timeout.

**Seal** (`python3 -m hb seal`, `hb.seal.seal_run`) runs only after every task
job succeeded. The task bundles are downloaded each into a directory named
after its artifact, so for every suite task in order seal reads exactly
`task-bundle-<task>/task-bundle-<task>.tar`, the path acceptance verifies
(`hb.accept.bundle_artifact_file`); other files are ignored, and a file that
another artifact carries under the same name cannot take its place. It
recomputes the cohort and entry from the suite and the registry. Each bundle
must be a regular file; it is hashed and extracted from the same open file
(regular-file members only, §4.1), and must verify against its own manifest
and name this run, this task and the suite's rep count. Every rep's lifecycle
must name its task, its rep and the registry's harness image. The attempts of
all reps must stay within the suite's infra rules (`hb.accept.check_attempts`),
and the run must be a first attempt (I10). Only then is the run manifest
written, with each bundle's digest and size, and attested.

Sealing adds no trust: other jobs in the caller's workflow can upload
artifacts too, so acceptance verifies every bundle's own attestation and
repeats the run-level checks (§8). A run that fails any check made here is
never sealed. Schema validation and re-grading happen only at acceptance, and
the lifecycle checks are seal's own consistency checks; acceptance relies on
the attestation of the pinned toolkit that wrote those records.
`scripts/make_examples.py` builds the example run manifest with the same
function.

A run that is not a first attempt can never be accepted, so plan, run-task
and seal all refuse one before doing anything: after GitHub's "re-run failed
jobs" or "re-run all jobs", no tokens are spent on a run that could not
count. Start a new run instead.

### 6.3 Releases

A release is what a submitter pins: one commit of this repository, the release
commit, whose `bench.yml` pins a toolkit image and a suite image by digest
(I2). Results are accepted only from the release commits in the release table
(§3.4).

**Images.** `.github/workflows/release.yml` runs when a maintainer starts it,
from main only, with the version (`vMAJOR.MINOR.PATCH`) as its one input. A
first job, with no write permission, checks the release inputs
(`scripts/release.py inputs`): the suite at `suites/py-maint/` and the
registry at `registry.json` must pass their schemas and plan (§6.2), and the
repository may hold no `.gitattributes` file, so a checkout holds exactly the
committed bytes. Every path a task names that a run reads must be there too (§7,
Task layout): a run would not notice a missing `repo` directory but start every
rep from an empty workspace. Those checks need packages from PyPI, which is why
they run apart. The release job, which can push and attest, takes nothing from
the first job and runs only this repository's code, with the runner's own Python
and the standard library, pinned actions and images pinned by digest; the one
exception is git, which the toolkit base installs from Debian's archive (see
below). On a GitHub-hosted runner it:

1. names the images (`scripts/release.py names`) and checks that the
   version's tag does not exist yet;
2. builds the toolkit image (`docker/toolkit.Dockerfile`: `hb/`, `schemas/` and
   the registry) on `docker/toolkit-base.Dockerfile`, whose Python and docker
   CLI images are pinned by digest, and the suite image
   (`docker/suite.Dockerfile`: the suite directory is the build context and is
   copied to `/suite`, with nothing else);
3. checks them as `bench.yml` uses them: `/suite` copied out of a created
   container is identical to the suite directory, the toolkit's registry is
   identical to `registry.json`, and plan succeeds in the toolkit image with no
   network;
4. pushes both to GHCR, and pulls each back by the digest the push recorded;
5. attests both with build provenance, stored in GHCR and in GitHub, which
   names the commit they were built from (the build commit) and
   `refs/heads/main`;
6. uploads `release-pins.json`: the version, the build commit and both image
   references.

**The release commit.** A workflow's own token cannot change workflow files,
so a maintainer makes the release commit, in a full clone at the build commit:
`scripts/release.py pin release-pins.json` rewrites the two pin lines of
`bench.yml` and nothing else, and `git commit` records it. The release commit
therefore has exactly one parent, the build commit, and differs from it only in
those two lines, which neither image contains: the code at the release commit
is the code its images were built from.

Before the tag is pushed, `scripts/release.py verify` checks the commit with
git plumbing whose NUL-separated output no name, quoting, submodule or filter
setting changes, and with replace refs off: one parent; one changed path,
`bench.yml`, with its file mode unchanged; only the two pin lines changed; both
pins in this release's image repositories; no `.gitattributes` file. Then it
runs:

- `docker pull` of each pinned image with an empty Docker configuration
  against the current Docker context's daemon. The pull is anonymous, so it
  succeeds only for a public image (GHCR packages start private: a maintainer
  makes both public once, after the first release workflow run);
- `gh attestation verify oci://<image> --repo SigorMatt/harness-bench
  --signer-workflow SigorMatt/harness-bench/.github/workflows/release.yml
  --source-digest <build commit> --source-ref refs/heads/main
  --deny-self-hosted-runners` for each, which needs a GitHub login.

Anyone with a GitHub login can repeat these checks from the tag.
`scripts/release.py entry` computes the release table entry (§3.4) from the
release commit's committed bytes alone, read with `git cat-file` so that no
checkout filter or setting of the clone applies (`hb.release.release_entry_at`).
It writes those bytes to a new directory and refuses the commit unless the
directory then holds exactly the committed paths: on a case-insensitive file
system (the default on macOS and Windows), two paths that differ only in case
become one file, and the suite digest would not be the suite image's.
`scripts/make_examples.py` builds the example entry with `release_entry`, the
function `release_entry_at` calls.

**Tag only.** The release commit is pushed as the tag `vMAJOR.MINOR.PATCH` and
never merged into main, whose pins stay placeholders: a caller that pins
`bench.yml` to a branch instead of a release commit fails at its first step
rather than spending tokens on a run acceptance would refuse. The tag is the
only ref that keeps the release commit reachable, so the repository has a
ruleset, set up once before the first release, that blocks deleting or moving
`v*` tags: a deleted tag could let the commit be garbage-collected, and every
caller's `@<sha>` with it. A clone that does not
have the tag yet fetches it with `git fetch origin tag vMAJOR.MINOR.PATCH`,
since a plain fetch brings no tag whose commit is on no fetched branch. CI runs
on branches only (main's pins are placeholders, which a test checks), so a
release tag does not start it.

Neither image is bit-for-bit reproducible: images record build timestamps, and
the toolkit base installs git from Debian's archive at build time. That does not
weaken a release: `bench.yml` pins the images the release workflow built and
attested, by digest, not a recipe for building them.

## 7. Grading

The same function, `hb.grade.grade_rep`, runs inside the run (advisory) and at
the aggregator (authoritative):

1. Pristine tree = the task's `repo/`, materialised (regular files, normalised modes).
2. Apply `workspace.patch` to a copy with `git apply` outside any repository,
   then materialise again, so the graded tree holds regular files only and none
   of the suite's `ignore_paths`. All paths are resolved to absolute first.
3. Record which files differ from pristine (for scope). This happens before the
   next step, so edits to protected paths count as edits.
4. Restore protected paths from pristine.
5. Run the probe in a sandbox container (`hb.orchestrator.DockerProbeRunner`):
   the toolkit image, a fresh volume holding the patched tree, the probe and its
   inputs, mounted read-only; no network, no capabilities, a non-root user,
   fixed CPU, memory and process limits, a time limit, and no log driver. The
   report is read from the attached stdout, at most 1 MiB; a probe over its time
   or that limit returns empty output. The probe keeps a private copy of stdout for its
   report and points file descriptor 1 at stderr before importing the code, so
   anything the code prints cannot corrupt the report. It reports observations —
   return values or exception type names — as JSON.
6. In the grader: parse the observations (malformed or missing output means
   every check fails) and compare them with `checks/target.json` and
   `checks/preservation.json` using strict JSON equality (`true ≠ 1`, `1 ≠ 1.0`).
7. `resolved` = every target and preservation check passed, and no out-of-scope
   edits if the task's scope is strict (restraint tasks).

**Probe runner contract.** Whatever the code under test does — hang, crash,
exit early, print — the runner returns normally, with empty output on timeout,
so the outcome is "every check fails", never an aborted grading. The runner
raises only for infrastructure faults, such as a sandbox that cannot start;
those fail the job, so the run cannot be sealed.

**Why this holds by construction.** Expected values never enter any sandbox, and
the comparison runs in a process the patched code cannot reach. All the code
under test can do is produce outputs, and producing the expected outputs for
hidden inputs is the task itself. In the public suite, inputs and expectations
are public; that gap is what the private holdout suite covers (§10).

### Task layout

```
tasks/<id>/
  task.json        id, category, scope, probe, checks
  instruction.md   shown to the agent (mounted at /task)
  repo/            pristine repository (copied into /workspace)
  probes/          probe program and inputs (grading sandbox only)
  checks/          expectations and reference solution (never in any sandbox)
```

Curation rules that follow from this design:

- Pristine fails the target checks and passes the preservation checks.
- The reference solution passes both.
- Preservation expectations are recorded from pristine.
- Observations must be hard to produce without correct behaviour ("the module
  imported" is not an observation).

## 8. Acceptance procedure

For a submitted run (repository R, run id N), the aggregator:

1. Skips the submission if `run_key(R, N)` was already accepted.
2. Downloads the run's artifacts.
3. Verifies the attestation of exactly the expected files — `run-manifest.json`
   and one bundle archive per suite task, named from the release's task list
   (`hb.accept.expected_artifact_files`, `attestation_commands`). Each command
   runs as an argument list, never through a shell. Other files in the
   download are ignored.
4. Extracts each archive into an empty directory (`extract_bundle`) and verifies
   it against its `task-manifest.json` (`verify_task_bundle`).
5. Runs `hb.accept.check_run`: the signed run identity equals (R, N); release
   images, cohort and entry are in the release table; `run_attempt` is 1; the
   task list equals the suite's; every bundle digest, run identity, task id and
   rep count match.
6. Checks every rep's attempts against the suite's infra rules
   (`check_attempts`: per-rep limit and per-run cap).
7. Validates every record against its schema.
8. Re-grades every rep (§7) and derives metrics from trusted records only.
9. Commits the results under `results/<cohort_id>/<entry_id>/<R>/<N>/` and
   rebuilds the static leaderboard.

Any failure stops the procedure with its reason. Nothing is partially accepted.

**Artifacts inside a run are not trusted on their own.** The caller's workflow
can contain jobs of its own, and those can upload artifacts into the same run.
That is why every bundle is attested by the job that built it and verified
individually here: a replaced artifact has no attestation from the signer
workflow, so it fails step 3.

**Intake** ([`templates/results-intake.yml`](../../templates/results-intake.yml))
runs from the results repository's default branch and is triggered by an issue
form, so no code from a submitter's pull request ever runs. The issue body is
parsed with a strict pattern. Patched code runs only in the grading sandbox. The
one job with write access consumes only derived JSON.

## 9. What a leaderboard cell shows

pass@1 (mean over tasks and reps), pass^k (all k reps pass), 95% confidence
intervals from a bootstrap over tasks (paired across entries within a cohort),
median tokens, cost and model calls, the number of `usage_unknown` requests next
to the token figures, tool-call parse-failure rate, and the rates of
protected-path restores, out-of-scope edits and infra re-runs.

## 10. Residual risks — stated and bounded, not detected

- **Best-of-N whole runs.** A submitter can run several times and submit only
  the best run. With 100 tasks × 3 reps, the run-to-run standard deviation of
  pass@1 is about 2 points (assuming an average per-rep variance p(1−p) ≈ 0.15).
  The expected gain from best-of-5 is about 1.2 standard deviations, roughly
  2–3 points, which is inside the displayed interval and costs five times the
  tokens. Pooling every run per cell and periodic maintainer reproductions
  shrink it further.
- **Memorising the public suite.** Public inputs and expectations can be
  memorised by a harness or a model. Maintainers run a private holdout suite on
  the top entries at each release and publish the public-vs-holdout gap.
  Patches are public, so hard-coded answers are visible to anyone.
- **Trusted platforms.** GitHub-hosted runners, GitHub's attestation service
  and Sigstore, and the inference provider are trusted.
- **Provider drift.** The cohort pins the model id and flavor, not the
  provider's serving stack. Results carry dates, and maintainers re-run the
  baseline at each release, so drift shows up as a baseline shift.
- **Wire protocols.** Harnesses that only speak other APIs are out of v1 rather
  than translated, because translation would change what is measured.
- **Provider errors a request can provoke.** Voids are triggered only by
  provider-side signals, but a request's content might make a provider fail
  (for example, a 5xx on an unusual input) and so cause a re-run. Re-runs are
  capped per rep and per run, and the leaderboard shows each cell's re-run rate.
- **Tokens the gateway cannot see.** A `usage_unknown` request may have been
  billed for tokens that are not in the totals or the token budget. Each one
  costs the harness at least the read timeout (600 seconds) of its wall-clock
  budget, or happens after it exited. Their answers never reached the harness
  complete: at most the start of a stream did, ended by an error event that
  standard OpenAI clients raise on, so the harness never received a finished
  answer from those tokens. The leaderboard
  shows the count next to the token totals. Converting non-streaming requests to
  streaming upstream would remove the timeout case, but it would change what
  reaches the provider and is left for later.
- **Job failures a harness can cause.** A harness can make its own task job
  fail (§6.1), for example by filling the runner's disk, or by filling the
  connection-tracking table every container on the runner shares so that the
  gateway cannot reach the provider (§5.2). That loses the whole run, so it is
  never a way to re-roll a rep; at most it is a costly form of the best-of-N
  whole runs above. In exchange, a provider that stays unreachable through all
  of one request's retries, or fails in another way after such an attempt,
  fails the run too, rather than one rep being run again.
- **The task job timeout is an estimate, not a bound.** The task job timeout
  (`hb.plan.task_job_timeout_minutes`, §5.1) gives every allowed attempt the
  wall-clock limit, the budget grace, the gateway's 120-second drain, the
  longest probe and 60 seconds for everything else, plus 600 seconds per job
  for setup. The toolkit's own limits allow an attempt more: 30 seconds for
  the gateway to listen, 30 for a killed harness to stop, a 150-second stop
  grace for the gateway (210 for the `docker stop` call) and 600 seconds for
  the workspace snapshot, and any other Docker call may take 120, while the
  tree copies, the diff and the patch applied for grading are not timed, on a
  workspace of up to 1 GiB or 200,000 entries. Usually what a job does not use
  covers this: the grace never extends the wall clock, the probe runs once per
  rep, and an attempt that is never run leaves its whole allowance unused. For
  the example suite (3 reps of 1200 seconds, 2 re-runs per rep, 226 minutes) a
  job taking the listen, stop and snapshot limits to their maximum runs out
  only after 5 or 6 void attempts that each end near their wall-clock limit.
  A run of that one-task suite may have no re-run, so its task job stops at its
  first void (§5.1); with the same settings, a run may have 5 re-runs only from
  34 tasks up. A suite with fewer reps or re-runs has less to spare, and with
  one rep and none a snapshot that takes its full 600 seconds can end the job
  by itself. A job ended by its timeout writes no bundle, so the run is never
  sealed: it costs the run, never its integrity, and when a harness causes it
  with a large workspace it is one of the job failures above. Budgeting the
  snapshot and the probe once per rep and each toolkit limit per attempt, and
  timing the copies, the diff and the patch apply, would bring it close to a
  bound; that is left for later.

## 11. Versioning

Every document carries `schema: hb/<kind>/<major>`. Adding optional fields keeps
the major version; anything that changes meaning increments it. Suites are
frozen by digest: a changed task is a new suite version and therefore a new
cohort.

## Implementation map

| Module | Responsibility |
|---|---|
| `hb/canonical.py` | canonical JSON, content IDs |
| `hb/identity.py` | cohort and entry keys |
| `hb/manifest.py` | layout, hashing, deterministic packing, safe extraction, verification |
| `hb/tree.py` | materialising trees, ignore and protected paths, scope |
| `hb/patch.py` | workspace diff and patch application outside any repository |
| `hb/profile.py` | container profiles as data |
| `hb/gateway.py` | model gateway: single upstream, budgets, retries, usage accounting, records |
| `hb/orchestrator.py` | run-task: rep attempts, wall clock and budget grace, voids and re-runs, probe sandbox, task bundle |
| `hb/transfer.py` | tar streams into and out of containers: packing toolkit trees, the workspace snapshot |
| `hb/dockercli.py` | Docker CLI calls as argument lists with timeouts, and the clock |
| `hb/cli.py` | `python3 -m hb plan`, `run-task`, `seal` and `gateway` in the toolkit image; inputs from the environment |
| `hb/plan.py` | values derived from the suite: the task matrix and the task job timeout (`plan_run`) |
| `hb/seal.py` | sealing a run: one verified bundle per task, run identity, re-run limits, the run manifest |
| `hb/grade.py` | observation comparison, verdict, probe runner contract, `grade_rep` |
| `hb/metrics.py` | usage and cost from gateway records |
| `hb/accept.py` | expected files, attestation commands, run acceptance, attempt rules, dedup key |
| `hb/release.py` | releases: the release inputs, `bench.yml`'s pins, the release commit's checks, the release table entry (`scripts/release.py`, `.github/workflows/release.yml`) |
| `hb/schemas.py` | schema loading and validation |
