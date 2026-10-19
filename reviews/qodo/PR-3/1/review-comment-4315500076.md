<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

1\. Harnesses can run past their kill deadline <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>_watch_harness</i></b> gives a gateway-log copy at least one second even when less than one second remains
before the wall or budget-grace deadline. If that copy blocks near either deadline, the loop cannot
kill the harness until the read returns or times out.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
A gateway-log read can outlast the remaining wall-clock or budget-grace time because its timeout is clamped to at least one second.

## Fix Focus Areas
- hb/orchestrator.py[234-257]

## Recommended Fix
Use the actual positive time remaining as the log-copy timeout, and recheck the deadline before starting a read when no time remains. Add a test with a slow log read immediately before a fractional-second deadline.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/926444c3-10f4-46ad-86f3-f9a3b520ce5a?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/926444c3-10f4-46ad-86f3-f9a3b520ce5a)