# Installing on Claude Code

Use the setup wizard without a host flag and let it wire the detected hosts:

```bash
npx --yes @paradigma-inc/flywheel setup --mode mcp
```

If the wizard does not offer Claude Code on your machine, register the server manually with an
MCP API key. Create the key in the Flywheel WebUI under **Settings → User → MCP API keys**, copy
it once, then:

```bash
claude mcp add --transport http flywheel https://flywheel.paradigma.inc/mcp-server --header "Authorization: Bearer <YOUR_MCP_API_KEY>"
```

Equivalent project-scoped config in `.mcp.json` at the repository root:

```json
{
  "mcpServers": {
    "flywheel": {
      "type": "http",
      "url": "https://flywheel.paradigma.inc/mcp-server",
      "headers": {
        "Authorization": "Bearer <YOUR_MCP_API_KEY>"
      }
    }
  }
}
```

Verify with `/mcp` inside Claude Code; the `flywheel` server must report as connected before any
graph write. Authentication is machine-local, so repeat this on every machine that publishes
lineage.

For the generic manual route and the OAuth connector alternative, see
[other-hosts-installation.md](other-hosts-installation.md).
