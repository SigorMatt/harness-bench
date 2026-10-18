<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

7\. Long model streams have no size bound <code>🐞 Bug</code> <code>➹ Performance</code>

<pre>
<b><i>_relay_stream()</i></b> iterates over SSE events without enforcing a cumulative response limit, although
<b><i>_read_capped()</i></b> limits non-streaming responses. A provider can send arbitrarily many individually
valid events, and tool-call argument fragments remain accumulated in memory throughout that stream.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
SSE responses bypass the cumulative response-size bound and can also grow retained tool-call arguments without limit.
## Fix Focus Areas
- hb/gateway.py[349-375]
- hb/gateway.py[329-345]
- hb/gateway.py[779-803]
## Recommended Fix
Track cumulative SSE bytes and retained tool-call argument bytes, enforce explicit limits, and use the existing stream-failure path when those limits are exceeded. Test many individually valid events whose combined size exceeds the limit.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/fb4cb135-bf9b-44b2-bfa3-dd93d2e8e670?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/fb4cb135-bf9b-44b2-bfa3-dd93d2e8e670)