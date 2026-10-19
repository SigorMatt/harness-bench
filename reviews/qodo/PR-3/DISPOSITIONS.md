# Qodo review of PR #3 — dispositions

| # | severity | finding (title as the tool prints it) | tags | file | disposition | Qodo now |
|---|---|---|---|---|---|---|
| 1 | Remediation recommended | Scratch copies can fill the runner disk | 🐞 Bug, ☼ Reliability | hb/orchestrator.py | fixed in 8826352: `run_attempt` removes its scratch directory (tars, snapshot, diff copies) together with its containers, whatever happened; the `Attempt` already holds the patch and the logs. `run_task` removes `grade-r<n>` and `probe-r<n>` once the rep is graded. Spec §6.1 steps 5 and 6 say so. `test_scratch_copies_are_removed_after_each_attempt_and_grade` (two reps, the first with a void attempt) and a new assertion in `test_workspace_over_the_limit_fails_the_job_and_cleans_up` fail without the fix. | resolved |
| 2 | Remediation recommended | Harnesses can run past their kill deadline | 🐞 Bug, ≡ Correctness | hb/orchestrator.py | fixed in a3bb383: a gateway-log read while the harness runs gets the time left before the next deadline (wall clock or budget grace), at most 30 s, with no 1-second floor; both deadlines are checked just before, so it is positive. A new 0.7 s case of `test_slow_log_reads_do_not_delay_the_wall_clock_kill` ended at 60 101 ms before the fix and at 60 001 ms after. | resolved |
| 3 | Remediation recommended | Gateway log is re-copied in full every poll | 🐞 Bug, ➹ Performance | hb/orchestrator.py | deferred: spec §10, "Early kills depend on reading the gateway log" (66b5bbb). The copy cost is real: each 2-second poll copies the whole log (up to 256 MiB, at most 30 s) and a failed copy is ignored. The claim that parsing is repeated is not: `RecordTail.feed` decodes only the bytes after `self.offset`. The effect is bounded: a void is still decided from the final log after the gateway stops, so a missed poll only delays its kill; the budget grace runs from the gateway's answer, so a late poll does not lengthen it, and only a harness that defeats every poll after its first refusal runs on, to its wall-clock limit and without the model; a log over 256 MiB fails the job. Reading only the new part of the log needs a way to read part of a file in the gateway's volume, a design change left for later. | open |

Built from fetches 1 and 2. The last fetch (2) reviewed commit
66b5bbbd7a47d0c3b157ae620f90b21654c339ea, the PR head at the time. Round 1
(fixes a3bb383 and 8826352, and the spec note 66b5bbb for the deferral) was
re-reviewed at 66b5bbb (fetch 2). Every round got a re-review. No finding is
absent from the latest fetch. No finding was Action required.

A read made seconds after the round-1 push got head 4ad493d from the pull-request
API, while the inline comments it fetched already pointed at 66b5bbb, so it took
Qodo's first round for a current review. Its comment bodies were byte-identical
to fetch 1; it was set aside and is not archived here.
