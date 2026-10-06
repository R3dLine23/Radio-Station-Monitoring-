"""Watch every station at once and send alerts when a Backstreet Boys song comes on."""

from __future__ import annotations

import csv
import logging
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from .icy import Metadata, iter_metadata
from .matcher import Match, Matcher
from .notifiers import Alert

log = logging.getLogger(__name__)


@dataclass
class Station:
    name: str
    url: str
    text_number: str = ""
    text_message: str = ""

    def sms_link(self) -> str | None:
        """An ``sms:`` link that opens a text to the station with the message filled in."""
        if not self.text_number:
            return None
        number = "".join(c for c in self.text_number if c.isdigit() or c == "+")
        if not self.text_message:
            return f"sms:{number}"
        return f"sms:{number}?&body={urllib.parse.quote(self.text_message)}"


@dataclass
class Settings:
    stale_after_seconds: float = 180
    realert_after_seconds: float = 900
    reconnect_min_seconds: float = 2
    reconnect_max_seconds: float = 60
    read_timeout_seconds: float = 30
    history_file: str = ""
    notify_on_start: bool = True
    notify_on_outage: bool = True


MetadataSource = Callable[[str, float], Iterator["Metadata | None"]]


class Dispatcher:
    """Send each alert to every notifier at the same time, off the reader threads."""

    def __init__(self, notifiers: list):
        self.notifiers = notifiers
        self._pool = ThreadPoolExecutor(max_workers=max(4, len(notifiers) * 2), thread_name_prefix="notify")

    def _deliver(self, notifier, alert: Alert) -> None:
        for attempt in range(3):
            try:
                notifier.send(alert)
                return
            except Exception as exc:  # noqa: BLE001 - one bad notifier must not stop the others
                log.warning("%s notification failed (attempt %d/3): %s", notifier.name, attempt + 1, exc)
                time.sleep(1 + attempt * 2)
        log.error("%s notification gave up: %s", notifier.name, alert.title)

    def send(self, alert: Alert) -> list:
        return [self._pool.submit(self._deliver, n, alert) for n in self.notifiers]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


