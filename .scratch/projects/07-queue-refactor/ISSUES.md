# ISSUES

## Open Roadblocks
- None currently.

## Design Questions Resolved During Review
- **Q: Should we use SQLite for durability?** Resolved: No. In-memory is sufficient for a local single-instance tool. See D-001.
- **Q: Should existing sync endpoints get a compatibility bridge?** Resolved: No. They block on job completion (same behavior). See D-005.
- **Q: Should rollout be staged with feature flags?** Resolved: No. Coordinated consumer migration. See D-006.

## Escalation Rule
- If the same problem fails 3 times, create `ISSUE_<num>.md` with exact attempts, observed failures, current hypothesis, and proposed next experiments.
- Link each issue file from this index.
