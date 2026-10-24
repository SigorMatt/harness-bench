# Qodo review of PR #14 — dispositions

| # | severity | finding (title as the tool prints it) | tags | file | disposition | Qodo now |
|---|---|---|---|---|---|---|
| 1 | Remediation recommended | Unstable probe results pass curation | 🐞 Bug, ≡ Correctness | hb/curate.py | fixed in 8e2d471 | resolved |
| 2 | Remediation recommended | Ignored artifacts can fail curation | 🐞 Bug, ≡ Correctness | hb/curate.py | fixed in 97de7c0 | resolved |
| 3 | Informational | A bad solution file crashes the whole suite check | 🐞 Bug, ☼ Reliability | hb/curate.py | fixed in 97de7c0 | resolved |

- 1: the two probe runs are compared with `strict_equal`, as grading compares, not with `==`.
  Test: a probe that reports 1, then 1.0 (`tests/test_suite.py`, curation rule cases).
- 2 and 3, one change: `checks/solution` is read with `materialize` and the suite's ignore globs, as
  every other tree is, before it is laid over pristine; an entry that is not a regular file (a
  symlink, dangling or not) is reported as a curation problem instead of being followed or raising.
  Tests: a `__pycache__/*.pyc` in the solution passes curation; a dangling symlink and a symlink to a
  file are each reported.

No fix changes a task's expected probe values (`checks/target.json`, `checks/preservation.json`).
No finding was Action required.

Built from fetches 1 and 2. Fetch 1 reviewed 9b1a67cac6bc540785088566d6ebf047523c2bcc; fetch 2
reviewed 97de7c03b0deb848d654b8e7c7ebaca4a6f878c6, the PR head at the time ("current"), and shows all
three findings struck through and marked resolved, with header counts 🐞 Bugs (0), 📘 Rule
violations (0), 📜 Skill insights (0). No finding is absent. One round of fixes ran, and it was
re-reviewed.

Nothing waits on the owner.

A fresh sub-agent triaged the archive and verified each claim with an experiment on a copy of the
example suite. A second fresh sub-agent audited this table before it was committed.
