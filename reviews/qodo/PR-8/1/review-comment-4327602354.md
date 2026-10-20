<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

1\. A release can contain an empty task workspace <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>check_inputs</i></b> validates each task document and runs <b><i>plan_run</i></b>, but neither checks that the task’s
referenced <b><i>repo</i></b> directory exists. If that directory is missing, <b><i>materialize</i></b> creates an empty
workspace without error, so the release can publish a task whose runs use an empty repository.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Release input validation accepts a task whose `repo` directory is absent; runtime silently materializes an empty workspace.
## Fix Focus Areas
- hb/release.py[128-131]
## Recommended Fix
After validating each task document, resolve its `repo` path beneath the task directory and reject it unless it is an existing directory. Add a release-input test for a missing task workspace.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/bb16f088-fc93-4fba-863e-daaa55d0f722?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/bb16f088-fc93-4fba-863e-daaa55d0f722)