<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

2\. Broken harness commands pass image checks <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>check_image_files</i></b> strips <b><i>/hb/</i></b> from an entrypoint argument but does not reject <b><i>..</i></b> or a second
leading slash before joining the remainder to the copied image directory. An argument such as
<b><i>/hb/../inspect.json</i></b> can pass by finding the workflow&#x27;s host-side inspection file, even when the
image lacks the file the container command would use; later registry verification does not recheck
entrypoint files.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Entrypoint paths containing traversal or doubled slashes can cause the image-file check to inspect host files instead of files copied from the image.

## Fix Focus Areas
- hb/harness.py[148-160]
- hb/harness.py[185-187]

## Recommended Fix
Reject noncanonical `/hb/` entrypoint paths before checking them: disallow empty, `.` and `..` components and require the resulting relative path to remain under the copied `/hb` directory. Test both traversal and doubled-slash arguments.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/7ad771ad-76a6-4a12-acde-7cc062bd87c6?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/7ad771ad-76a6-4a12-acde-7cc062bd87c6)