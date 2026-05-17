# Security Policy

## Credentials

Do not commit Kaloricke Tabulky credentials. Configure them through environment variables or Portainer secrets/environment values.

The server never intentionally logs:

- account passwords
- cookie headers
- password hashes
- raw credential environment values

## Write Safety

Every write tool requires an explicit `account` and writes only when `commit=true`.

## Reporting

Open a GitHub issue without secrets, cookies, or private diary data.

