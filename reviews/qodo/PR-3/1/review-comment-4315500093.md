<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

3\. Gateway log is re-copied in full every poll <code>🐞 Bug</code> <code>➹ Performance</code>

<pre>
<b><i>_watch_harness</i></b> calls <b><i>_poll_records</i></b> about every <b><i>poll_s</i></b> (2 s). Each call runs <b><i>docker cp</i></b> on the
whole <b><i>/log/gateway.jsonl</i></b> and buffers it in memory through <b><i>read_single_file</i></b>, and only then does
<b><i>RecordTail.feed</i></b> throw away everything before <b><i>self.offset</i></b>. Over a long rep the total copy and
parse work grows with the square of the log size, up to 256 MiB per poll. The per-poll
<b><i>LOG_READ_TIMEOUT_S</i></b> and the caller swallowing <b><i>DockerError</i></b>/<b><i>TransferError</i></b> mean a slow copy just
ends without a result, so the early <b><i>upstream_failed</i></b> and budget-refusal detection quietly stops
working once the log is large.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
While the harness runs, the orchestrator copies the whole gateway log out of the container on every poll (every 2 s), even though it only parses lines after `RecordTail.offset`. The cost grows with the log size, and slow copies time out silently, which turns off early void and budget detection.

## Fix Focus Areas
- hb/orchestrator.py[178-187]
- hb/orchestrator.py[234-257]

## Recommended Fix
Read only the tail you have not seen yet. One option: `docker exec`-free reads aren't possible because the gateway is read-only, so instead have the gateway also write a small marker file (for example `/log/state.json` with the first refusal time and a void flag) and poll only that. The other option is to back off polling as the log grows, e.g. poll the full log only when `docker container inspect`/a size check shows it grew, and widen the interval with size. Also log, rather than silently swallow, repeated poll failures.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/ce376d5c-7b36-45fb-9212-867bc4bfbdc1?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/ce376d5c-7b36-45fb-9212-867bc4bfbdc1)