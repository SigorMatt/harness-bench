# Qodo review of PR #6 — dispositions

| # | severity | finding (title as the tool prints it) | tags | file | disposition | Qodo now |
|---|---|---|---|---|---|---|
| 1 | Remediation recommended | Task jobs can time out before finishing | 🐞 Bug, ≡ Correctness | hb/plan.py | deferred: spec §10, "The task job timeout is an estimate, not a bound" (92f8168, worded more exactly in 03030c2). The mechanism is real: `task_job_timeout_minutes()` gives each attempt 60 seconds beyond the wall clock, the budget grace, the 120-second drain and the probe (hb/plan.py:14-18, :31-37), while the toolkit's own limits allow an attempt more: 600 s for the workspace snapshot (hb/orchestrator.py:91, used at :298), a 150 s stop grace for the gateway (:90, :350; the `docker stop` call itself may take 210 s, hb/dockercli.py:201), 30 s each to listen and to stop the harness (:88-89), 120 s for any other Docker call (hb/dockercli.py:22), and untimed tree copies, `git diff` and `git apply` (hb/patch.py:43-45, :60, :64, :71). It is older than this pull request: the formula and its constants are unchanged from main at e515ffb, and hb/orchestrator.py, hb/patch.py and hb/dockercli.py are not touched; the PR only computes the estimate in `plan` for the first time (`plan_run()`, hb/plan.py:60-83). What a job does not use usually covers it: the grace never extends the wall clock (hb/orchestrator.py:267-270), the snapshot runs only on an attempt that is not void (:355-359) and the probe once per rep. For the example suite (3 reps of 1200 s, 2 re-runs per rep, probe 30 s, 226 minutes = 13,560 s), the listen, stop and snapshot limits at their maximum, with job setup and the other Docker calls left out, need 14,610 s with 6 void attempts that each end near the wall clock, and leave 360 s for setup with 5; an accepted run of that one-task suite cannot have a single re-run (`max_rerun_permille` 50, hb/accept.py:102-105), and seal refuses one (hb/seal.py:130). A task job ended by its timeout writes no bundle and the seal job `needs: run-task` (.github/workflows/bench.yml:141), so it costs the run, never its integrity. Bringing the estimate close to a bound needs the copies, the diff and the apply timed as well, which is outside this pull request; spec §5.1 and §6.2 and the docstrings in hb/plan.py no longer claim that every attempt fits or that plan refuses every run that could not complete. | open |

Built from fetches 1 and 2. Fetch 1 reviewed 149896d920d2ba158acfb6c70d093a3eb286d5b6,
the PR head at the time; fetch 2, the last, reviewed
92f8168e2fdaedd423d942d74032f23faacd0a1a, the head after the deferral was pushed,
and still lists the finding as open, as expected for a deferral. No finding is
new, reopened or absent from the latest fetch. No finding was Action required.
One round ran (the deferral, 92f8168) and was re-reviewed. Two later commits
change wording only: 089298d rewraps the docstring 92f8168 changed, and 03030c2
makes the §10 entry, spec §6.2 and the hb/plan.py docstrings claim no more than
the code does, as the audit of this table found. Neither had been re-reviewed
when this table was written; the final check after this push reads Qodo's
re-review of them, with its archive outside the repository. The finding was
triaged by a fresh sub-agent that read the code, the spec and the tests and
computed the worst case above with a script outside the repository; the table
was audited by another, and its corrections are in 03030c2 and in this text.
Line numbers are at 03030c2.
