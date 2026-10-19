<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

2\. Scratch copies can fill the runner disk <code>🐞 Bug</code> <code>☼ Reliability</code>

<pre>
<b><i>run_task</i></b> retains each attempt&#x27;s workspace tar, extracted snapshot and diff copies under <b><i>work_dir</i></b>
after the rep is finished. With multiple reps or reruns containing large workspaces, these copies
accumulate even though the workspace size limit applies separately to each snapshot.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Attempt scratch directories retain large workspace copies throughout the task, allowing later reps to exhaust runner storage.

## Fix Focus Areas
- hb/orchestrator.py[266-272]
- hb/orchestrator.py[333-347]
- hb/orchestrator.py[428-451]

## Recommended Fix
Remove each attempt's scratch directory after its patch and logs have been captured, including void attempts. Keep only data required for grading and bundle assembly, and clean up the task scratch directory on completion or failure.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/3663de70-5358-43a0-acca-883ec3491eda?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/3663de70-5358-43a0-acca-883ec3491eda)