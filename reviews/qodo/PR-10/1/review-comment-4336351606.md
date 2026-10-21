<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

3\. Some accepted harness names cannot build <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>check_harness_id</i></b> accepts a slug ending in <b><i>-</i></b>, and <b><i>harness_repository</i></b> appends it directly to the
Docker repository name. For an adapter such as <b><i>harnesses/foo-/</i></b>, validation and naming succeed, but
the resulting image reference ends in a hyphen before its tag and <b><i>docker build --tag</i></b> rejects it.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
A valid harness slug can produce an invalid Docker repository name, failing its build after the adapter check passes.

## Fix Focus Areas
- hb/harness.py[53-65]

## Recommended Fix
Validate harness IDs for use as the final component of the image repository, including rejecting a trailing hyphen, and add a test for such an ID.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/1be244d6-28c7-40ab-bbb5-c00bd8026a09?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/1be244d6-28c7-40ab-bbb5-c00bd8026a09)