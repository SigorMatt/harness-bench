<img src="https://img.shields.io/badge/High-634FD1?style=flat-square" height="20px" alt="Action required">

3\. Shutdown can still send a provider request <code>🐞 Bug</code> <code>☼ Reliability</code>

<pre>
<b><i>_open_upstream()</i></b> does not recheck <b><i>abandoned</i></b> after <b><i>conn.connect()</i></b> and before <b><i>conn.request()</i></b>.
If the drain expires while connection establishment is in progress, <b><i>abandon()</i></b> cannot shut down a
socket that has not yet been assigned, and the worker can send the POST after shutdown has recorded
it as abandoned.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
An upstream connection completed after the drain deadline can still send a request that shutdown has already recorded as abandoned.
## Fix Focus Areas
- hb/gateway.py[536-548]
- hb/gateway.py[725-735]
## Recommended Fix
Coordinate abandonment with the transition from connecting to sending so no POST can begin after an exchange is abandoned. Add a test that holds connection establishment across the drain deadline.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/93824836-fb93-462d-a710-fc48e7a70df0?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/93824836-fb93-462d-a710-fc48e7a70df0)