class StationWatcher:
    """Follows one station's metadata stream and decides when to alert.

    The decision logic (:meth:`handle_title`, :meth:`check_stale`) is kept
    apart from the network loop so tests can drive it directly.
    """

    def __init__(self, station: Station, matcher: Matcher, dispatcher: Dispatcher,
                 settings: Settings, source: MetadataSource = iter_metadata,
                 history: "PlayHistory | None" = None, clock: Callable[[], float] = time.monotonic):
        self.station = station
        self.matcher = matcher
        self.dispatcher = dispatcher
        self.settings = settings
        self.source = source
        self.history = history
        self.clock = clock

        self.current_title: str | None = None
        self.last_alert_title: str | None = None
        self.last_alert_at: float | None = None
        self.last_seen_at: float = clock()
        self.outage_reported = False
        self.connected_once = False

    # ---- decision logic ----------------------------------------------------

    def handle_title(self, title: str, searchable: str | None = None) -> Match | None:
        now = self.clock()
        self.mark_alive(now)
        if title == self.current_title:
            return None
        self.current_title = title
        match = self.matcher.match(searchable or title)
        log.info("[%s] now playing: %s%s", self.station.name, title or "(blank)",
                 f"  <-- MATCH ({match.kind}: {match.matched})" if match else "")
        if self.history:
            self.history.record(self.station.name, title, match)
        if not match:
            return None

        recently = (self.last_alert_at is not None
                    and now - self.last_alert_at < self.settings.realert_after_seconds)
        if title == self.last_alert_title and recently:
            # The metadata flickered (e.g. a station ID slipped in) but it's the same play.
            log.info("[%s] same song as the last alert, not alerting again", self.station.name)
            return match
        self.last_alert_title = title
        self.last_alert_at = now
        self.dispatcher.send(self._song_alert(title, match))
        return match

    def mark_alive(self, now: float | None = None) -> None:
        self.last_seen_at = self.clock() if now is None else now
        if self.outage_reported:
            self.outage_reported = False
            log.info("[%s] metadata stream recovered", self.station.name)
            if self.settings.notify_on_outage:
                self.dispatcher.send(Alert(
                    title=f"{self.station.name}: monitoring is back",
                    message=f"Metadata from {self.station.name} is coming in again.",
                    urgent=False, tags=["white_check_mark"],
                ))

    def check_stale(self) -> bool:
        """Report once if the station has gone quiet. Returns True if it is stale."""
        silent_for = self.clock() - self.last_seen_at
        stale = silent_for > self.settings.stale_after_seconds
        if stale and not self.outage_reported:
            self.outage_reported = True
            log.warning("[%s] no metadata for %.0fs", self.station.name, silent_for)
            if self.settings.notify_on_outage:
                self.dispatcher.send(Alert(
                    title=f"{self.station.name}: monitoring is DOWN",
                    message=(f"No data from {self.station.name} for {silent_for:.0f}s. "
                             "Songs on this station may be missed until it reconnects. "
                             "Keep listening in the meantime."),
                    urgent=False, tags=["warning"],
                ))
        return stale

    def _song_alert(self, title: str, match: Match) -> Alert:
        s = self.station
        if match.confident:
            headline = f"BACKSTREET BOYS on {s.name}!"
        else:
            headline = f"Possible Backstreet Boys song on {s.name}"
        lines = [f"Now playing: {title}"]
        if not match.confident:
            lines.append(f"(Matched the song title \"{match.matched}\" without the artist name. Check the radio.)")
        if s.text_number:
            lines.append(f"TEXT NOW: {s.text_number}" + (f"  \"{s.text_message}\"" if s.text_message else ""))
        lines.append("Detected at " + datetime.now().strftime("%I:%M:%S %p").lstrip("0"))
        return Alert(
            title=headline,
            message="\n".join(lines),
            urgent=True,
            tags=["rotating_light", "microphone"] if match.confident else ["eyes"],
            click_url=s.sms_link(),
        )

    # ---- network loop ------------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        delay = self.settings.reconnect_min_seconds
        while not stop.is_set():
            try:
                log.info("[%s] connecting to %s", self.station.name, self.station.url)
                for meta in self.source(self.station.url, self.settings.read_timeout_seconds):
                    if stop.is_set():
                        return
                    if not self.connected_once:
                        self.connected_once = True
                        log.info("[%s] connected, receiving metadata", self.station.name)
                    delay = self.settings.reconnect_min_seconds
                    if meta is None:
                        self.mark_alive()
                    else:
                        self.handle_title(meta.title, meta.searchable_text)
                log.warning("[%s] stream closed by server", self.station.name)
            except Exception as exc:  # noqa: BLE001 - always reconnect
                log.warning("[%s] stream error: %s", self.station.name, exc)
            if stop.wait(delay):
                return
            delay = min(delay * 2, self.settings.reconnect_max_seconds)


class PlayHistory:
    """Append every song change to a CSV, which also helps tune the matcher."""

    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="") as f:
                csv.writer(f).writerow(["timestamp", "station", "title", "match"])

    def record(self, station: str, title: str, match: Match | None) -> None:
        row = [datetime.now().isoformat(timespec="seconds"), station, title,
               f"{match.kind}:{match.matched}" if match else ""]
        with self._lock, self.path.open("a", newline="") as f:
            csv.writer(f).writerow(row)


class Monitor:
    def __init__(self, stations: list[Station], matcher: Matcher, notifiers: list,
                 settings: Settings, source: MetadataSource = iter_metadata):
        self.settings = settings
        self.dispatcher = Dispatcher(notifiers)
        history = PlayHistory(settings.history_file) if settings.history_file else None
        self.watchers = [StationWatcher(s, matcher, self.dispatcher, settings, source, history)
                         for s in stations]
        self.stop_event = threading.Event()

    def run(self) -> None:
        threads = [threading.Thread(target=w.run, args=(self.stop_event,), name=w.station.name, daemon=True)
                   for w in self.watchers]
        for t in threads:
            t.start()
        if self.settings.notify_on_start:
            names = ", ".join(w.station.name for w in self.watchers)
            self.dispatcher.send(Alert(
                title="Backstreet Boys monitor started",
                message=f"Now watching: {names}. You'll get an alert the moment a BSB song comes on.",
                urgent=False, tags=["radio"],
            ))
        try:
            while not self.stop_event.wait(5):
                for w in self.watchers:
                    w.check_stale()
        finally:
            self.stop_event.set()
            for t in threads:
                t.join(timeout=2)
            self.dispatcher.shutdown()

    def stop(self) -> None:
        self.stop_event.set()
