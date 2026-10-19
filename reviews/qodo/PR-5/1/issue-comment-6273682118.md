<h3>Code Review by Qodo</h3>

<code>🐞 Bugs (2)</code>  <code>📘 Rule violations (0)</code>  <code>📜 Skill insights (0)</code>

<img src="https://www.qodo.ai/wp-content/uploads/2025/11/light-grey-line.svg" height="10%" alt="Grey Divider">

<br/>

<img src="https://img.shields.io/badge/Medium-634FD1?style=flat-square" height="20px" alt="Remediation recommended">

<details>
<summary>  1.  The budget grace period can start late <code>🐞 Bug</code> <code>≡ Correctness</code></summary>

<br/>

> <details open>
><summary>Description</summary>
><br/>
>
><pre>
><b><i>note_refusal()</i></b> timestamps a refusal after <b><i>ledger.admit()</i></b> has released its lock, and
><b><i>StateFile._change()</i></b> permanently keeps whichever thread writes first. If the first refused request
>is paused before that write, a later refused request can supply the timestamp, delaying the
>harness’s grace-period deadline.
></pre>
></details>

> <details>
><summary>Code</summary>
><br/>
>
><code>[hb/gateway.py[713]](https://github.com/SigorMatt/harness-bench/pull/5/files#diff-fef3562d0489296e808673921d86c962355faa436a870c5f670f6d49e9471f96R713-R713)</code>
>
>```diff
>+            self.gw.note_refusal()
>```
></details>

> <details>
><summary>Evidence</summary>
><br/>
>
><pre>
>Admission decides <b><i>exhausted</i></b> under the ledger lock, but the timestamp is taken only after that lock
>is released. The state-file lock preserves the first caller to reach <b><i>_change</i></b>, while the
>orchestrator computes its deadline from that preserved timestamp.
></pre>
>
> <code>[hb/gateway.py\[209-224\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/gateway.py/#L209-L224)</code>
> <code>[hb/gateway.py\[275-292\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/gateway.py/#L275-L292)</code>
> <code>[hb/gateway.py\[553-556\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/gateway.py/#L553-L556)</code>
> <code>[hb/gateway.py\[704-714\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/gateway.py/#L704-L714)</code>
> <code>[hb/orchestrator.py\[281-284\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/orchestrator.py/#L281-L284)</code>
></details>

> <details>
><summary>Agent prompt</summary>
><br/>
>
>```
>The issue below was found during a code review. Follow the provided context and guidance below and implement a solution
>
>## Issue description
>Concurrent exhausted requests can write the state file out of refusal order, so a later request starts the grace period.
>## Fix Focus Areas
>- hb/gateway.py[209-224]
>- hb/gateway.py[553-556]
>- hb/gateway.py[704-714]
>## Recommended Fix
>Capture and preserve the first refusal's timestamp as part of the serialized admission decision, then publish that value to the state file without allowing a later refusal to replace it.
>```
> <code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
></details>

[Dismiss ↗](https://app.qodo.ai/findings/c082c5b9-95fb-40f1-a4d5-1b7caa691f3d?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/c082c5b9-95fb-40f1-a4d5-1b7caa691f3d)

<hr/>
</details>


<details>
<summary>  2.  The budget grace period can start early <code>🐞 Bug</code> <code>≡ Correctness</code></summary>

<br/>

> <details open>
><summary>Description</summary>
><br/>
>
><pre>
><b><i>Exchange._handle()</i></b> records <b><i>first_refusal_ms</i></b> before its synchronous state-file save and before
>constructing the budget-refusal response. If that save or the request thread is delayed, the toolkit
>counts the delay against the 30-second grace period and can kill the harness before it receives the
>refusal.
></pre>
></details>

> <details>
><summary>Code</summary>
><br/>
>
><code>[hb/gateway.py[713]](https://github.com/SigorMatt/harness-bench/pull/5/files#diff-fef3562d0489296e808673921d86c962355faa436a870c5f670f6d49e9471f96R713-R713)</code>
>
>```diff
>+            self.gw.note_refusal()
>```
></details>

> <details>
><summary>Evidence</summary>
><br/>
>
><pre>
>The new timestamp is captured before <b><i>_save()</i></b> writes and replaces the file; only afterward does
><b><i>_local()</i></b> reach <b><i>_answer()</i></b> and <b><i>_send()</i></b>. The orchestrator subtracts elapsed time since that
>timestamp from the grace period.
></pre>
>
> <code>[hb/gateway.py\[269-286\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/gateway.py/#L269-L286)</code>
> <code>[hb/gateway.py\[553-556\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/gateway.py/#L553-L556)</code>
> <code>[hb/gateway.py\[710-716\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/gateway.py/#L710-L716)</code>
> <code>[hb/gateway.py\[1019-1025\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/gateway.py/#L1019-L1025)</code>
> <code>[hb/orchestrator.py\[276-284\]](https://github.com/SigorMatt/harness-bench/blob/67dae12dfbf9f29b8a3367707c9e851ab4972d8a/hb/orchestrator.py/#L276-L284)</code>
></details>

> <details>
><summary>Agent prompt</summary>
><br/>
>
>```
>The issue below was found during a code review. Follow the provided context and guidance below and implement a solution
>
>## Issue description
>The state timestamp is captured before the state write and refusal response are constructed, allowing gateway-side delay to consume the harness's grace period.
>## Fix Focus Areas
>- hb/gateway.py[553-556]
>- hb/gateway.py[712-716]
>- hb/gateway.py[1019-1025]
>## Recommended Fix
>Record the refusal time at the point used for the budget-refusal record, without placing a potentially slow state-file write between the timestamp and that decision. Preserve the first refusal when publishing concurrent updates.
>```
> <code>ⓘ Copy this prompt and use it to remediate the issue with your preferred AI generation tools</code>
></details>

[Dismiss ↗](https://app.qodo.ai/findings/31a2c538-69ce-4106-934c-c426f03d8196?action=dismiss&origin=git) | [View ↗](https://app.qodo.ai/findings/31a2c538-69ce-4106-934c-c426f03d8196)

<hr/>
</details>



<img src="https://www.qodo.ai/wp-content/uploads/2025/11/light-grey-line.svg" height="10%" alt="Grey Divider">


<!-- qodo-context:start -->
<details><summary><strong>Context sources</strong></summary>

<div>&#x274C; Tickets: failed to load — Ticket collection could not establish complete evidence</div>
<div>Review mode: <code>Auto: 🧠 Deep</code>: Substantial concurrent gateway and orchestrator logic spans multiple independent failure paths.</div>
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


<!-- https://github.com/SigorMatt/harness-bench/commit/67dae12dfbf9f29b8a3367707c9e851ab4972d8a -->

<a href="https://www.qodo.ai"><img src="https://www.qodo.ai/wp-content/uploads/2025/03/qodo-logo.svg" width="80" alt="Qodo Logo"></a>