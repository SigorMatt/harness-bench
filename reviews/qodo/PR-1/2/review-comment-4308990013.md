<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

8\. Shutdown can erase a provider failure's void <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>abandon()</i></b> builds its own record from <b><i>admitted</i></b> and <b><i>counted_usage</i></b>, and ignores any record the
exchange thread has already built. So an exchange that has run <b><i>_fail()</i></b> (ledger voided,
<b><i>upstream_failed</i></b> record built, 502 being sent) but has not yet reached <b><i>_finish()</i></b> gets recorded as
<b><i>usage_unknown</i></b>. Exchanges whose answer is already decided are relabelled in the same way: a locally
answered <b><i>budget_exhausted</i></b> or void 502 becomes <b><i>bad_request</i></b>, and an <b><i>upstream_rejected</i></b> becomes
<b><i>usage_unknown</i></b>. When the drain deadline lands in that window, which includes a <b><i>_send()</i></b> to a slow
client that can block for up to <b><i>client_timeout_s</i></b>, <b><i>gateway.jsonl</i></b> has no <b><i>upstream_failed</i></b> record
and the gateway exits 0, so the toolkit never re-runs an attempt that hit a provider failure.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
`Exchange.abandon()` builds its own record and ignores any record the exchange already built (for example `upstream_failed` from `_fail`, `budget_exhausted`, or `upstream_rejected`). If shutdown abandons the exchange between building that record and calling `_finish`, the outcome is lost or relabelled. A lost `upstream_failed` means the void is lost.

## Fix Focus Areas
- hb/gateway.py[536-565]
- hb/gateway.py[850-855]

## Recommended Fix
In `_answer` (and in the mid-stream branch of `_fail`), store the record on the exchange under `_done_lock` (for example `self.pending_record = record`) before sending. In `abandon()`, if `pending_record` is set, write that record (optionally updating `duration_ms`) instead of building a new one. Alternatively, in `abandon()`, record `upstream_failed` whenever the ledger is void and this exchange called `_fail`.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/43fedd65-34b6-4efa-8c48-8ecfb11c5aa6?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/43fedd65-34b6-4efa-8c48-8ecfb11c5aa6)