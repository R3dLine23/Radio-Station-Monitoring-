"""Decide whether a "now playing" string is a Backstreet Boys song."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Strong signal: the artist name shows up in the metadata.
DEFAULT_ARTIST_ALIASES = [
    "backstreet",          # also catches "Everybody (Backstreet's Back)"
    "backstreetboys",
    "back street boys",
    "bsb",
]

# Weaker signal: a BSB song title with no artist name (some stations drop the
# artist or shorten it). A false alarm costs a glance at the radio, while a
# missed song costs the contest, so this list is fairly generous. Titles that
# other artists also own and these stations play ("Drowning", "The One",
# "Incomplete", "Chances", "Don't Go Breaking My Heart", "Last Christmas") are
# left out because they would set off false alarms all day.
DEFAULT_SONG_TITLES = [
    "I Want It That Way",
    "Everybody (Backstreet's Back)",
    "As Long As You Love Me",
    "Quit Playing Games",               # also matches "... (With My Heart)"
    "Larger Than Life",
    "Show Me the Meaning of Being Lonely",
    "Shape of My Heart",
    "I'll Never Break Your Heart",
    "All I Have to Give",
    "We've Got It Goin' On",
    "Get Down (You're the One for Me)",
    "Straight Through My Heart",
    "Inconsolable",
    "Helpless When She Smiles",
    "Don't Want You Back",
    "Get Another Boyfriend",
    "Show 'Em (What You're Made Of)",
    "In a World Like This",
    "Just Want You to Know",
]


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, and squeeze whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"['’`]", "", text)  # "don't" -> "dont", "backstreet's" -> "backstreets"
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


@dataclass
class Match:
    kind: str      # "artist" or "title"
    matched: str   # the alias or song title that matched

    @property
    def confident(self) -> bool:
        return self.kind == "artist"


class Matcher:
    def __init__(self, artist_aliases: list[str] | None = None, song_titles: list[str] | None = None):
        aliases = DEFAULT_ARTIST_ALIASES if artist_aliases is None else artist_aliases
        titles = DEFAULT_SONG_TITLES if song_titles is None else song_titles
        self._aliases = [(a, normalize(a)) for a in aliases if normalize(a)]
        # Titles match as whole-word phrases, so a short form like
        # "Quit Playing Games" also matches the full "... (With My Heart)".
        # Parentheticals are never stripped automatically, because
        # "Everybody (Backstreet's Back)" would become just "everybody".
        self._titles = [(t, normalize(t)) for t in titles if normalize(t)]

    @staticmethod
    def _contains(haystack: str, needle: str) -> bool:
        # Whole words only, so "bsb" doesn't match inside "absbx".
        return f" {needle} " in f" {haystack} "

    def match(self, text: str) -> Match | None:
        norm = normalize(text)
        if not norm:
            return None
        for original, alias in self._aliases:
            if self._contains(norm, alias) or self._contains(norm, alias + "s"):
                return Match("artist", original)
        for original, title in self._titles:
            if self._contains(norm, title):
                return Match("title", original)
        return None
