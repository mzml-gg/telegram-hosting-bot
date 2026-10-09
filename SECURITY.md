# Security Policy

## Reporting a vulnerability

Please do not open a public issue for credentials, authentication bypasses, or
server-control vulnerabilities. Contact the maintainer privately through the
website listed in `README.md`.

## Credential hygiene

- Never commit `.env`, SQLite databases, bot tokens, or Pterodactyl API keys.
- Rotate credentials immediately if they are exposed.
- Use a least-privilege Pterodactyl Application API key where possible.
- Restrict the developer Telegram ID with `DEVELOPER_ID`.
