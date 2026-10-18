<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

5\. Malformed chunked uploads reach the model <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>_read_chunked()</i></b> discards the line following each chunk without checking that it is the required
delimiter. A client can supply an invalid separator and a valid JSON payload, which the gateway then
admits and forwards instead of rejecting as bad framing.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
The chunked-body reader accepts malformed separators and can forward a request whose body framing is invalid.
## Fix Focus Areas
- hb/gateway.py[665-683]
## Recommended Fix
Require the exact delimiter after each nonzero chunk and reject premature EOF, including in the final trailer terminator. Add malformed-separator and missing-terminator tests asserting that no provider request is made.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/f1749e6a-5643-4936-9df5-1355ab9d41e1?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/f1749e6a-5643-4936-9df5-1355ab9d41e1)