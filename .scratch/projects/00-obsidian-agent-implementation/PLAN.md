# PLAN

## Objective
Implement the standalone `obsidian-agent` service per the simplified architecture concept.

## Steps
1. Set up repository structure and dependencies.
2. Implement config and API models.
3. Migrate foundation modules (fs/locks/page context).
4. Implement VCS, tool runtime, and agent loop.
5. Implement FastAPI endpoints (`/api/apply`, `/api/undo`, `/api/health`).
6. Add tests and run full verification.

## Exit Criteria
- API contract is implemented.
- Tests pass.
- Manual apply/undo flow validated.
