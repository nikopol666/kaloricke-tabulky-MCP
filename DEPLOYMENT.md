# Deployment

## Portainer

Use `docker-compose.yml` as a Git stack or copy the service into your homelab stack.

Set credentials only in Portainer environment variables:

```env
KT_ACCOUNTS=personal
KT_ACCOUNT_PERSONAL_EMAIL=user@example.com
KT_ACCOUNT_PERSONAL_PASSWORD=replace-me
KT_DEFAULT_ACCOUNT=personal
MCP_BEARER_TOKEN=replace-me-long-random-token
MCP_HTTP_PORT=8080
```

The container healthcheck calls `/health`. This endpoint is intentionally liveness/config-only and must not depend on Kaloricke Tabulky auth state or cached auth diagnostics.

## MCP Endpoint

Streamable HTTP endpoint:

```text
http://<host>:8080/mcp
```

If `MCP_BEARER_TOKEN` is set, clients must include:

```http
Authorization: Bearer <token>
```

Health endpoint:

```text
http://<host>:8080/health
```

## Homelab Exposure

Keep the service LAN-only unless it is protected by your MCP gateway or another auth layer. The MCP tool layer still requires explicit `account` and `commit=true` for writes, but that is not a replacement for endpoint authentication.
