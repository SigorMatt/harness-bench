# Qodo review of PR #12 — dispositions

| # | severity | finding (title as the tool prints it) | tags | file | disposition | Qodo now |
|---|---|---|---|---|---|---|
| 1 | Remediation recommended | Task-base updates can leave the suite stale | 🐞 Bug, ≡ Correctness | scripts/task_base.py (fix in .github/workflows/task-base.yml) | fixed in a11bf02 | resolved |
| 2 | Remediation recommended | Registry validation accepts unusable entries | 🐞 Bug, ≡ Correctness | schemas/registry.schema.json | dismissed: true that the schema does not require `task_base`, but no entry without it gets through. The schema leaves `build_commit` optional too (a precedent set in PR #10). Every gate that admits an entry checks `task_base` after the schema: `release.py inputs` (`hb/release.py:202-204`, tested by `tests/test_release.py`, "not built on the pinned task base"), `harness.py verify` (`check_task_base_current`, `hb/harness.py:250`, called at `scripts/harness.py:150`), and `harness.py pin`, which always computes the field. | open |
| 3 | Informational | A valid suite can block a task-base pin | 🐞 Bug, ≡ Correctness | hb/taskbase.py | dismissed: reproduced, but it fails closed. `pin` refuses, with an error naming the exact spelling it needs, before it writes anything (`scripts/task_base.py:63`; the first write is at `:64`), and the post-check (`hb/taskbase.py:156`) confirms that the key it changed is `runtime`'s. The rewrite is textual on purpose so that the diff is one line (`test_pin_sets_the_suites_task_base_and_nothing_else`). The suite is a maintainer-written file in this repo, where a key spelled with a JSON escape (`\u0074ask_base`) is not a realistic input. | open |

Built from fetches 1 and 2. Fetch 2 reviewed a11bf02d5dd52019f38358cc289c72df1beb9942, the PR head at
the time ("current"). Fetch 1 reviewed 6b5b3fe2f685b5bdae1ca068e5c184ada2536810. Tags for #1 are from fetch 1 (fetch 2 prints the resolved finding without tags). No finding is
absent, and every round had a re-review.

None of the findings is Action required, so nothing waits on the owner. The finding for #1 is
anchored at `scripts/task_base.py` (the `pin` command that rewrites the suite). The defect was
the instructions in `.github/workflows/task-base.yml` (header comment and run summary), and the
fix is there, with `tests/test_task_base.py::test_the_workflows_commit_steps_take_the_suite_with_the_pin`,
which fails before the fix.

A fresh sub-agent triaged the archive. A second fresh sub-agent audited this table before it was
committed.
