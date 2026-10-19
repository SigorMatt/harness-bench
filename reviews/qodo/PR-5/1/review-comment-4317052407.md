<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

1\. The budget grace period can start late <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>note_refusal()</i></b> timestamps a refusal after <b><i>ledger.admit()</i></b> has released its lock, and
<b><i>StateFile._change()</i></b> permanently keeps whichever thread writes first. If the first refused request
is paused before that write, a later refused request can supply the timestamp, delaying the
harness’s grace-period deadline.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Concurrent exhausted requests can write the state file out of refusal order, so a later request starts the grace period.
## Fix Focus Areas
- hb/gateway.py[209-224]
- hb/gateway.py[553-556]
- hb/gateway.py[704-714]
## Recommended Fix
Capture and preserve the first refusal's timestamp as part of the serialized admission decision, then publish that value to the state file without allowing a later refusal to replace it.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/c082c5b9-95fb-40f1-a4d5-1b7caa691f3d?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/c082c5b9-95fb-40f1-a4d5-1b7caa691f3d)