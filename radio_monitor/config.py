"""Load config.toml. Any string value may reference environment variables as ${NAME}."""

from __future__ import annotations

import os
import tomllib
from dataclasses import fields
from pathlib import Path

from .matcher import DEFAULT_SONG_TITLES, Matcher
from .monitor import Settings, Station

# Used when the config file has no [[stations]] entries.
DEFAULT_STATIONS = [
    Station("KISS 92.5", "https://rogers-hls.leanstream.co/rogers/tor925.stream/icy"),
    Station("CHFI 98.1", "https://rogers-hls.leanstream.co/rogers/tor981.stream/icy"),
]


def _expand(value):
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load(path: str | Path | None) -> dict:
    if path is None:
        return {}
    with open(path, "rb") as f:
        return _expand(tomllib.load(f))


def stations_from(cfg: dict) -> list[Station]:
    entries = cfg.get("stations")
    if not entries:
        return list(DEFAULT_STATIONS)
    stations = []
    for i, e in enumerate(entries):
        if not e.get("name") or not e.get("url"):
            raise ValueError(f"stations[{i}] needs both name and url")
        if e.get("enabled", True):
            stations.append(Station(
                name=e["name"], url=e["url"],
                text_number=str(e.get("text_number", "")), text_message=e.get("text_message", ""),
            ))
    if not stations:
        raise ValueError("every station is disabled")
    return stations


def settings_from(cfg: dict) -> Settings:
    raw = cfg.get("settings", {})
    known = {f.name for f in fields(Settings)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown [settings] keys: {', '.join(sorted(unknown))}")
    return Settings(**raw)


def matcher_from(cfg: dict) -> Matcher:
    m = cfg.get("match", {})
    titles = list(m.get("song_titles", DEFAULT_SONG_TITLES)) + list(m.get("extra_song_titles", []))
    return Matcher(artist_aliases=m.get("artist_aliases"), song_titles=titles)
