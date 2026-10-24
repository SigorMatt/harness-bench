<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

1\. Unstable probe results pass curation <code>🐞 Bug</code> <code>≡ Correctness</code>

<pre>
<b><i>observe()</i></b> compares repeated probe results with ordinary dictionary equality, which treats JSON
values such as <b><i>1</i></b> and <b><i>1.0</i></b> as equal. When a probe alternates between those types, curation reports
no repeatability problem even though grading uses <b><i>strict_equal()</i></b> to distinguish them.
</pre>


<details>
<summary><strong>Agent Prompt</strong></summary>

```
## Issue description
The repeatability check accepts probe outputs whose JSON value types change between runs because Python dictionary equality treats some different types as equal.

## Fix Focus Areas
- hb/curate.py[100-107]

## Recommended Fix
Use the existing `strict_equal()` comparison for the two parsed observation dictionaries, and add a test with repeated outputs that differ only by JSON numeric or boolean type.
```

<code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
</details>

[Dismiss ↗](https://app.qodo.ai/findings/47c37e8a-1327-4561-845b-7ecaea98c78a?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/47c37e8a-1327-4561-845b-7ecaea98c78a)