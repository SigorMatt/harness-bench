<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

2\. Windows pinning can fail verification <code>🐞 Bug</code> <code>☼ Reliability</code>

<pre>
<b><i>cmd_pin</i></b> writes the complete workflow using <b><i>Path.write_text</i></b>, which translates line endings on
Windows rather than preserving the committed bytes of lines it did not change. In a clone without
Git line-ending normalization, the resulting commit changes every line to CRLF and
<b><i>check_release_commit</i></b> rejects it because its content is not the parent workflow with only two pins
substituted.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
The maintainer’s `pin` command can translate the whole workflow to CRLF on Windows, causing its release commit to fail the two-line-only verification.
## Fix Focus Areas
- scripts/release.py[83-87]
- hb/release.py[303-306]
## Recommended Fix
Write the pinned workflow as UTF-8 bytes, or open it with newline translation disabled, so every unchanged byte remains identical to the committed workflow. Add a test covering the byte-preservation requirement.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/f50b01cd-1fdb-4ea9-b0a4-afde276e0da6?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/f50b01cd-1fdb-4ea9-b0a4-afde276e0da6)