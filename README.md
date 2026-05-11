# Muad Reminder Bot

Telegram-bot dlya razovykh i regulyarnykh napominaniy s privyazkoy k chatam i topikam, inline-chernovikami i web-panel'yu upravleniya.

## Features

- `aiogram` bot with `/bind`, `/menu`, `/new`, `/targets`, `/testtopic`
- active reminder storage in PostgreSQL
- recurring reminders: once, daily, weekly, monthly
- inline mode for quick drafts like `@muadnotsbot napomni zavtra v 15:00 oplatit' podpisku`
- FastAPI panel for viewing, editing and toggling reminders
- Caddy reverse proxy and Docker Compose deployment
- install and manage scripts for Linux and Windows

## Quick start

1. Edit `.env` and fill:
   - `BOT_TOKEN`
   - `BOT_USERNAME`
   - `ADMIN_USER_ID`
   - `PUBLIC_BASE_URL`
   - `POSTGRES_PASSWORD`
   - `DATABASE_URL` can be left empty, the app will build it from `POSTGRES_*`
2. Linux:
   ```bash
   chmod +x scripts/install.sh scripts/manage.sh
   ./scripts/install.sh
   ```
3. Windows PowerShell:
   ```powershell
   .\scripts\install.ps1
   ```
4. Open the panel at the URL from `PUBLIC_BASE_URL`.

## Manage

Linux:

```bash
./scripts/manage.sh start
./scripts/manage.sh stop
./scripts/manage.sh logs backend
./scripts/manage.sh status
./scripts/manage.sh panel
```

Windows:

```powershell
.\scripts\manage.ps1 start
.\scripts\manage.ps1 stop
.\scripts\manage.ps1 logs backend
.\scripts\manage.ps1 status
.\scripts\manage.ps1 panel
```

## Bot flow

1. Add the bot to the target closed chat or supergroup with topics.
2. Send `/bind` inside the target chat or exact topic.
3. Open the bot in private chat and use `/menu`.
4. Create reminders and choose repeat mode.
5. Use `/testtopic` to verify delivery.

## Current limitations

- Telegram inline mode does not expose target chat id to the bot, so inline reminders are finalized through private chat confirmation.
- Topic names fall back to `Topic #ID` when Telegram does not send a richer title in the update payload.
