#!/usr/bin/env python3
"""Weekly Toloka catalog generator: public catalog metadata -> TMDb -> TMDb Helper JSON.

No torrent files, links, cookies or site passwords are collected or published.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SOURCE_URL = "https://tf.auct.eu/"
TMDB_API = "https://api.themoviedb.org/3"
EXCLUDED_GENRES_UK = (
    "анімац", "аніме", "документ", "коротк", "реаліті", "ток-шоу", "ігрове шоу",
    "новини", "телефільм", "тв-фільм",
)
TMDB_EXCLUDED_GENRES = {16, 99, 10770}  # animation, documentary, TV movie
QUALITY_RE = re.compile(r"(?i)(?<!\w)(?:1080p|2160p|4k|bdremux)(?!\w)")
AUDIO_RE = re.compile(r"(?i)(?:\b\d*xukr\b|\bukr\b|українське\s+озвучення)")
IMDB_RE = re.compile(r"^tt\d{7,10}$")


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def get_json(url: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "TolokaCinemaUA/0.1 (weekly metadata catalog)"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=25) as response:
                payload = json.load(response)
                if not isinstance(payload, dict):
                    raise ValueError(f"Unexpected JSON document from {url.split('?')[0]}")
                return payload
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt == 2 or (isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504)):
                raise RuntimeError(f"Request failed: {url.split('?')[0]} ({type(exc).__name__})") from exc
            time.sleep(1 + attempt * 2)
    raise RuntimeError("Unreachable")


def imdb_id(value) -> str | None:
    s = str(value or "").strip().lower()
    if s.isdigit():
        s = f"tt{int(s):07d}"
    return s if IMDB_RE.fullmatch(s) else None


def eligible_release_threads(row: dict) -> set[int]:
    threads = row.get("threads") or []
    if not isinstance(threads, list):
        return set()
    found = set()
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        title = str(thread.get("title") or "")
        # Subtitle-only 'Sub Ukr' must not be treated as an Ukrainian audio track.
        audio_part = re.split(r"(?i)\|\s*sub(?:titles?)?\b", title, maxsplit=1)[0]
        if not QUALITY_RE.search(title) or not AUDIO_RE.search(audio_part):
            continue
        # Exclude unfinished/announced material; actual release must have a quality tag.
        if re.search(r"(?i)\b(анонс|trailer|трейлер)\b", title):
            continue
        try:
            number = int(thread["id"])
            if number > 0:
                found.add(number)
        except (ValueError, TypeError, KeyError):
            continue
    return found


def source_eligible(row: dict) -> bool:
    if not imdb_id(row.get("imdb_id")):
        return False
    if not eligible_release_threads(row):
        return False
    if not row.get("title") and not row.get("title_uk"):
        return False
    try:
        duration = int(row.get("runtime") or 0)
        if 0 < duration < 60:
            return False
    except (ValueError, TypeError):
