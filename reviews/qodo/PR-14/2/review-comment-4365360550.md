<img src="https://img.shields.io/badge/Low-634FD1?style=flat-square" height="20px" alt="Informational">

3\. A bad solution file crashes the whole suite check <code>🐞 Bug</code> <code>☼ Reliability</code>

<pre>
<b><i>_curate</i></b> lays the reference solution over the pristine tree with a plain `shutil.copytree(solution,
solved, dirs_exist_ok=True)<b><i>, which follows symlinks and copies </i></b>__pycache__` and other ignored
files. Unlike <b><i>materialize</i></b>, it does not drop non-regular entries. A dangling or looping symlink in
<b><i>checks/solution</i></b> raises <b><i>shutil.Error</i></b>/<b><i>OSError</i></b>. <b><i>curate_task</i></b> only catches <b><i>_Unfit</i></b> and
<b><i>scripts/suite.py</i></b> only catches <b><i>DockerError</i></b>, so one bad task aborts the check with a traceback and
no other task gets reported.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
The reference solution is copied with `shutil.copytree`, which follows symlinks and copies ignored files. A broken symlink raises an exception that `curate_task` does not catch, so the whole `scripts/suite.py check` aborts.

## Fix Focus Areas
- hb/curate.py[118-121]
- hb/curate.py[50-62]

## Recommended Fix
Materialize the solution first with `materialize(solution, workdir / "solution", ignore)`. If it returns any dropped entries, append them as a problem, for example "checks/solution holds non-regular files: ...". Then overlay that materialized copy with `copytree(..., dirs_exist_ok=True)`. Also wrap the overlay in `try/except (OSError, shutil.Error)` and raise `_Unfit` with the message, so the failure is reported against this task instead of crashing the run.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/d6f7384e-a36b-469a-88a3-07e97b93a330?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/d6f7384e-a36b-469a-88a3-07e97b93a330)