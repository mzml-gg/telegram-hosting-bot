<div align="center">

# Telegram Hosting Bot

### A production-minded Telegram control plane for Pterodactyl hosting

<img src="assets/eagle.jpg" alt="Arab Code Hosting eagle logo" width="180" />

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot_API-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![Pterodactyl](https://img.shields.io/badge/Pterodactyl-Application_API-6f42c1)](https://pterodactyl.io/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Create, provision, manage, and monetize Python and Node.js hosting directly from Telegram.**

[Features](#features) · [Architecture](#architecture) · [Quick Start](#quick-start) · [Configuration](#configuration) · [Commands](#commands) · [Security](#security) · [Contact](#maintainer)

</div>

---

## Overview

`telegram-hosting-bot` is a Telegram-first hosting automation layer built for [Pterodactyl](https://pterodactyl.io/). It turns a Pterodactyl installation into a simple, branded hosting storefront and control panel for customers and administrators.

The project is designed for operators who want to offer managed application hosting without exposing the full panel workflow to every customer. It supports free trials, resource selection, server ownership, lifecycle actions, invoices, and subscription messaging while keeping all credentials outside the repository.

> **Important:** This repository is a reusable template. Never commit production bot tokens, Pterodactyl API keys, databases, or customer credentials.

## Features

### Customer experience

- Guided server provisioning from Telegram inline buttons.
- Python and Node.js egg discovery from the Pterodactyl Application API.
- One configurable free trial per Telegram user.
- Seven-day default trial lifecycle with automatic suspension after expiry.
- Per-user server ownership checks.
- Server list, status, resource information, suspend, unsuspend, and reinstall actions.
- Subscription/contact flow for post-trial upgrades.
- Branded welcome screen with a custom image and persistent keyboard.

### Administrator experience

- Admin-only resource wizard: egg → RAM → disk → CPU → account email.
- RAM presets from 1G to 5G plus Infinite.
- Disk presets from 1G to 10G.
- CPU presets from 100% to 200% plus Infinite.
- Automatic Pterodactyl user creation with generated credentials.
- Resource-aware provisioning and formatted invoices.
- Configurable pricing model for RAM, disk, and CPU.
- Server renewal, egg switching, password changes, bulk suspension, and bulk unsuspension.
- Official Telegram command menu registration.

### Operations and security

- Environment-based secret management.
- SQLite persistence for local ownership and trial state.
- Allocation refresh before provisioning to avoid stale allocation failures.
- One retry for transient allocation/Wings errors.
- No database, token, or API key is included in the public package.
- MIT licensed and easy to extend.

## Architecture

```text
Telegram user
     │
     ▼
python-telegram-bot  ──────►  SQLite ownership/trial state
     │
     ▼
Pterodactyl Application API
     │
     ├── Users
     ├── Servers
     ├── Nests / Eggs
     ├── Allocations
     └── Server lifecycle actions
     │
     ▼
Pterodactyl Node + Wings + Docker
```

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/telegram-hosting-bot.git
cd telegram-hosting-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure secrets

```bash
cp .env.example .env
```

Fill in `BOT_TOKEN`, `DEVELOPER_ID`, `PANEL_URL`, and `APPLICATION_API_KEY`. Load the file with your process manager or export it before starting:

```bash
set -a
source .env
set +a
python main.py
```

For production, use a systemd service, Docker secret, or another secret manager instead of placing `.env` in a public directory.

### 3. Prepare Pterodactyl

1. Create an **Application API key** with only the permissions required by your deployment.
2. Confirm that your panel contains Python and Node.js eggs.
3. Confirm that the node has free allocations.
4. Set the Telegram numeric ID of the administrator in `DEVELOPER_ID`.
5. Verify that the configured Docker images are available to Wings.

## Configuration

All runtime configuration is environment-based. Start from [`.env.example`](.env.example).

| Variable | Purpose | Example |
|---|---|---|
| `BOT_TOKEN` | Telegram Bot API token | `123456:replace-me` |
| `DEVELOPER_ID` | Numeric Telegram ID allowed to use admin tools | `123456789` |
| `PANEL_URL` | Pterodactyl panel URL | `https://panel.example.com` |
| `APPLICATION_API_KEY` | Pterodactyl Application API key | `ptla_replace_me` |
| `SERVER_DURATION_DAYS` | Customer trial duration | `7` |
| `ADMIN_SERVER_DURATION_DAYS` | Admin-created server duration | `30` |
| `PRICE_CURRENCY` | Invoice currency label | `USD` |
| `PRICE_RAM_PER_GB` | Price per RAM GB | `1.0` |
| `PRICE_DISK_PER_GB` | Price per disk GB | `0.10` |
| `PRICE_CPU_PER_100` | Price per 100% CPU | `1.0` |
| `BRAND_IMAGE` | Welcome image path | `assets/eagle.jpg` |

## Commands

### Customers

| Command | Description |
|---|---|
| `/start` | Open the branded main menu |
| `/create` | Start the guided hosting wizard |
| `/servers` | List owned servers |
| `/status` | Check bot and panel connectivity |
| `/subscribe` | Show subscription instructions |
| `/cancel` | Cancel an active wizard |

### Administrator

| Command | Description |
|---|---|
| `/stats` | Show panel statistics |
| `/renew <server_id>` | Renew a server |
| `/changegg <server_id>` | Change the server egg |
| `/chpass` | Change a user password interactively |
| `/suspendall` | Suspend all panel servers |
| `/unsuspendall` | Unsuspend all panel servers |

## Invoice and pricing model

The administrator wizard produces an invoice containing the account credentials, server ID, egg, RAM, disk, CPU, expiry date, price, and configured brand footer.

The default editable pricing model is:

- `1.00 USD` per GB of RAM.
- `0.10 USD` per GB of disk.
- `1.00 USD` per 100% CPU.
- Infinite resources are labelled **Contact developer**.

Change these values in `.env`; pricing is informational and should be connected to your own billing or payment workflow before commercial use.

## Security

- Keep `.env` private.
- Rotate a token immediately if it appears in a log, screenshot, commit, or chat.
- Never commit `hosting_bot.sqlite3`.
- Use a dedicated Pterodactyl API key and the smallest practical permission set.
- Review admin IDs before deploying the bot.
- Do not paste customer passwords into public issues or support channels.

Read [SECURITY.md](SECURITY.md) for the vulnerability-reporting policy.

## Project layout

```text
.
├── assets/eagle.jpg       # Branded welcome image
├── bot.py                 # Telegram handlers and user/admin workflows
├── config.py              # Environment-backed configuration
├── main.py                # Application entry point
├── pterodactyl.py         # Pterodactyl API client and provisioning helpers
├── requirements.txt       # Python dependencies
├── .env.example           # Safe configuration template
├── LICENSE                # MIT license
└── SECURITY.md            # Security policy
```

## Maintainer

Built and maintained by **Monte** for **Arab Code Hosting**.

- **Instagram:** [@monte4g](https://instagram.com/monte4g)
- **WhatsApp:** [+249 91 861 4328](https://wa.me/249918614328)
- **WhatsApp Channel:** [Join the channel](https://whatsapp.com/channel/0029Vb7AkG84inotOc8BXE1K)
- **Website:** [monte-dev.online](https://www.monte-dev.online)

## Contributing

Pull requests are welcome. For substantial changes, open an issue first so the design and operational impact can be discussed. Please keep credentials, production URLs, customer data, and generated databases out of commits.

## License

Released under the [MIT License](LICENSE).

<div align="center">

**ARAB CODE HOSTING · ADMIN: MONTE**

</div>
