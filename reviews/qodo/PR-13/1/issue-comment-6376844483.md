<h3>PR Summary by Qodo</h3>

Register baseline harness version 0.1.0

<code>⚙️ Configuration changes</code> <code>🕐 10-20 Minutes</code>

<img src="https://www.qodo.ai/wp-content/uploads/2025/11/light-grey-line.svg" height="10%" alt="Grey Divider">

<details>
<summary>AI Description</summary>

<dl>
<dd>
<br/>

><pre>
>• Add the baseline 0.1.0 harness to the registry for release verification and use.
>• Pin its image, build commit, configuration digest, and task base for reproducibility.
></pre>

</dd>
</dl>

</details>

<details>
<summary>Diagram</summary>

<dl>
<dd>

<br/>

```mermaid
graph TD
  A["Release workflow"] --> B["Harness verification"] --> C["Harness registry"] --> D["Baseline image"]
  C --> E["Task base image"]
```

</dd>
</dl>

</details>




<details>
<summary>High-Level Assessment</summary>

<dl>
<dd>

<br/>

>Keep the generated registry entry. It uses the existing pin-and-verify workflow and immutable image digests; a mutable image tag would weaken reproducibility.

</dd>
</dl>

</details>

<details>
<summary> Files changed (1) <code> +18 / -0 </code> </summary>

<dl>
<dd>

<br/>

<details>
<summary>Other (1) <code> +18 / -0 </code></summary>

<dl>
<dd>

<details>
<summary>registry.json<code>Register the baseline 0.1.0 harness</code> <code>+18/-0</code></summary>

<br/>

>Register the baseline 0.1.0 harness
>
><pre>
>• Creates the harness registry with a baseline entry. The entry records the image digest, configuration digest, entrypoint, homepage, build commit, and pinned task base.
></pre>
>
><a href='https://github.com/SigorMatt/harness-bench/pull/13/files#diff-c1ccab6b761f7c3ef53302c329215f605bb24c73aa16fb75bc4da712926e972b'>registry.json</a>

</details>

</dd>
</dl>

</details>

</dd>
</dl>

</details>