# Qodo review of PR #10 — dispositions

| # | severity | finding (title as the tool prints it) | tags | file | disposition | Qodo now |
|---|---|---|---|---|---|---|
| 1 | Action required | Release check cannot write the plan output | 🐞 Bug, ≡ Correctness | .github/workflows/release.yml | fixed in e5617ef | resolved |
| 2 | Remediation recommended | Some accepted harness names cannot build | 🐞 Bug, ≡ Correctness | hb/harness.py | fixed in 15c2966 | resolved |
| 3 | Remediation recommended | Broken harness commands pass image checks | 🐞 Bug, ≡ Correctness | hb/harness.py | fixed in 6ff7e4a | resolved |
| 4 | Remediation recommended | Background commands linger after exit | 🐞 Bug, ☼ Reliability | harnesses/baseline/agent.py | fixed in 31f8a89 | resolved |
| 5 | Remediation recommended | Long tasks can exhaust model context | 🐞 Bug, ☼ Reliability | harnesses/baseline/agent.py | deferred: spec §10 "The baseline keeps its whole conversation" (docs/spec/result-bundle.md, 10ae4a1) | open |

Built from fetches 1 and 2. Fetch 1 reviewed 2147adfe0ce2671712236707f4a26e3e0654c369
and fetch 2 reviewed 10ae4a1a1e06cdc9352e54629b636ef87cb80d56; each was the PR head at
the time ("current"). One round of fixes ran, and it was re-reviewed (fetch 2): Qodo
marked findings 1 to 4 resolved and raised nothing new or reopened; no finding is
absent. Numbers are fetch 2's; Qodo numbered findings 2 and 3 the other way round in
fetch 1.

- Finding 1, the only Action-required one, was fixed: plan runs as the runner's user.
  The new Docker test `test_release_plan_check_runs_as_release_yml_runs_it` runs
  release.yml's own plan lines and failed with EACCES before the fix.
- Finding 5 is real but deferred: the stock limits let the baseline's history outgrow
  the context window, which ends the harness with exit status 2 (`exited`, graded on
  the workspace). Bounding or summarising the history would change what the baseline
  measures. The project tracks open items in spec §10 rather than an issue tracker.

Nothing waits on the owner, and no round lacks a re-review. The table was audited by a
fresh sub-agent against the archive before it was committed.
