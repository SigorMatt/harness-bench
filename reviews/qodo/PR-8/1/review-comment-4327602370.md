<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

3\. Some release entries get the wrong cohort <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>Git.export</i></b> writes each committed path into a scratch directory without detecting paths that
collide on the host filesystem. When a maintainer runs <b><i>entry</i></b> on a case-insensitive filesystem and
the suite contains distinct names such as <b><i>Foo.py</i></b> and <b><i>foo.py</i></b>, one file overwrites the other and
the resulting cohort ID differs from the Linux-built suite’s ID.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Exporting committed suite files into a case-insensitive scratch directory can overwrite one of two distinct Git paths, producing the wrong release-entry cohort ID.
## Fix Focus Areas
- hb/release.py[254-272]
- hb/release.py[333-341]
## Recommended Fix
Detect destination-path collisions before writing exported files and fail with a clear release error. Test an export containing Git paths that differ only by case; alternatively, compute the suite tree digest directly from Git paths and blob bytes without a host-filesystem export.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/1e35ebd2-d052-468c-a648-f0bd4d37a0ab?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/1e35ebd2-d052-468c-a648-f0bd4d37a0ab)