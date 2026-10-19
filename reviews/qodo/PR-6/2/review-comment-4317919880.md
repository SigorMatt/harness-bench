<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

1\. Task jobs can time out before finishing <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>plan_run()</i></b> uses <b><i>task_job_timeout_minutes()</i></b>, which budgets only 60 seconds per attempt for work
including the workspace snapshot, although <b><i>_snapshot_patch()</i></b> alone can wait up to 600 seconds.
When snapshots take longer than that allowance, the workflow can terminate a valid task job before
it finishes, even though planning approved the suite.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
The newly wired planner can approve a suite with a task-job timeout shorter than the time its execution path permits. Its 60-second per-attempt overhead does not cover a workspace snapshot that can take 600 seconds.
## Fix Focus Areas
- hb/plan.py[17-32]
- hb/plan.py[74-79]
- hb/orchestrator.py[85-92]
## Recommended Fix
Include the permitted snapshot duration and other bounded per-attempt operations in the timeout calculation. Recheck the hosted-job limit using that bound, and add a test for a suite near the limit.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/9999c6d6-1ae4-483c-9bd2-a5f8b45a0326?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/9999c6d6-1ae4-483c-9bd2-a5f8b45a0326)