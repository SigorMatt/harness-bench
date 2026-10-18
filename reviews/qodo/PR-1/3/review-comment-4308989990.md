<img src="https://img.shields.io/badge/High-634FD1?style=flat-square" height="20px" alt="Action required">

1\. Stalled response bodies void the attempt <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>_relay_json</i></b> and <b><i>_relay_stream</i></b> route read timeouts after response headers through <b><i>_fail()</i></b>
instead of recording <b><i>usage_unknown</i></b>. If a provider stalls during a JSON body or between stream
events, the gateway voids and re-runs the attempt even though the provider may still be generating
and billing it.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Read timeouts after upstream response headers currently void an attempt, although the gateway's timeout policy treats requests it stops waiting for as `usage_unknown`.
## Fix Focus Areas
- hb/gateway.py[381-388]
- hb/gateway.py[757-761]
- hb/gateway.py[785-801]
## Recommended Fix
Preserve timeout identity through JSON and SSE body reads. Record these timeouts as `usage_unknown` without retrying or voiding; handle an already-started stream without attempting to send a second HTTP status. Add post-header timeout tests for both response types.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/70e78541-c22b-4930-9b47-1467c2ee14bc?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/70e78541-c22b-4930-9b47-1467c2ee14bc)