<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

2\. Ignored artifacts can fail curation <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>_curate()</i></b> runs <b><i>tree_changes()</i></b> on the unfiltered solution tree and checks every resulting path
against the task&#x27;s allowed paths. If <b><i>checks/solution</i></b> contains an ignored artifact outside those
paths, curation rejects it even though <b><i>workspace_diff()</i></b> removes that artifact from the patch used
to grade the reference solution.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Curation counts ignored files in its solution-change and scope checks, but the graded workspace patch excludes them.

## Fix Focus Areas
- hb/curate.py[118-130]

## Recommended Fix
Calculate solution changes from trees materialized with the suite's ignore rules, matching `workspace_diff()` and grading. Add a test with an ignored cache artifact outside the task's allowed paths.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/aff2d514-e0a2-4a5b-b0ca-9782eef3bcb5?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/aff2d514-e0a2-4a5b-b0ca-9782eef3bcb5)