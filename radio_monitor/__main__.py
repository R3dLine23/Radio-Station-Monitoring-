"""Command line entry point: ``python -m radio_monitor``."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from . import config as cfgmod
from .icy import iter_metadata
from .monitor import Monitor
from .notifiers import Alert, ConsoleNotifier, build_notifiers


def _probe(stations, seconds: float, matcher) -> int:
    """Connect to each station and print the raw metadata it sends."""
    ok = True
    for s in stations:
        print(f"\n== {s.name}  ({s.url})")
        deadline = time.monotonic() + seconds
        got_title = False
        try:
            for meta in iter_metadata(s.url, timeout=min(30, seconds)):
                if meta is not None:
                    got_title = True
                    m = matcher.match(meta.searchable_text)
                    print(f"  raw:    {meta.raw}")
                    print(f"  title:  {meta.title!r}   match: {m}")
                    break
                if time.monotonic() > deadline:
                    break
            if got_title:
                print("  OK: connected and received a song title")
            else:
                print(f"  connected, but no title within {seconds:.0f}s (the station may be in an ad break)")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  FAILED: {exc}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="radio_monitor",
                                description="Alert the moment a radio station plays the Backstreet Boys.")
    p.add_argument("-c", "--config", default="config.toml",
                   help="path to the TOML config (default: config.toml; built-in defaults if it doesn't exist)")
    p.add_argument("--probe", action="store_true",
                   help="connect to each station, print the current metadata, and exit")
    p.add_argument("--probe-seconds", type=float, default=45)
    p.add_argument("--test-notify", action="store_true",
                   help="send a test alert through every enabled notifier and exit")
    p.add_argument("--check", metavar="TEXT",
                   help='show whether TEXT (e.g. "Backstreet Boys - I Want It That Way") would trigger an alert')
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    path = Path(args.config)
    if not path.exists():
        if args.config != "config.toml":
            p.error(f"config file not found: {path}")
        logging.warning("no config.toml found, using built-in defaults (console alerts only)")
        cfg = {}
    else:
        cfg = cfgmod.load(path)

    stations = cfgmod.stations_from(cfg)
    matcher = cfgmod.matcher_from(cfg)

    if args.check is not None:
        print(matcher.match(args.check) or "no match")
        return 0
    if args.probe:
        return _probe(stations, args.probe_seconds, matcher)

    notifiers = build_notifiers(cfg.get("notify", {"console": {"enabled": True}}))
    if not notifiers:
        logging.warning("no notifiers enabled, falling back to console")
        notifiers = [ConsoleNotifier()]
    logging.info("notifiers: %s", ", ".join(n.name for n in notifiers))

    if args.test_notify:
        failed = False
        alert = Alert(
            title="TEST: Backstreet Boys on KISS 92.5!",
            message="This is a test alert from your radio monitor. If you can read this, alerts work.",
            urgent=True, tags=["test_tube"], click_url=stations[0].sms_link(),
        )
        for n in notifiers:
            try:
                n.send(alert)
                print(f"{n.name}: sent")
            except Exception as exc:  # noqa: BLE001
                failed = True
                print(f"{n.name}: FAILED: {exc}")
        return 1 if failed else 0

    monitor = Monitor(stations, matcher, notifiers, cfgmod.settings_from(cfg))
    signal.signal(signal.SIGTERM, lambda *_: monitor.stop())
    logging.info("watching %d station(s): %s", len(stations), ", ".join(s.name for s in stations))
    try:
        monitor.run()
    except KeyboardInterrupt:
        monitor.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
