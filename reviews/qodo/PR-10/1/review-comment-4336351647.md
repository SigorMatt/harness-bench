<img src="https://img.shields.io/badge/High-634FD1?style=flat-square" height="20px" alt="Action required">

1\. Release check cannot write the plan output <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
The release workflow applies <b><i>--cap-drop ALL</i></b> to the toolkit’s root-run <b><i>plan</i></b> command, removing
<b><i>CAP_DAC_OVERRIDE</i></b> while <b><i>/out</i></b> points to a runner-owned directory. When <b><i>$RUNNER_TEMP/plan</i></b> has
mode 0755, <b><i>plan</i></b> cannot write its output files and the images job stops before publishing, unlike
the corresponding plan step in bench.yml.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
The release images job drops all capabilities from the toolkit’s root-run `plan` container. It then cannot write its output files into the runner-owned `$RUNNER_TEMP/plan` bind mount when that directory has mode 0755, stopping the job before publishing.

## Fix Focus Areas
- .github/workflows/release.yml[209-218]

## Recommended Fix
Keep the sandbox restrictions, but give the container write access to the output directory without restoring unnecessary capabilities. Add `--user "$(id -u):$(id -g)"` to the plan `docker run` or the shared `sandbox` array; alternatively, make `$RUNNER_TEMP/plan` writable before the run, for example with `chmod 0777`. Verify that plan creates both output files and that the release plan step still behaves like its counterpart in bench.yml.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/bec0a4de-24f5-4811-8f03-7f179336a85e?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/bec0a4de-24f5-4811-8f03-7f179336a85e)