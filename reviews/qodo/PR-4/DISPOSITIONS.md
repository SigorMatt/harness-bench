# Qodo review of PR #4 — dispositions

| # | severity | finding (title as the tool prints it) | tags | file | disposition | Qodo now |
|---|---|---|---|---|---|---|
| 1 | Remediation recommended | Shutdown can fail a job when no void happened | 🐞 Bug, ≡ Correctness | hb/gateway.py | fixed in 8011b9b: the claim holds, but in fewer cases than it says. A request that shutdown abandoned (recorded `usage_unknown`, never a void) got exit status 3 when shutdown cut a read already under way: an answer's body after its headers (an error answer once retries were spent included), or a stream inside an HTTP chunk not yet fully received. It also could when shutdown came between the relay's last abandoned check and `_fail`. A stream cut on a chunk boundary ends as a normal end of stream, and the retry backoff and the wait for headers already returned before `_fail`. `_decide` now sets the status in its void branch, under the exchange's lock and after the abandoned check. Such a request therefore no longer fails the job. Every void after an attempt that could not reach the provider still exits 3, before its record exists, and no such failure voids again. `test_shutdown_while_reading_the_answer_after_an_unreachable_attempt_is_not_a_failure` fails without the fix (exit 3). `test_the_exit_status_is_set_before_the_void_is_recorded` now samples the status when the record is built and checks exit 3. | resolved |

Built from fetches 1 and 2. The last fetch (2) reviewed commit
8011b9b1e093ff9ae45dc61cf7e8ded84a4de8a3, the PR head at the time. Round 1
(fix 8011b9b) was re-reviewed at 8011b9b (fetch 2). Every round got a
re-review. No finding is absent from the latest fetch. No finding was Action
required. The tags are from fetch 1: fetch 2 prints the resolved finding without
them.
