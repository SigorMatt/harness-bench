<img src="https://img.shields.io/badge/High-634FD1?style=flat-square" height="20px" alt="Action required">

4\. A partial upload can be sent twice <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>_open_upstream()</i></b> sets <b><i>sent</i></b> only after <b><i>conn.request()</i></b> returns and retries exceptions raised
during that call. If sending the POST fails after some bytes have left the socket, the gateway
cannot establish that the provider did no work before it sends another attempt.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
A failure while `conn.request()` transmits a POST is treated as safe to retry, even though transmission may have begun.
## Fix Focus Areas
- hb/gateway.py[729-743]
## Recommended Fix
Distinguish failures before any request bytes can be sent from failures during or after transmission. Do not retry the latter as connection-only failures; record their uncertain accounting according to the gateway's timeout policy. Test a failed send after partial transmission.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/9cd403e7-6b59-4a1e-aad0-de3f368f9323?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/9cd403e7-6b59-4a1e-aad0-de3f368f9323)