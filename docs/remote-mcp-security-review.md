# Remote MCP server — security review

Status: settled on 2026-10-27, before any code, and implemented on 2026-10-28 (see `CLAUDE.md`,
"API tokens" and "MCP server"). The HTTP transport is stateless, so T5 has no session to protect.

## Scope

Today the MCP server (`mcp_server/`) runs on the user's machine, launched by the agent over stdio,
with **one** personal API token read from `.env.mcp`. The remote server serves the same 14 tools
over HTTP to many agents, each acting for its own user.

Decisions already taken:

1. **A separate service**, next to the API in `docker-compose.yml`. It stays a plain HTTP client of
   the API and never imports `src/`, so every rule the API enforces still applies to an agent.
2. **Tokens issued for the MCP server** (option B below), instead of forwarding any API token.
3. **The local stdio mode stays**, with the same tool code.

Out of scope: deploying the service, OAuth 2.1 (option C below), and the known limitations of
`CLAUDE.md` that the remote mode doesn't change.

## Target architecture

```
agent ──HTTPS──▶ reverse proxy ──HTTP──▶ MCP server ──HTTP──▶ API ──▶ Postgres / Redis
       Bearer tda_… (usage: mcp)          + service key
                                          + X-Forwarded-For
```

- The agent sends its own API token, created with the usage `mcp`.
- The MCP server forwards that token to the API, together with **its own service key**, which
  proves the request comes through the MCP server.
- The API accepts an `mcp` token **only** with a valid service key, and an `api` token only
  without one.

### Why not forward any API token (option A)

The MCP specification forbids "token passthrough": *MCP servers MUST NOT accept any tokens that
were not explicitly issued for the MCP server.* Most of its stated risks are weak here, because the
API enforces every control itself (permissions, rate limits, idempotency, audit). One applies in
full: a stolen token would work the same through the MCP server or straight against the API, and
nothing would tell the two uses apart. Option B closes that: a stolen `mcp` token is useless
without the service key, which never leaves the server.

### Why not OAuth 2.1 (option C)

Fully compliant, but it turns the API into an authorization server (client registration, consent
page, token issuance for the MCP audience). Larger than the rest of the item; left for later.

## Assets

| Asset | Where | Impact if exposed |
|---|---|---|
| Users' `mcp` tokens | sent by agents on every request, held in memory by the MCP server for that request only | act as the user through the MCP server, within what a token may do |
| The service key | MCP server and API configuration only | alone, nothing: every API request still needs a user's token. With a stolen `mcp` token, that token becomes usable directly against the API |
| Users' todos, categories, approval requests | API | read, change or soft-delete through the tools |

## Trust boundaries

1. **Internet → reverse proxy**: anyone can connect. TLS ends here.
2. **Reverse proxy → MCP server**: trusted network, but the request content is still untrusted.
3. **MCP server → API**: the API trusts the service key to mean "through the MCP server", nothing
   more. It never trusts the MCP server about **who** the user is: that comes from the token.
4. **Tool results → model**: stored texts are data, never instructions (unchanged, see `CLAUDE.md`,
   prompt injection).

## What a remote agent can reach

Exactly what a local one can today: the 14 tools, with the permissions of a non-admin API token.
It can't manage the account, act as an admin, approve its own requests, write a category name or
read the audit log. The remote mode adds no tool and no permission.

## Threats

| # | Threat | Existing protection | Gap | Measure |
|---|---|---|---|---|
| T1 | A stolen `mcp` token is used straight against the API | none: any token works on any route it is allowed | full | **service key** required with an `mcp` token; an `api` token refused when a service key is present |
| T2 | The service key leaks | — | — | secret in configuration only (never in the image, the repository or a local install); compared in constant time; rotated by redeploying both services. Alone it grants nothing |
| T3 | **One user's token sent in another user's request** inside the MCP server | none: `TodoApiClient` puts the token in the client's default headers, which is only safe with one user | full | the token is passed **per request**, never stored on a shared object; no per-user state survives a request; a test runs two users' tool calls concurrently and checks each API request carries its own token |
| T4 | Unauthenticated calls to the MCP endpoint | — | full | every HTTP request, `initialize` included, must carry a bearer token with the `tda_` prefix, or gets 401 before anything reaches the API |
| T5 | Session hijacking: a stolen or guessed MCP session id reused | — | — | authentication is checked on **every** request, never derived from the session (the specification: servers MUST NOT use sessions for authentication) |
| T6 | Token intercepted in transit | HTTPS is not set up anywhere today | full | HTTPS mandatory between agents and the reverse proxy; plain HTTP accepted on localhost only, for development |
| T7 | A web page in the user's browser calls a locally exposed server (DNS rebinding) | — | full | the SDK's transport security: allowed `Host` and `Origin` values set from configuration |
| T8 | Every request reaches the API from the MCP server's single IP: unauthenticated or invalid-token traffic all shares one IP counter of the general rate limit, so one abuser blocks it for every client | general limit (per user for valid tokens, per IP otherwise) | partial | the MCP server forwards the agent's IP in `X-Forwarded-For` and is listed in the API's `TRUSTED_PROXIES` (the mechanism built for reverse proxies) |
| T9 | Flood of large or malformed requests | API limits | partial | the SDK's request-body size limit; the token-format check of T4 before any forwarding |
| T10 | Tokens or the service key written to logs | the MCP server never prints; API error messages carry no secret | — | never log `Authorization` or the service key; a test checks an API error turned into a tool error contains neither |
| T11 | The local stdio server leaks the service key | — | — | the local mode never holds the service key: it keeps using `api` tokens, like any script |
| T12 | The MCP server is made to call another host (SSRF) | the API URL comes from configuration, never from a request | — | keep it that way: no tool takes a URL |
| T13 | Prompt injection through stored texts | `INSTRUCTIONS`, and the structural limits listed in `CLAUDE.md` | unchanged | none new; the remote mode serves more agents, not more permissions |

## Decisions

| # | Decision |
|---|---|
| D1 | The local stdio mode uses `api` tokens and never the service key (T11). |
| D2 | Tokens created before this change become `api` tokens: nothing breaks for existing scripts or the local mode. |
| D3 | The usage is chosen when the token is created and can't be changed: a user who needs the other usage creates a second token. |
| D4 | The MCP server forwards the agent's IP and is a trusted proxy for the API (T8). |
| D5 | HTTPS is the reverse proxy's job, not the application's (T6). |

## Implementation outline

1. API: a `usage` on API tokens (migration, creation schema, listing), and the service-key rule in
   `get_current_user` and the middlewares' token lookup.
2. MCP server: per-request token forwarding (T3), bearer check (T4), service key and
   `X-Forwarded-For` on API calls, transport security (T7).
3. Startup mode: stdio (as today) or HTTP, chosen at startup; a `docker-compose.yml` service for the
   HTTP mode.
4. Tests for each measure above; then `CLAUDE.md`, `README.md` and the ROADMAP item.
