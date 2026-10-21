<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

4\. Background commands linger after exit <code>🐞 Bug</code> <code>☼ Reliability</code>

<pre>
<b><i>run_bash</i></b> kills the command&#x27;s process group only when the foreground bash process times out,
leaving children started by a normally exiting shell alive. If a background child retains stdout,
the reader remains blocked and each affected tool call waits five extra seconds; those children also
keep consuming the harness container&#x27;s process allowance.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Background children survive a successful shell exit and can hold the output pipe open throughout later agent steps.

## Fix Focus Areas
- harnesses/baseline/agent.py[100-139]

## Recommended Fix
After the foreground shell exits, terminate any remaining processes in its group and close or finish draining the pipe before returning the tool result. Test a command that backgrounds a long-lived process inheriting stdout.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/9dd7b7fa-ac7d-4982-9f98-bc44856a61b8?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/9dd7b7fa-ac7d-4982-9f98-bc44856a61b8)