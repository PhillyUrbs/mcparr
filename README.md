# mcparr

MCPify your \*arr stack. mcparr is a self-hosted [Model Context Protocol](https://modelcontextprotocol.io)
gateway that lets AI assistants (Claude, ChatGPT, GitHub Copilot, Cursor, ...)
drive your media services - Radarr, Sonarr, and more - through a single,
authenticated MCP endpoint.

## What it does

mcparr runs as one Docker container exposing two ports:

- **7475 - config UI:** a small web app to add service connections, test them,
  toggle tools, and copy the MCP connection details. Protected by an admin
  password.
- **7474 - MCP endpoint:** a Streamable HTTP MCP server that AI clients connect
  to. Always requires a bearer token.

Each enabled service contributes a set of namespaced tools (for example
`radarr_search_movies`, `sonarr_list_series`). Toggling a service in the UI adds
or removes its tools live, without restarting the container.

## Status

Early development. The v1 surface ships the gateway core plus Radarr and Sonarr;
more services (Prowlarr, Plex, Overseerr/Seerr, Maintainerr, Lidarr) are planned.

## Quick start

```bash
docker compose up -d
```

Then:

1. Open `http://localhost:7475` and set an admin password.
2. Add a service (base URL + API key), test the connection, and enable it.
3. Open the **Connect** page to copy the MCP URL and bearer token into your AI
   client.

The config UI is published on localhost only by default. The MCP endpoint is
exposed but always requires the token.

### Connecting a client (VS Code / GitHub Copilot)

```json
{
  "servers": {
    "mcparr": {
      "type": "http",
      "url": "http://your-host:7474/mcp",
      "headers": { "Authorization": "Bearer <token-from-connect-page>" }
    }
  }
}
```

For Claude Desktop, use the `mcp-remote` bridge (snippet on the Connect page).
Hosted connectors (Claude.ai, ChatGPT) require HTTPS - put mcparr behind a TLS
reverse proxy.

## Configuration

All state lives in the `/data` volume (SQLite database, encryption key, MCP
token, audit log). Useful environment variables:

| Variable                | Default            | Purpose                                        |
| ----------------------- | ------------------ | ---------------------------------------------- |
| `MCPARR_HOST`           | `127.0.0.1`        | Bind address for both ports                    |
| `MCPARR_UI_PORT`        | `7475`             | Config UI port                                 |
| `MCPARR_MCP_PORT`       | `7474`             | MCP endpoint port                              |
| `MCPARR_DATA_DIR`       | `/data`            | State directory                                |
| `MCPARR_SECRET_KEY`     | _(generated)_      | Fernet key for secrets at rest                 |
| `MCPARR_MCP_TOKEN`      | _(generated)_      | MCP bearer token (env wins; rotation disabled) |
| `MCPARR_ADMIN_PASSWORD` | _(unset)_          | Set the admin password headlessly on first run |
| `MCPARR_SEED_FILE`      | `<data>/seed.yaml` | Optional service seed file                     |

You can pre-populate services declaratively with a seed file - see
[config.yaml.example](config.yaml.example).

## Security

The MCP endpoint always requires a token; the UI always requires a password.
Service keys are encrypted at rest. See [SECURITY.md](SECURITY.md) and the
[threat model](docs/threat-model.md) for details.

## Development

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
ruff check .
python -m mcparr.main   # runs both servers locally
```

Translations are managed with Babel (`babel.cfg`); compile catalogs with
`pybabel compile -d mcparr/ui/locales -D messages`.

## Adding a service

Create a `ServiceModule` subclass under `mcparr/services/`, declare its metadata
and tools, and decorate it with `@register`. The base class provides the HTTP
client, pagination, result shaping, and error mapping. Radarr and Sonarr are the
reference implementations.

## License

See [LICENSE](LICENSE).
