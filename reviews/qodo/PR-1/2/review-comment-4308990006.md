<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

6\. Trailer uploads can occupy workers indefinitely <code>🐞 Bug</code> <code>➹ Performance</code>

<pre>
<b><i>_read_chunked()</i></b> reads an unlimited number of trailer lines after the zero-size chunk, without
counting them against the body limit. A client that keeps supplying nonempty lines before each
socket inactivity timeout can retain a gateway worker for an unbounded period without completing its
POST.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Chunked trailers have no aggregate size or line-count bound, allowing an incomplete upload to occupy a worker indefinitely.
## Fix Focus Areas
- hb/gateway.py[665-678]
- hb/gateway.py[958-960]
## Recommended Fix
Limit the total bytes or number of trailer lines and reject requests exceeding that bound. Test repeated trailer lines that stay below the per-read timeout.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/8df48798-4f1c-4518-9e78-502cec38786b?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/8df48798-4f1c-4518-9e78-502cec38786b)