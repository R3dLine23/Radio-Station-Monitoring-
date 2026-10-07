# Backstreet Boys Radio Monitor

Watches **KISS 92.5** and **CHFI 98.1** (Toronto) around the clock and alerts your
phone the moment either one starts playing a Backstreet Boys song, so you can text in
for the contest before anyone else does.

- **Fast detection.** Every 15 seconds it checks the "now playing" data each station
  publishes on its own website (the same data the station's player shows), so a BSB
  song is spotted within seconds of starting.
- **Alerts that act.** On your phone, tap the alert and a text to the station opens
  with your contest keyword already typed.
- **Watches itself.** If a station's feed drops, you get a "monitoring is DOWN" alert
  so you know to listen yourself until it reconnects (which happens automatically).
- **No dependencies.** Plain Python 3.11+ standard library. Runs on a laptop,
  Raspberry Pi, or a $5 VPS.

## Quick start

```bash
git clone <this repo> && cd Radio-Station-Monitoring-
cp config.example.toml config.toml

# 1. Check that both stations are reachable and sending song titles:
python3 -m radio_monitor --probe

# 2. Set up phone alerts (see below), then send yourself a test:
python3 -m radio_monitor --test-notify

# 3. Run it:
python3 -m radio_monitor
```

Every song change on both stations scrolls by in the log, and a match looks like this:

```
13:02:11 INFO    [KISS 92.5] now playing: Backstreet Boys - I Want It That Way  <-- MATCH (artist: backstreet)
```

## Getting the alert on your phone (recommended: ntfy)

[ntfy](https://ntfy.sh) is free and needs no account:

1. Install the **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) /
   [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. In the app, subscribe to a topic name nobody else would guess, e.g. `bsb-contest-7f3k2`.
3. In `config.toml`, set `[notify.ntfy] enabled = true` and the same `topic`.
4. Run `python3 -m radio_monitor --test-notify`. Your phone should buzz.

Priority 5 (the default) is ntfy's "max" level: a loud, persistent notification. On
Android you can also let it bypass Do Not Disturb.

Other built-in options, which you can combine: **Pushover**, **Telegram**,
**Twilio SMS**, **Discord/Slack webhook**, and the console (terminal bell). See
`config.example.toml`.

### Pre-fill the contest text

Fill in each station's `text_number` and `text_message` from the contest rules:

```toml
[[stations]]
name = "KISS 92.5"
source = "web"
url = "https://www.kiss925.com/"
call_letters = "CKIS"
text_number = "92592"      # whatever number the contest says
text_message = "BACKSTREET"
```

The alert then reads `TEXT NOW: 92592 "BACKSTREET"`, and tapping it (ntfy and
Pushover) opens your messaging app with the text ready to send.

## What counts as a match

| Match | Example metadata | Alert |
|---|---|---|
| **Artist** (`backstreet`, `backstreetboys`, `back street boys`, `bsb`) | `Backstreet Boys - Larger Than Life`, `Steve Aoki & Backstreet Boys - ...`, `Everybody (Backstreet's Back)` | 🚨 **BACKSTREET BOYS on KISS 92.5!** |
| **Song title only** (built-in list of distinctive BSB titles) | `I Want It That Way` with no artist | 👀 *Possible Backstreet Boys song...* |

Upper/lower case, punctuation, accents, and either "Artist - Title" or
"Title - Artist" order all work. Titles that other artists also own and these stations
play ("Drowning", "Incomplete", Elton John's "Don't Go Breaking My Heart", "Last
Christmas") are left off the title list on purpose so they don't cause false alarms.
Add your own with `extra_song_titles`.

Test any string:

```bash
python3 -m radio_monitor --check "BACKSTREET BOYS/SHAPE OF MY HEART"
```

`play_history.csv` records every song both stations play, which is handy for
seeing the station's exact metadata format.

## Keeping it running 24/7

It has to be running when the song plays, so put it somewhere that stays on.

**Raspberry Pi / Linux box (systemd)**: restarts on crash and on boot:
```bash
sudo cp deploy/radio-monitor.service /etc/systemd/system/
sudo nano /etc/systemd/system/radio-monitor.service   # fix User= and WorkingDirectory=
sudo systemctl daemon-reload && sudo systemctl enable --now radio-monitor
journalctl -u radio-monitor -f
```

**Docker** (any VPS, NAS, etc.):
```bash
docker compose up -d --build
docker compose logs -f
```
(Set `history_file = "data/play_history.csv"` to keep the log outside the container.)

**Google Cloud**: see [Run it on Google Cloud](#run-it-on-google-cloud) below.

**Laptop**: `python3 -m radio_monitor` works fine, but turn off sleep. A sleeping
laptop misses songs. You'll get a "monitoring is DOWN" alert when it wakes.

Bandwidth: each check downloads the station's homepage (compressed), roughly 1–2 GB
per station per day at the default 15-second interval. That's no concern on home
internet, and incoming data is free on Google Cloud. Raise `poll_seconds` to use
less.

## Run it on Google Cloud

A small always-on Compute Engine VM runs the monitor as a system service. Everything
below runs in **Cloud Shell** (the `>_` button at the top of
[console.cloud.google.com](https://console.cloud.google.com)), so you don't need to
install anything. Cloud Shell works from a phone browser too.

You need a Google Cloud project with billing turned on. The free tier also needs a
billing account on file.

**1. Create the VM.** The startup script installs everything on first boot.
Replace `YOUR-NTFY-TOPIC` with the topic you subscribed to in the ntfy app:

```bash
git clone https://github.com/R3dLine23/Radio-Station-Monitoring-.git
cd Radio-Station-Monitoring-
gcloud services enable compute.googleapis.com

gcloud compute instances create radio-monitor \
  --zone=northamerica-northeast2-a \
  --machine-type=e2-micro \
  --image-family=debian-12 --image-project=debian-cloud \
  --metadata=ntfy-topic=YOUR-NTFY-TOPIC \
  --metadata-from-file=startup-script=deploy/gcp/startup.sh
```

`northamerica-northeast2` is **Toronto**. Canadian stations sometimes block listeners
outside Canada, and a Toronto VM avoids that. It costs roughly US$7–10/month,
including the disk and public IP; the console shows the exact estimate. To try the
**free tier** instead, use `--zone=us-central1-a --boot-disk-type=pd-standard`. If the
check in step 2 shows `HTTP 403` for the stations there, the site is blocking US
visitors, so delete that VM and recreate it in Toronto.

Bandwidth isn't a cost here: Google doesn't charge for incoming data, and the page
checks are all incoming.

**2. Check that it's working** (give it about a minute after creation):

```bash
gcloud compute instances get-serial-port-output radio-monitor \
  --zone=northamerica-northeast2-a | grep -A12 "checking that the stations"
```

Each station should show a `title:` line. Your phone should also get a
**"Backstreet Boys monitor started"** push alert.

**3. Watch the live log or change settings** (contest text numbers, extra titles):

```bash
gcloud compute ssh radio-monitor --zone=northamerica-northeast2-a

# then, on the VM:
sudo journalctl -u radio-monitor -f            # live log, Ctrl+C to exit
sudo nano /opt/radio-monitor/config.toml       # edit text_number / text_message etc.
sudo systemctl restart radio-monitor           # apply changes
```

**Updating:** reboot the VM
(`gcloud compute instances reset radio-monitor --zone=northamerica-northeast2-a`). The
startup script pulls the latest code from `main` and keeps your `config.toml`.

**When the contest is over:**
`gcloud compute instances delete radio-monitor --zone=northamerica-northeast2-a` stops
all charges.

## How it works

```
 kiss925.com  (every 15 s) ──┐                         ┌─> ntfy / Pushover / Telegram / SMS / webhook
                             ├─> now_playing ─> matcher ─> dispatcher (all notifiers in parallel)
 chfi.com     (every 15 s) ──┘   (1 thread / station)  │
                                                       └─> play_history.csv
```

- `radio_monitor/web.py` fetches the station homepage (gzip, cache-busted) and reads
  the `now_playing` artist and title that the site's Next.js page embeds for its
  player, picking the entry for the station's `call_letters`. During ads or talk,
  when the station shows no current song, it keeps the last one. If the station
  disappears from the page for 3 minutes, you get a "monitoring is DOWN" alert
  instead of the monitor silently going blind, and the first such page is saved
  to `debug/<call letters>-no-song.html` for troubleshooting.
- `radio_monitor/icy.py` is the other source type, for streams that do carry song
  titles. It connects with `Icy-MetaData: 1` and reads the `StreamTitle='...'` blocks.
  The Rogers streams (`rogers-hls.leanstream.co/.../icy`) only ever send the station
  name, which is why KISS and CHFI use the website source.
- `radio_monitor/monitor.py` sends one alert per play. Brief metadata flicker
  (a station ID between two halves of the same song) doesn't trigger a second
  alert, but two different BSB songs back to back each get one. Errors retry with
  exponential backoff (2 s → 60 s).
- `radio_monitor/matcher.py` holds the artist and title matching rules.
- `tools/discover.py` investigates a new station: it watches the ICY/HLS metadata
  and scans the website for now-playing data. It's how the website source was found.

### Adding or changing stations

For other Rogers stations, use their homepage with `source = "web"` and the
`call_letters` that `python3 tools/discover.py` shows next to `now_playing`. For any
Shoutcast/Icecast stream that carries song titles, use `source = "icy"` with the
stream URL. Either way, confirm with `--probe`, which warns you if a source only
returns the station's own name.

## Development

```bash
python3 -m unittest discover -s tests -t . -v
```

The tests include a fake local ICY radio server and a fake station website built
from the real page structure, so the full pipeline (source → match → alert) runs
without network access.
