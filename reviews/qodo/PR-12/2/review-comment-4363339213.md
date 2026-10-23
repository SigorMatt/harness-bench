<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

2\. Registry validation accepts unusable entries <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
The registry schema defines <b><i>task_base</i></b> but does not add it to each entry’s <b><i>required</i></b> fields. An
entry without it passes schema validation, then fails the new release-input comparison and harness
verification, making the schema an unreliable check of the registry’s required shape.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
Registry entries may omit `task_base` under the schema even though release and harness verification require it.
## Fix Focus Areas
- schemas/registry.schema.json[25-54]
## Recommended Fix
Add `task_base` to the registry entry object's `required` array.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/323cc6ed-d6b6-4ad2-acaf-7dff411254cf?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/323cc6ed-d6b6-4ad2-acaf-7dff411254cf)