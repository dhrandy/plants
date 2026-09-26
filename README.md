# Plants

[![Beta](https://img.shields.io/badge/status-beta-orange)](https://github.com/dhrandy/plants)

Plants is in beta. Features, data formats, and the API may change until a stable release.

Plants is a simple household plant care log with reminders. It shows what needs checking today, lets you tap **Done**, **Snooze**, or **Skip**, and keeps a photo timeline for every plant. You decide what each plant needs; the app just keeps track. It is self-hosted, multi-user, and runs as one Docker container with SQLite storage. No subscriptions, no cloud account, and your data stays on your server.

## Screenshots

**Today view** - upcoming care tasks and the weather at a glance.

![Today view with upcoming plant care tasks](docs/screenshots/plants-today-blurred.jpg)

**Plants grid** - browse plants and see what care is due.

![Plants grid with care status](docs/screenshots/plants-grid-blurred.jpg)

**Plant detail and timeline** - care tasks, photos, and activity for one plant.

![Plant detail with care tasks and timeline](docs/screenshots/plants-detail-blurred.jpg)

**Mobile view** - Plants on a phone.

<img src="docs/screenshots/plants-mobile-blurred.jpg" alt="Mobile view" width="380">

## Quick start

1. Run Plants with Docker, keeping its data in a folder on the host:

   ```sh
   docker run -d --name plants -p 8653:8000 -v ./data:/app/data -e TZ=America/Chicago ghcr.io/dhrandy/plants:latest
   ```

   The examples pull `latest`. To pin a version instead, use a version tag such as `ghcr.io/dhrandy/plants:v0.2.0`.

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
         - TZ=${TZ}
         # Set to true once Plants is served over HTTPS (reverse proxy).
         - PLANTS_COOKIE_SECURE=${PLANTS_COOKIE_SECURE}
         # IP of your reverse proxy, so login rate limits see real client addresses.
         - FORWARDED_ALLOW_IPS=${FORWARDED_ALLOW_IPS}
         # Browser push. Generate a key pair once (see "Browser push" in the readme); leave empty to keep push off.
         - PLANTS_VAPID_PUBLIC_KEY=${PLANTS_VAPID_PUBLIC_KEY}
         - PLANTS_VAPID_PRIVATE_KEY=${PLANTS_VAPID_PRIVATE_KEY}
         # Contact the push services can reach you at: mailto:you@example.com or your https:// address.
         - PLANTS_VAPID_SUBJECT=${PLANTS_VAPID_SUBJECT}
         # Photo identification with Pl@ntNet. Get a free key at https://my.plantnet.org/ (see "Photo identification" in the readme); leave empty to keep it off.
         - PLANTS_PLANTNET_API_KEY=${PLANTS_PLANTNET_API_KEY}
       ports:
         - 8653:8000
   ```

   Create a `.env` file next to `docker-compose.yml`. Docker Compose reads it automatically. Copy this example and change the values for your setup:

   ```dotenv
   TZ=UTC
   PLANTS_COOKIE_SECURE=false
   FORWARDED_ALLOW_IPS=127.0.0.1
   PLANTS_VAPID_PUBLIC_KEY=
   PLANTS_VAPID_PRIVATE_KEY=
   PLANTS_VAPID_SUBJECT=
   PLANTS_PLANTNET_API_KEY=
   ```

   Set `PLANTS_COOKIE_SECURE=true` behind HTTPS and `FORWARDED_ALLOW_IPS` to your reverse proxy's IP. Leave the push and photo ID values blank unless you set up those features below. For push, use your generated VAPID keys and a contact such as `mailto:you@example.com` for `PLANTS_VAPID_SUBJECT`. For photo ID, use your own Pl@ntNet key. Then start it:

   ```sh
   docker compose up -d
   ```

2. Open `http://your-server:8653`.
3. The first visit shows setup. Create the first account, which becomes the administrator.

Fresh installs start with one example plant that you can edit or delete.

### Using a Docker manager

Any tool that accepts a compose file works: paste the block above into a new stack in Portainer, Dockhand, CasaOS, Synology Container Manager, or similar. Set the same variables in its Environment tab instead of a `.env` file. Make sure the `./data` volume points somewhere that is backed up.

## Features

- **Today view**: Overdue, Today, and Next 7 days, with the reason for each due date ("every 7 days, last done Sep 20"). Filter by room.
- **Done, Snooze, Skip**: Done logs the care. Snooze pushes it 1, 3, 7, or any number of days; snoozed tasks show a "Snoozed to" badge and an **Unsnooze** button. **Snooze plant** (or the "all due tasks" box in the snooze menu) snoozes everything due on a plant at once. Skip means you checked and it doesn't need it yet; the cycle restarts from today without changing the last-watered date. **Log…** on a plant lets you backdate care and add a note.
- **Batch care**: tick several plants (or **Select all** in a section, filtered by room) and mark them done, snoozed, or skipped at once.
- **Plant profiles**: name, species (whatever the tag says), room, light, pot size and material, acquired date, indoor or outdoor, care notes, and a care source link.
- **Care tasks**: watering, fertilizing, misting, repotting, rotating, or custom, each with its own check interval and optional winter interval. All of them show on Today next to watering. When adding a plant, tap **+ Fertilize**, **+ Mist**, **+ Repot**, **+ Rotate** to add a task with a sensible starting interval. Duplicate a plant to copy its setup.
- **Starter library**: twelve common houseplants that pre-fill species, light, care notes, and conservative starting intervals, each linked to its NC State Extension Plant Toolbox page. Everything stays editable; intervals mean "check the soil", not "water now".
- **Photo timeline**: every care entry and photo lands on the plant's timeline with who logged it. Pick any timeline photo as the main photo.
- **Growth timeline** (optional): each plant's photos side by side, oldest first, labeled with the date and how long since the first photo. Tap one to flip through them full size.
- **One-tap quick links** (optional): every task gets its own link that logs it without signing in, made for the daily notification. See [One-tap quick links](#one-tap-quick-links).
- **Photo identification**: when adding or editing a plant, identify it from a photo through [Pl@ntNet](https://plantnet.org/) and fill the name and species from the top suggestions. With **care suggestions** on, picking a match also pre-fills starting water, fertilize, and mist intervals. Needs one free API key; see [Photo identification](#photo-identification).
- **Feature switches**: administrators can turn the growth timeline, care suggestions, and quick links on or off in **Settings → Features**. Turning one off hides it; nothing is deleted.
- **Seasons**: choose your winter months and a winter stretch (x1.25, x1.5, x2), or set a winter interval on a specific task. Nothing changes a schedule behind your back.
- **Weather**: current conditions and a 5-day forecast (temperature, humidity, rain chance and amount) for a location you pick, with plain hints like "Rain likely tomorrow; outdoor pots may not need water". Weather never changes schedules.
- **Notifications**: daily digest or one alert per plant through [Apprise](https://github.com/caronc/apprise), with quiet hours, a send-from hour, repeat reminders for overdue plants, and links back to the plant.
- **Browser push**: each person can turn on push notifications on their own phone or computer in Settings. Reminders show up even when Plants is closed, follow the same schedule as Apprise, and tapping one opens Today.
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
- **App address**: your public URL, so alerts link straight to the plant. With quick links on, each alert line carries a "Watered? Tap:" link instead.

The server checks every 10 minutes. Each due date is announced once. Notification URLs often contain secrets; they are stored in the database and only administrators can see them.

## Browser push

Plants can send care reminders straight to a browser or an installed home-screen app, with no Apprise service needed. Push uses the same schedule as Apprise (send-from hour, quiet hours, repeat overdue, digest or one per plant) and works alongside it. If one channel fails, the other still goes out.

**1. Generate a VAPID key pair once.** The keys identify your server to the browsers' push services. Run:

```sh
docker run --rm ghcr.io/dhrandy/plants:latest python -m app.vapid
```

It prints two lines:

```
PLANTS_VAPID_PUBLIC_KEY=BExamplePublicKey...
PLANTS_VAPID_PRIVATE_KEY=ExamplePrivateKey...
```

Any VAPID generator works too, for example `npx web-push generate-vapid-keys`. Keep the private key secret: don't commit it or paste it anywhere public.

**2. Add them to the compose file** (or your Docker manager's environment settings), plus a contact address for the push services, then redeploy:

```yaml
      - PLANTS_VAPID_PUBLIC_KEY=BExamplePublicKey...
      - PLANTS_VAPID_PRIVATE_KEY=ExamplePrivateKey...
      - PLANTS_VAPID_SUBJECT=mailto:you@example.com
```

`PLANTS_VAPID_SUBJECT` can be `mailto:` an email address or your `https://` address. If you leave it empty, Plants uses the **App address** from Settings when it's https, or a placeholder. Apple's push service is picky, so set it.

Keep the same keys from then on. If you replace them, every device has to turn push off and on again.

**3. Turn it on per device.** Open **Settings → Push notifications** on each phone or computer, tick **Push on this device**, and allow notifications when the browser asks. **Send test push** checks it end to end. Each person manages their own devices; the settings page shows how many other devices you have push on.

Things to know:

- **HTTPS is required.** Browsers only allow push on `https://` sites (or `localhost`). Put Plants behind a reverse proxy with a certificate first; see below.
- **iPhone and iPad** (iOS/iPadOS 16.4 or later): push only works in the installed app. In Safari tap Share, then **Add to Home Screen**, open Plants from the home screen, and turn push on there.
- **Android and desktop**: Chrome, Edge, Firefox, Brave, Opera, and Safari on macOS all work in a normal tab or installed.
- If you block notifications for the site, allow them again in the browser's site settings, then reload.
- Devices that uninstall the app or expire their subscription are removed automatically the next time Plants tries to reach them. Signing out also turns push off for that device.
- Disabled users get no push.
- The server only sends to the known push services (Google, Mozilla, Apple, Microsoft). If a browser uses a different one, add its host with `PLANTS_PUSH_HOSTS`.

## Photo identification

Plants can guess what a plant is from a photo when you add or edit it. Pick a photo, tap **Identify**, and the top suggestions show up with common names, scientific names, and confidence. Picking one fills the species (and the name, if it's still empty) - you can edit everything before saving.

Identification runs on your server, which calls the [Pl@ntNet API](https://my.plantnet.org/doc). The photo is sent to Pl@ntNet only to identify it; Plants never saves it. Pl@ntNet is free for personal use (500 identifications a day), and the app credits Pl@ntNet next to the button as its terms ask.

**1. Get a free API key.** Create an account at [my.plantnet.org](https://my.plantnet.org/), confirm the email, then open your account page and copy the API key shown there.

**2. Add it to the compose file** (or your Docker manager's environment settings), then redeploy:

```yaml
      - PLANTS_PLANTNET_API_KEY=your-key-here
```

Without a key, the add/edit plant form shows a short note that identification isn't set up, and nothing else changes.

**Care suggestions.** When you pick a match while adding a plant, Plants looks the species up in its starter library (exact species first, then the same genus) and, failing that, in a short list of broad plant groups such as cacti, succulents, aroids, ferns, and orchids. It fills the water, fertilize, and mist intervals, plus light and care notes on an exact library match, and says where the numbers came from. No extra service or key is involved. If nothing matches, it fills only the species and name. Turn it off in **Settings → Features**.

## One-tap quick links

Each task can have a quick link like `https://plants.example.com/q/<random-code>`. Opening it shows the plant and logs the task in one tap, without signing in. It's built for the daily notification: set **Settings → Notifications → App address**, and every alert line gets a "Watered? Tap:" link. **Copy link** on a task copies its link, and the API returns links for scripts that build their own digest.

- Each link is a random code for one task. It can only mark that task done or snooze it (add `?a=snooze&days=3`, 1 to 30 days). It can't read other plants, change settings, or reach the API.
- API tokens never go in a link.
- Opening a link doesn't change anything by itself; the page submits the change. So chat apps that fetch a link to build a preview can't log care by accident. Tapping the same link twice in a day logs it once.
- Entries show "via Quick link" on the timeline.
- Wrong codes are rate limited per IP (20 misses per 15 minutes), and links are limited to 100 requests a minute per IP.
- Anyone who has a link can log that task, so treat links like a house key for one chore. **Settings → Features → Reset all quick links** makes every old link stop working; new ones are issued automatically. Turning quick links off disables every link at once.

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
| GET | `/api/v1/due?days=7` | What's overdue, due today, or due soon (each item includes `quick_links` when quick links are on) |
| POST | `/api/v1/tasks/{id}/done` | Log care (`{"date": "YYYY-MM-DD", "note": "..."}`, both optional) |
| POST | `/api/v1/tasks/{id}/skip` | Skip this cycle |
| POST | `/api/v1/tasks/{id}/snooze` | Snooze (`{"days": 3}`) |
| POST | `/api/v1/tasks/{id}/unsnooze` | Clear a snooze |
| POST | `/api/v1/plants/{id}/snooze` | Snooze everything due on a plant (`{"days": 3}`) |
| GET | `/api/v1/tasks/{id}/quick-links` | One-tap links for a task (`done`, `snooze_1`, `snooze_3`) |
| GET | `/api/v1/care-suggestion?species=...` | Suggested starting intervals for a species |
| GET | `/api/v1/rooms` | Rooms |
| GET | `/api/v1/library` | Starter library |

Example, adding a plant from a photo of its tag:

```sh
curl -X POST http://your-server:8653/api/v1/plants \
  -H "Authorization: Bearer $PLANTS_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Peace lily","species":"Spathiphyllum","room":"Office","notes":"Keep evenly moist","tasks":[{"kind":"water","interval_days":5}]}'
```

Entries made through a token are attributed to the token's owner and show the token name on the timeline. Tokens are limited to 100 requests a minute; repeated bad tokens are blocked for 15 minutes.

### Sign in with an API token

On the website sign-in screen, choose **Use API token** and paste an existing token from Settings. This creates the same browser session as a username/password sign-in, with the token owner's account and permissions. The regular bearer-token API remains unchanged. A token is only shown when created; if you no longer have it, create a new one while signed in normally. Revoked tokens or tokens owned by inactive users cannot start new sessions. Sign out on shared devices, and revoke a token if it is exposed.

An administrator can hide and disable this sign-in option under **Settings → Features → API token sign-in**. It is on by default; the option does not appear during first-time setup. Both sign-in methods share the limit of five failed attempts per 15 minutes per IP. The token is sent in the POST body over HTTPS, never in the URL or application logs. Set `PLANTS_COOKIE_SECURE=true` when serving the site through HTTPS.

## Mobile layout

Plants is built for phones first. Buttons and toggles are at least 44px tall, form fields use a 16px font so iOS doesn't zoom on focus, the section tabs stay pinned at the top, dialogs open as bottom sheets, nothing scrolls sideways at 360px, and the layout respects the notch and home-indicator safe areas.

## Reverse proxy and security

Plants works behind any reverse proxy (Caddy, Nginx, Nginx Proxy Manager, Traefik, Synology's built-in proxy, and so on). Point it at `http://<docker-host>:8653`, then:

- Set `PLANTS_COOKIE_SECURE=true` once the site is on HTTPS so session cookies are only sent over HTTPS.
- Set `FORWARDED_ALLOW_IPS` to your proxy's IP so rate limits see real client addresses. Only use `*` if the container is reachable solely through the proxy.

Built in: passwords hashed with PBKDF2, HttpOnly SameSite=Strict session cookies, login rate limiting (5 failures per 15 minutes per IP), a strict Content Security Policy and other security headers, upload type and size checks (JPEG, PNG, WebP, GIF, HEIC up to 10 MB), and photos served only to signed-in users. One-tap quick links are per-task random codes with their own rate limits, never contain API tokens, and never change anything on a plain page load; see [One-tap quick links](#one-tap-quick-links).

## Backup

**Settings → Backup → Export** downloads everything, photos included, as one JSON file. **Import** replaces all data with a backup and signs everyone out. You can also back up the `./data` folder directly (`plants.db` plus the `photos` folder).

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Timezone for due dates and quiet hours |
| `PLANTS_COOKIE_SECURE` | `false` | Send session cookies only over HTTPS |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted reverse proxy IPs |
| `PLANTS_DATA_DIR` | `/app/data` | Where the database and photos live |
| `PLANTS_NOTIFY_WORKER` | `true` | Set `false` to turn off the notification checker (Apprise and push) |
| `PLANTS_VAPID_PUBLIC_KEY` | empty | Browser push public key; see [Browser push](#browser-push) |
| `PLANTS_PLANTNET_API_KEY` | empty | Pl@ntNet key for photo identification; see [Photo identification](#photo-identification) |
| `PLANTS_VAPID_PRIVATE_KEY` | empty | Browser push private key; keep it secret |
| `PLANTS_VAPID_SUBJECT` | App address or placeholder | `mailto:` or `https://` contact sent to push services |
| `PLANTS_PUSH_HOSTS` | empty | Extra push service hosts to allow, comma-separated |
| `PLANTS_WEATHER_OFFLINE` | `false` | Set `true` to never call the weather service |

## Development

```sh
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
PYTHONPATH=. pytest -q
PLANTS_DATA_DIR=./data uvicorn app.main:app --reload
```

The test suite covers the API (including real push encryption, checked by decrypting it with a browser-style key) and runs browser tests at 1920px and 390px. The push browser test uses full Chromium, which `playwright install chromium` includes. GitHub Actions runs it on every push to `main` before building and publishing the image to `ghcr.io/dhrandy/plants`.

## Credits

Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0). Starter library care notes summarized from the [North Carolina Extension Gardener Plant Toolbox](https://plants.ces.ncsu.edu/).

## License

MIT
