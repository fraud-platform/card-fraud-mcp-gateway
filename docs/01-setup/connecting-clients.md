# Connecting MCP Clients

The gateway exposes a single Streamable HTTP MCP endpoint:

```
http://localhost:8005/mcp           (local, no auth)
https://your-gateway-host/mcp      (production, Bearer token required)
```

---

## Local development (no auth)

When running locally with `uv run doppler-local`, the gateway starts with
`APP_ENV=local` and `SECURITY_SKIP_JWT_VALIDATION=true`. **No token is needed.**

### Claude Code

Add to your Claude Code MCP settings (`.claude/settings.json` or via the MCP UI):

```json
{
  "mcpServers": {
    "card-fraud-gateway": {
      "url": "http://localhost:8005/mcp"
    }
  }
}
```

Then run `/mcp` or restart Claude Code to connect.

---

### GitHub Copilot (VS Code extension)

Open VS Code settings (`Ctrl+,` / `Cmd+,`), search for **MCP**, and add:

```json
{
  "github.copilot.chat.mcp.servers": {
    "card-fraud-gateway": {
      "url": "http://localhost:8005/mcp"
    }
  }
}
```

Restart VS Code. The tools appear in Copilot Chat when the gateway is running.

---

### GitHub Copilot CLI

```bash
# One-off connection test
gh copilot suggest --mcp-server http://localhost:8005/mcp "list all Kafka topics"
```

For persistent sessions, set in your shell profile:

```bash
export COPILOT_MCP_SERVER=http://localhost:8005/mcp
```

---

### Codex CLI

In your `codex.toml` or the Codex project config:

```toml
[mcp_servers]
[mcp_servers.card-fraud-gateway]
url = "http://localhost:8005/mcp"
```

Or via environment:

```bash
CODEX_MCP_SERVER=http://localhost:8005/mcp codex "show me the fraud.db schema"
```

---

### OpenCode

In your project's `.opencode/config.json` (or `~/.config/opencode/config.json`):

```json
{
  "mcp": {
    "card-fraud-gateway": {
      "type": "remote",
      "url": "http://localhost:8005/mcp"
    }
  }
}
```

Or start a session with the server pre-connected:

```bash
opencode --mcp http://localhost:8005/mcp "show me top fraud transactions"
```

---

### Custom UI / chatbot (HTTP client)

The gateway speaks standard Streamable HTTP MCP. Connect any HTTP client in two steps:

**Step 1 — Initialize session:**
```python
import httpx, re, json

BASE = "http://localhost:8005/mcp"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

resp = httpx.post(BASE, headers=HEADERS, json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "my-ops-ui", "version": "1.0"}
    }
})
# CRITICAL: save the session ID — all subsequent calls require it
session_id = resp.headers["mcp-session-id"]
```

**Step 2 — Call any tool:**
```python
resp = httpx.post(BASE, headers={**HEADERS, "mcp-session-id": session_id}, json={
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {
        "name": "postgres.query_readonly",
        "arguments": {
            "sql": "SELECT transaction_id, transaction_amount, decision FROM fraud_gov.transactions ORDER BY transaction_amount DESC LIMIT 10"
        }
    }
}, timeout=30)

# Parse SSE response
data = re.search(r"^data: (.+)$", resp.text, re.MULTILINE).group(1)
result = json.loads(data)
rows = json.loads(result["result"]["content"][0]["text"])["rows"]
```

**JavaScript/Node.js:**
```js
const BASE = "http://localhost:8005/mcp";
const headers = { "Accept": "application/json, text/event-stream", "Content-Type": "application/json" };

// Initialize
const init = await fetch(BASE, {
  method: "POST", headers,
  body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize",
    params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "ops-ui", version: "1" } }
  })
});
const sessionId = init.headers.get("mcp-session-id");

// Call a tool
const resp = await fetch(BASE, {
  method: "POST",
  headers: { ...headers, "mcp-session-id": sessionId },
  body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call",
    params: { name: "platform.inventory", arguments: {} }
  })
});
const text = await resp.text();
const data = JSON.parse(text.match(/^data: (.+)$/m)[1]);
```

> **Key requirements for custom clients:**
> - `Accept: application/json, text/event-stream` header is **required** on every request (server returns 406 without it)
> - `mcp-session-id` header is **required** on all calls after `initialize` (server returns 400 without it)
> - Responses are SSE format: `event: message\ndata: {json}\n\n` — extract the `data:` line
> - Session IDs are invalidated on server restart — re-initialize after any restart

---

### curl (raw protocol)

```bash
# Step 1 — Initialize (save the mcp-session-id from the response headers)
SESSION=$(curl -s -D - -X POST http://localhost:8005/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' \
  | grep -i mcp-session-id | awk '{print $2}' | tr -d '\r')

# Step 2 — List all tools
curl -s -X POST http://localhost:8005/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# Step 3 — Call a tool (example: list Kafka topics)
curl -s -X POST http://localhost:8005/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"kafka.list_topics","arguments":{}}}'
```

---

## Production (Auth0 Bearer token required)

In production, every request to `/mcp` must include a valid Auth0 JWT in the
`Authorization: Bearer <token>` header.

### Obtain a token

```bash
export CLIENT_ID="<from Doppler GATEWAY_AUTH0_CLIENT_ID>"
export CLIENT_SECRET="<from Doppler GATEWAY_AUTH0_CLIENT_SECRET>"
export AUTH0_DOMAIN="<from Doppler GATEWAY_AUTH0_DOMAIN>"

TOKEN=$(curl -s -X POST \
  https://$AUTH0_DOMAIN/oauth/token \
  -H "Content-Type: application/json" \
  -d "{
    \"grant_type\": \"client_credentials\",
    \"client_id\": \"$CLIENT_ID\",
    \"client_secret\": \"$CLIENT_SECRET\",
    \"audience\": \"https://card-fraud-mcp-gateway\"
  }" | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

### Claude Code (production)

```json
{
  "mcpServers": {
    "card-fraud-gateway": {
      "url": "https://your-gateway-host/mcp",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}
```

### GitHub Copilot VS Code (production)

```json
{
  "github.copilot.chat.mcp.servers": {
    "card-fraud-gateway": {
      "url": "https://your-gateway-host/mcp",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}
```

### curl (production)

```bash
curl -s -X POST https://your-gateway-host/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

---

## Verify the connection

Regardless of client, these REST endpoints are always accessible without auth and are
useful for quickly verifying connectivity:

```bash
curl http://localhost:8005/health    # {"status":"ok"}
curl http://localhost:8005/ready     # per-backend readiness
curl http://localhost:8005/catalog   # full tool/resource/prompt catalog
```

---

## Available tools quick reference

See [`../07-reference/tool-catalog-and-scope-matrix.md`](../07-reference/tool-catalog-and-scope-matrix.md)
for the full catalog with scope requirements. The `/catalog` endpoint always reflects
the live state.
