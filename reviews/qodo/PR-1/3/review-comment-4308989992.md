<img src="https://img.shields.io/badge/High-634FD1?style=flat-square" height="20px" alt="Action required">

2\. Shutdown can record fewer tokens than counted <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>_count()</i></b> checks <b><i>abandoned</i></b> separately from its ledger update and assignment to <b><i>counted_usage</i></b>,
while <b><i>abandon()</i></b> snapshots usage under a different lock. If shutdown intervenes between that check
and the update, it writes a zero-usage record before the request thread adds tokens to the rep-wide
ledger.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Shutdown can snapshot zero usage for a request whose handler subsequently adds tokens to the ledger.
## Fix Focus Areas
- hb/gateway.py[536-558]
- hb/gateway.py[816-821]
## Recommended Fix
Use the exchange lifecycle lock to make the abandonment check, ledger update, and `counted_usage` assignment atomic with respect to `abandon()`. Test shutdown at the usage-counting boundary and verify that persisted usage agrees with ledger totals.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/c4397487-5324-4f9e-8e70-80b83571a32f?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/c4397487-5324-4f9e-8e70-80b83571a32f)