# Test Fixtures

Fixture vault sources live under `tests/fixtures/vaults/`.

- `basic/`: shared baseline for agent/config tests.
- `tools/`: coverage fixture for tool operations (frontmatter, headings, blocks).
- `app/`: minimal HTTP fixture.
- `integration/`: fixture copied into JJ-backed integration workspaces.

Tests never mutate these source fixtures in place. Each test gets a copied workspace
under `tests/artifacts/<run-id>/<test-id>/work`.
