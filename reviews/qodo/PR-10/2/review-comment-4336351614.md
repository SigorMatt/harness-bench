<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

5\. Long tasks can exhaust model context <code>🐞 Bug</code> <code>☼ Reliability</code>

<pre>
<b><i>main</i></b> appends every assistant reply and tool result to <b><i>messages</i></b> and resends the entire list
without a history limit. With up to 60 steps and tool results of up to 12,000 characters each, a
task can exceed the model&#x27;s context window; the resulting gateway rejection ends the agent with exit
status 2 rather than letting it continue its work.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Repeated tool results make the baseline agent's request history grow until a model rejects a long-running task.

## Fix Focus Areas
- harnesses/baseline/agent.py[241-275]
- harnesses/baseline/config/agent.json[4-10]

## Recommended Fix
Apply a bounded-history or summarization strategy before each chat request, retaining the task instruction and valid assistant/tool-call groupings. Test a long sequence of large tool results.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/3006acaa-2b05-46ef-bd13-ca9a73db6184?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/3006acaa-2b05-46ef-bd13-ca9a73db6184)