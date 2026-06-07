# Security Policy

## Reporting a vulnerability

Please report security issues privately via [GitHub Security Advisories](https://github.com/PhillyUrbs/mcparr/security/advisories/new)
rather than opening a public issue. Include reproduction steps and the affected
version. We aim to acknowledge reports within a few days.

## Scope and threat model

mcparr is a homelab tool that bridges AI clients to media services that hold real
control over your library. Read [docs/threat-model.md](docs/threat-model.md) for
the trust boundaries and the assumptions the design makes.

Key points:

- The MCP endpoint (port 7474) **always** requires a bearer token. There is no
  unauthenticated mode.
- The config UI (port 7475) requires an admin password and should be bound to
  localhost or placed behind a trusted reverse proxy. It holds every service API
  key and can rotate the MCP token.
- Service API keys are encrypted at rest with a key stored as a `0600` file under
  the data volume (or supplied via `MCPARR_SECRET_KEY`).
- For hosted connectors (Claude.ai, ChatGPT), terminate TLS at a reverse proxy.
