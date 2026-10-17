**Agents fail 79% of everyday web tasks (95% CI 66%–88%), and 55% of those failures are repeats (semantic clustering; 26% by the stricter structured key). 55% were preventable with a single shared note. With our knowledge base: 21% → 12% success, 96% more steps on successful runs.**

- When an agent says it's done, it's wrong 55% of the time (false-success rate, n=22).
- Reliability gap: pass@1 21% vs pass^k 12%; 17% of tasks are flaky across repeated identical runs.
- 39% of failures are the same quirk biting a *different* task on the same site (cross-task repeats).
- 89% of all agent tokens (8.37M, $1.46) were burned on runs that failed; 52% went to knowledge-fixable failures.
- Tokens per successful task: 938k → 1.83M with the KB (95% change); 4 task(s) regressed.
