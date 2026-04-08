# Demo Vault

This is a sample Obsidian vault for testing and verifying the Obsidian Agent service.

## Contents

- `index.md` - Root note
- `notes/` - Directory for additional notes

## Usage

Start the agent service pointing at this vault:

```bash
AGENT_VAULT_DIR=demo-vault devenv shell -- python -m obsidian_agent
```

Then test endpoints:

```bash
# Health check
curl -sS http://127.0.0.1:8081/api/health

# Apply an instruction
curl -sS -X POST http://127.0.0.1:8081/api/apply \
  -H 'Content-Type: application/json' \
  -d '{"instruction":"Add a summary section","current_file":"index.md"}'

# Undo the last change
curl -sS -X POST http://127.0.0.1:8081/api/undo
```
