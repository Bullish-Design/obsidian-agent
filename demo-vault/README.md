# Demo Vault

This is a sample Obsidian vault for testing and verifying the Obsidian Agent service.

## Contents

- `index.md` - Root note
- `notes/` - Directory for additional notes

## Usage

### Interactive walkthrough (recommended)

Open `demo-vault` in Obsidian first, then run:

```bash
devenv shell -- obsidian-agent-demo --vault-dir demo-vault
```

Defaults used by the demo script:

- Base URL: `http://remora-server:8000/v1`
- Model: `unsloth/gemma-4-E4B`

Controls while the script is running:

- `Enter` or `Space`: run next demo step
- `Backspace`: undo the last step so you can replay it
- `q`: quit

You can also run the wrapper directly:

```bash
devenv shell -- python scripts/demo_walkthrough.py --vault-dir demo-vault
```

### HTTP service mode

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
