# Plants

[![Beta](https://img.shields.io/badge/status-beta-orange)](https://github.com/dhrandy/plants)

Plants is in beta. Features, data formats, and the API may change until a stable release.

Plants is a simple household plant care log with reminders. It shows what needs checking today, lets you tap **Done**, **Snooze**, or **Skip**, and keeps a photo timeline for every plant. You decide what each plant needs; the app just keeps track. It is self-hosted, multi-user, and runs as one Docker container with SQLite storage. No subscriptions, no cloud account, and your data stays on your server.

## Quick start

1. Run Plants with Docker, keeping its data in a folder on the host:

   ```sh
   docker run -d --name plants -p 8653:8000 -v ./data:/app/data -e TZ=America/Chicago ghcr.io/dhrandy/plants:latest
   ```

   Or with Docker Compose. Save this as `docker-compose.yml` (the repo includes the same file):

   ```yaml
   services:
     plants:
       image: ghcr.io/dhrandy/plants:latest
       container_name: plants
       restart: unless-stopped
       volumes:
         - ./data:/app/data
       environment:
         # Your timezone, so "due today" and quiet hours match your clock.
         - TZ=${TZ:-UTC}
         # Set to true once Plants is served over HTTPS (reverse proxy).
         - PLANTS_COOKIE_SECURE=${PLANTS_COOKIE_SECURE:-false}
         # IP of your reverse proxy, so login rate limits see real client addresses.
         - FORWARDED_ALLOW_IPS=${FORWARDED_ALLOW_IPS:-127.0.0.1}
       ports:
         - 8653:8000
   ```

   No `.env` file is needed. The `${VAR:-default}` values work as-is; change them in the file or in your Docker manager's environment settings. Then start it:

   ```sh
   docker compose up -d
   ```

2. Open `http://your-server:8653`.
3. The first visit shows setup. Create the first account, which becomes the administrator.

Fresh installs start with one example plant that you can edit or delete.

### Using a Docker manager

Any tool that accepts a compose file works: paste the compose block above into a new stack in Portainer, Dockhand, CasaOS, Synology Container Manager, or similar, and fill in `TZ` in its environment settings. Make sure the `./data` volume points somewhere that is backed up.

## Features

- **Today view**: Overdue, Today, and Next 7 days, with the reason for each due date ("every 7 days, last done Sep 20"). Filter by room.
- **Done, Snooze, Skip**: Done logs the care. Snooze pushes it 1, 3, 7, or any number of days. Skip means you checked and it doesn't need it yet; the cycle restarts from today without changing the last-watered date. **Log…** on a plant lets you backdate care and add a note.
- **Batch care**: tick several plants (or **Select all** in a section, filtered by room) and mark them done, snoozed, or skipped at once.
- **Plant profiles**: name, species (whatever the tag says), room, light, pot size and material, acquired date, indoor or outdoor, care notes, and a care source link.
- **Care tasks**: watering, fertilizing, misting, repotting, or custom, each with its own check interval and optional winter interval. Duplicate a plant to copy its setup.
- **Starter library**: twelve common houseplants that pre-fill species, light, care notes, and conservative starting intervals, each linked to its NC State Extension Plant Toolbox page. Everything stays editable; intervals mean "check the soil", not "water now".
- **Photo timeline**: every care entry and photo lands on the plant's timeline with who logged it, so it doubles as a growth history. Pick any timeline photo as the main photo.
- **Seasons**: choose your winter months and a winter stretch (x1.25, x1.5, x2), or set a winter interval on a specific task. Nothing changes a schedule behind your back.
- **Weather**: current conditions and a 5-day forecast (temperature, humidity, rain chance and amount) for a location you pick, with plain hints like "Rain likely tomorrow; outdoor pots may not need water". Weather never changes schedules.
- **Notifications**: daily digest or one alert per plant through [Apprise](https://github.com/caronc/apprise), with quiet hours, a send-from hour, repeat reminders for overdue plants, and links back to the plant.
- **Multi-user**: one shared set of plants for the household. Administrators manage users; every care entry records who did it.
- **API tokens** for scripts, home automation, or an AI assistant.
- **Backup**: export everything, photos included, as one JSON file, and import it back.
- **Installable**: add it to your phone's home screen; it works like an app.

## Weather

Weather comes from [Open-Meteo](https://open-meteo.com/). It is free for non-commercial use, needs no API key, and its data is licensed CC BY 4.0 (the app shows the required attribution link). In **Settings → Weather location**, search for your city and pick it. The server fetches the forecast and caches it for 30 minutes, so your browser never talks to Open-Meteo directly. **Settings → Units** switches between °F/inches/mph and °C/mm/km/h.

## Notifications

Open **Settings → Notifications** and add one or more Apprise URLs, one per line. Some common ones:

| Service | Example URL |
| --- | --- |
| ntfy (self-hosted or ntfy.sh) | `ntfys://ntfy.example.com/plants` |
| Gotify | `gotifys://gotify.example.com/APP_TOKEN` |
| Pushover | `pover://USER_KEY@APP_TOKEN` |
| Email | `mailtos://user:password@example.com` |
| Discord | `discord://WEBHOOK_ID/WEBHOOK_TOKEN` |

See the [Apprise wiki](https://github.com/caronc/apprise/wiki) for every service. Use **Send test** to check it works.

- **Style**: one daily digest, or one alert per plant.
- **Send from**: alerts wait until this hour.
- **Quiet hours**: nothing is sent between start and end.
- **Repeat overdue every**: re-send overdue plants every N days (0 turns repeats off).
- **App address**: your public URL, so alerts link straight to the plant.

The server checks every 10 minutes. Each due date is announced once. Notification URLs often contain secrets; they are stored in the database and only administrators can see them.

## API tokens

Create a token in **Settings → API tokens** (the token is shown once). Send it as `Authorization: Bearer <token>`. Interactive docs are at `/api/docs`.

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/plants` | All plants with tasks and next due dates |
| GET | `/api/v1/plants/{id}` | One plant |
| POST | `/api/v1/plants` | Add a plant (name, species, room, notes, tasks) |
| PATCH | `/api/v1/plants/{id}` | Update some fields |
| POST | `/api/v1/plants/{id}/tasks` | Add a care task |
| POST | `/api/v1/plants/{id}/photos` | Add a photo and/or note (multipart: `photo`, `note`, `date`, `main`) |
| GET | `/api/v1/due?days=7` | What's overdue, due today, or due soon |
| POST | `/api/v1/tasks/{id}/done` | Log care (`{"date": "YYYY-MM-DD", "note": "..."}`, both optional) |
| POST | `/api/v1/tasks/{id}/skip` | Skip this cycle |
| POST | `/api/v1/tasks/{id}/snooze` | Snooze (`{"days": 3}`) |
| GET | `/api/v1/rooms` | Rooms |
| GET | `/api/v1/library` | Starter library |

Example, adding a plant from a photo of its tag:

```sh
curl -X POST http://your-server:8653/api/v1/plants \
  -H "Authorization: Bearer $PLANTS_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Peace lily","species":"Spathiphyllum","room":"Office","notes":"Keep evenly moist","tasks":[{"kind":"water","interval_days":5}]}'
```

Entries made through a token are attributed to the token's owner and show the token name on the timeline. Tokens are limited to 100 requests a minute; repeated bad tokens are blocked for 15 minutes.

## Mobile layout

Plants is built for phones first. Buttons and toggles are at least 44px tall, form fields use a 16px font so iOS doesn't zoom on focus, the section tabs stay pinned at the top, dialogs open as bottom sheets, nothing scrolls sideways at 360px, and the layout respects the notch and home-indicator safe areas.

## Reverse proxy and security

Plants works behind any reverse proxy (Caddy, Nginx, Nginx Proxy Manager, Traefik, Synology's built-in proxy, and so on). Point it at `http://<docker-host>:8653`, then:

- Set `PLANTS_COOKIE_SECURE=true` once the site is on HTTPS so session cookies are only sent over HTTPS.
- Set `FORWARDED_ALLOW_IPS` to your proxy's IP so rate limits see real client addresses. Only use `*` if the container is reachable solely through the proxy.

Built in: passwords hashed with PBKDF2, HttpOnly SameSite=Strict session cookies, login rate limiting (5 failures per 15 minutes per IP), a strict Content Security Policy and other security headers, upload type and size checks (JPEG, PNG, WebP, GIF, HEIC up to 10 MB), and photos served only to signed-in users.

## Backup

**Settings → Backup → Export** downloads everything, photos included, as one JSON file. **Import** replaces all data with a backup and signs everyone out. You can also back up the `./data` folder directly (`plants.db` plus the `photos` folder).

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Timezone for due dates and quiet hours |
| `PLANTS_COOKIE_SECURE` | `false` | Send session cookies only over HTTPS |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted reverse proxy IPs |
| `PLANTS_DATA_DIR` | `/app/data` | Where the database and photos live |
| `PLANTS_NOTIFY_WORKER` | `true` | Set `false` to turn off the notification checker |
| `PLANTS_WEATHER_OFFLINE` | `false` | Set `true` to never call the weather service |

## Development

```sh
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
PYTHONPATH=. pytest -q
PLANTS_DATA_DIR=./data uvicorn app.main:app --reload
```

The test suite covers the API and runs browser tests at 1920px and 390px. GitHub Actions runs it on every push to `main` before building and publishing the image to `ghcr.io/dhrandy/plants`.

## Credits

Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0). Starter library care notes summarized from the [North Carolina Extension Gardener Plant Toolbox](https://plants.ces.ncsu.edu/).

## License

MIT
