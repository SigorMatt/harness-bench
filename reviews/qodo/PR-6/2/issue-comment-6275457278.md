<h3>Code Review by Qodo</h3>

<code>🐞 Bugs (1)</code>  <code>📘 Rule violations (0)</code>  <code>📜 Skill insights (0)</code>

<img src="https://www.qodo.ai/wp-content/uploads/2025/11/light-grey-line.svg" height="10%" alt="Grey Divider">

<br/>

<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

<details>
<summary>  1.  Task jobs can time out before finishing <code>🐞 Bug</code> <code>≡ Correctness</code></summary>

<br/>

> <details open>
><summary>Description</summary>
><br/>
>
><pre>
><b><i>plan_run()</i></b> uses <b><i>task_job_timeout_minutes()</i></b>, which budgets only 60 seconds per attempt for work
>including the workspace snapshot, although <b><i>_snapshot_patch()</i></b> alone can wait up to 600 seconds.
>When snapshots take longer than that allowance, the workflow can terminate a valid task job before
>it finishes, even though planning approved the suite.
></pre>
></details>

> <details>
><summary>Code</summary>
><br/>
>
><code>[hb/plan.py[79]](https://github.com/SigorMatt/harness-bench/pull/6/files#diff-19782370be3ee656dae79282a5c795254c797423e8b97d1b69d832fd907b49d5R79-R79)</code>
>
>```diff
>+    return {"tasks": ids, "timeout_minutes": task_job_timeout_minutes(suite, max(probe_timeouts))}
>```
></details>

> <details>
><summary>Relevance</summary>
><br/>
>
> `●●● Strong`
>
><pre>
>Concrete timeout-budget mismatch can terminate valid jobs; similar deadline handling was accepted
>recently.
></pre>
>
> [PR-#3](https://github.com/SigorMatt/harness-bench/pull/3)
> [PR-#1](https://github.com/SigorMatt/harness-bench/pull/1)
>
> <code>ⓘ Recommendations generated based on similar findings in past PRs</code>
></details>

> <details>
><summary>Evidence</summary>
><br/>
>
><pre>
>The new call makes the existing estimate the timeout supplied to the workflow. That estimate allows
>60 seconds of per-attempt overhead, while the execution path permits a 600-second snapshot on each
>successful attempt.
></pre>
>
> <code>[hb/plan.py\[17-32\]](https://github.com/SigorMatt/harness-bench/blob/92f8168e2fdaedd423d942d74032f23faacd0a1a/hb/plan.py/#L17-L32)</code>
> <code>[hb/plan.py\[74-79\]](https://github.com/SigorMatt/harness-bench/blob/92f8168e2fdaedd423d942d74032f23faacd0a1a/hb/plan.py/#L74-L79)</code>
> <code>[hb/orchestrator.py\[85-92\]](https://github.com/SigorMatt/harness-bench/blob/92f8168e2fdaedd423d942d74032f23faacd0a1a/hb/orchestrator.py/#L85-L92)</code>
> <code>[hb/orchestrator.py\[297-301\]](https://github.com/SigorMatt/harness-bench/blob/92f8168e2fdaedd423d942d74032f23faacd0a1a/hb/orchestrator.py/#L297-L301)</code>
> <code>[hb/orchestrator.py\[339-343\]](https://github.com/SigorMatt/harness-bench/blob/92f8168e2fdaedd423d942d74032f23faacd0a1a/hb/orchestrator.py/#L339-L343)</code>
> <code>[.github/workflows/bench.yml\[80-89\]](https://github.com/SigorMatt/harness-bench/blob/92f8168e2fdaedd423d942d74032f23faacd0a1a/.github/workflows/bench.yml/#L80-L89)</code>
></details>

> <details>
><summary>Agent prompt</summary>
><br/>
>
>```
>The issue below was found during a code review. Follow the provided context and guidance below and implement a solution
>
>## Issue description
>The newly wired planner can approve a suite with a task-job timeout shorter than the time its execution path permits. Its 60-second per-attempt overhead does not cover a workspace snapshot that can take 600 seconds.
>## Fix Focus Areas
>- hb/plan.py[17-32]
>- hb/plan.py[74-79]
>- hb/orchestrator.py[85-92]
>## Recommended Fix
>Include the permitted snapshot duration and other bounded per-attempt operations in the timeout calculation. Recheck the hosted-job limit using that bound, and add a test for a suite near the limit.
>```
> <code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
></details>

[Dismiss ↗](https://app.qodo.ai/findings/9999c6d6-1ae4-483c-9bd2-a5f8b45a0326?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/9999c6d6-1ae4-483c-9bd2-a5f8b45a0326)

<hr/>
</details>



<img src="https://www.qodo.ai/wp-content/uploads/2025/11/light-grey-line.svg" height="10%" alt="Grey Divider">


<!-- qodo-context:start -->
<details><summary><strong>Context sources</strong></summary>

<div>Review mode: <code>Auto: 🧠 Deep</code>: Substantial security-sensitive planning, sealing, workflow, and archive-validation logic spans many independent paths.</div>
<!-- qodo-context:end -->
</details>

<img src="https://www.qodo.ai/wp-content/uploads/2025/11/light-grey-line.svg" height="10%" alt="Grey Divider">



<!-- qodo-daily-tip:start -->

<details>
<summary><strong>Tip of the day</strong></summary>

<br/>

<pre>💡 Did you know, you can import rules and skills only from the repositories your team follows</pre>

<a href="https://docs.qodo.ai/tips-and-tricks">More tips ↗</a> | <a href="https://app.qodo.ai/configurations?tab=display-preferences">Customize Qodo ↗</a> | <a href="https://docs.qodo.ai">Qodo docs ↗</a>

</details>

<img src="https://www.qodo.ai/wp-content/uploads/2025/11/light-grey-line.svg" height="10%" alt="Grey Divider">
<!-- qodo-daily-tip:end -->


<!-- https://github.com/SigorMatt/harness-bench/commit/92f8168e2fdaedd423d942d74032f23faacd0a1a -->

<a href="https://www.qodo.ai"><img src="https://www.qodo.ai/wp-content/uploads/2025/03/qodo-logo.svg" width="80" alt="Qodo Logo"></a>