<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

1\. Shutdown can fail a job when no void happened <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>Exchange._fail</i></b> calls <b><i>gw.mark_unreachable</i></b> before <b><i>_decide</i></b> checks <b><i>self.abandoned</i></b>, so an
exchange that shutdown records as <b><i>usage_unknown</i></b> rather than a void can still set exit status 3. If
a first connect fails but its retry is still streaming when shutdown abandons the request, closing
the upstream socket can send the relay through <b><i>_fail</i></b>; <b><i>_decide</i></b> then returns <b><i>None</i></b>, yet the
nonzero gateway status fails the job.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
`Exchange._fail` marks the gateway unreachable before the locked void decision, so shutdown can record an abandoned request as non-void while the gateway still exits with status 3.

## Fix Focus Areas
- hb/gateway.py[923-939]
- hb/gateway.py[875-893]

## Recommended Fix
Move `mark_unreachable` into `_decide`’s locked `if void:` branch, after the `self.abandoned` check and before `_record`, or pass a flag to `_decide` to achieve the same ordering. Keep the status set before a void record becomes visible, but never set it for an abandoned request. Add a test in which the first connect fails, the retry streams, and shutdown abandons the request; the exit status must be 0.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/c8ec4f74-ab9f-4f1a-a5cf-84ee297f0484?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/c8ec4f74-ab9f-4f1a-a5cf-84ee297f0484)