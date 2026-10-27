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
        pass
    for genre in row.get("genres") or []:
        name = str(genre.get("name", "") if isinstance(genre, dict) else genre).lower()
        if any(block in name for block in EXCLUDED_GENRES_UK):
            return False
    return True


def tmdb_movie_by_imdb(imdb: str, token: str, demo: bool = False) -> dict | None:
    if demo:
        # Explicit demo fixtures, never mistaken for a live API result.
        demos = {"tt0000001": {"id": 101, "genre_ids": [18], "adult": False},
                 "tt0000002": {"id": 102, "genre_ids": [28], "adult": False}}
        return demos.get(imdb)
    url = f"{TMDB_API}/find/{imdb}?" + urlencode({"external_source": "imdb_id", "language": "uk-UA"})
    response = get_json(url, token=token)
    movies = response.get("movie_results") or []
    # No movie result means it may be a TV series or an unknown IMDb title.
    if not isinstance(movies, list) or len(movies) != 1 or not isinstance(movies[0], dict):
        return None
    movie = movies[0]
    genre_ids = set(movie.get("genre_ids") or [])
    if movie.get("adult") or genre_ids.intersection(TMDB_EXCLUDED_GENRES):
        return None
    if not isinstance(movie.get("id"), int) or movie["id"] <= 0:
        return None
    return movie


def read_state(path: Path) -> dict:
    if not path.exists():
        return {"movies": {}, "tmdb_cache": {}, "details_cache": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("State file is not an object")
    if not isinstance(payload.get("movies", {}), dict):
        raise ValueError("Invalid movies in state")
    return {"movies": payload.get("movies", {}), "tmdb_cache": payload.get("tmdb_cache", {}), "details_cache": payload.get("details_cache", {})}


def dump_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def gather(pages: int, demo: bool = False) -> list[dict]:
    if demo:
        path = Path(__file__).parent / "fixtures" / "sample_source.json"
        return json.loads(path.read_text(encoding="utf-8"))["data"]
    rows = []
    for page in range(1, pages + 1):
        url = SOURCE_URL + "?" + urlencode({"page": page, "format": "json"})
        payload = get_json(url)
        page_rows = payload.get("data")
        if not isinstance(page_rows, list):
            raise ValueError("Source schema changed: missing 'data' array; keeping last catalog")
        if not page_rows:
            break
        rows.extend(page_rows)
        print(f"Page {page}: {len(page_rows)} source titles")
    if not rows:
        raise ValueError("Source returned no results; keeping last catalog")
    return rows


def make_catalog(records: list[dict]) -> list[dict]:
    out = []
    for rank, item in enumerate(records, 1):
        row = {
            "id": item["tmdb_id"],
            "rank": rank,
            "adult": 0,
            "title": item["title_uk"] or item["title"],
            "imdb_id": item["imdb_id"],
            "mediatype": "movie",
            "release_year": item["year"],
        }
        details = item.get("tmdb_details") or {}
        if details.get("poster_path"):
            row["poster_url"] = "https://image.tmdb.org/t/p/w500" + details["poster_path"]
        if details.get("backdrop_path"):
            row["fanart_url"] = "https://image.tmdb.org/t/p/w1280" + details["backdrop_path"]
        if details.get("overview"):
            row["description"] = details["overview"]
        out.append(row)
    return out


def process(rows: list[dict], state: dict, token: str, max_items: int, demo: bool = False) -> tuple[list[dict], dict, dict]:
    now = utcnow()
    movies = dict(state.get("movies", {}))
    cache = dict(state.get("tmdb_cache", {}))
    details_cache = dict(state.get("details_cache", {}))
    rejected = 0
    added = 0
    updated = 0
    all_rows = {}
    # Group same IMDb from multiple source pages, while retaining initial source order.
    for pos, row in enumerate(rows):
        if not isinstance(row, dict) or not source_eligible(row):
            rejected += 1
            continue
        imdb = imdb_id(row.get("imdb_id"))
        thread_set = eligible_release_threads(row)
        if imdb not in all_rows:
            all_rows[imdb] = [row, set(), pos]
        all_rows[imdb][1].update(thread_set)

    for imdb, (row, new_threads, pos) in all_rows.items():
        prev = movies.get(imdb, {})
        old_threads = set(prev.get("thread_ids", []))
        if prev.get("tmdb_id"):
            tmdb_id = int(prev["tmdb_id"])
        elif imdb in cache:
            tmdb_id = int(cache[imdb]) if cache[imdb] else None
        else:
            movie = tmdb_movie_by_imdb(imdb, token, demo)
            tmdb_id = movie["id"] if movie else None
            cache[imdb] = tmdb_id
        if not tmdb_id:
            rejected += 1
            continue
        seen = old_threads | new_threads
        changed = bool(new_threads - old_threads)
        if prev and not changed:
            last_update = prev.get("last_update", now)
            initial_order = prev.get("initial_order", pos)
        else:
            last_update = now
            initial_order = pos if not prev else prev.get("initial_order", pos)
            if prev:
                updated += 1
            else:
                added += 1
        movies[imdb] = {
            "imdb_id": imdb,
            "tmdb_id": tmdb_id,
            "title": str(row.get("title") or ""),
            "title_uk": str(row.get("title_uk") or ""),
            "year": int(row.get("year") or 0),
            "thread_ids": sorted(seen),
            "first_seen": prev.get("first_seen", now),
            "last_update": last_update,
            "initial_order": initial_order,
        }
    if not movies:
        raise ValueError("No eligible films identified; keeping previous files")

    # Most recently detected/newly updated content first, using source order for first run.
    sorted_movies = sorted(movies.values(), key=lambda m: (m["last_update"], -m["initial_order"]), reverse=True)
    winners = sorted_movies[:max_items]
    # Fetch Ukrainian artwork and plot for the Kodi-native catalog view.
    # Keep old metadata on temporary TMDb errors rather than removing posters.
    for record in winners:
        key = str(record["tmdb_id"])
        details = details_cache.get(key, {})
        if not details.get("checked") or (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(details["checked"])).days >= 14:
            if demo:
                details = {"poster_path": None, "backdrop_path": None, "overview": "Test catalog entry", "checked": now}
            else:
                try:
                    details = get_json(f"{TMDB_API}/movie/{key}?language=uk-UA", token=token)
                    details = {"poster_path": details.get("poster_path"), "backdrop_path": details.get("backdrop_path"),
                               "overview": details.get("overview") or "", "checked": now}
                except (RuntimeError, ValueError):
                    print(f"WARNING: Artwork lookup failed for TMDb {key}; keeping existing metadata")
            if details:
                details_cache[key] = details
        record["tmdb_details"] = details
    # Limit state to a modest rolling history (3 catalog windows).
    preserved = sorted_movies[:max_items * 3]
    new_state = {"movies": {m["imdb_id"]: {k:v for k,v in m.items() if k != "tmdb_details"} for m in preserved},
                 "tmdb_cache": cache, "details_cache": details_cache}
    report = {
        "generated_utc": now,
        "scanned_source_entries": len(rows),
        "unique_eligible_source_imdb": len(all_rows),
        "added": added,
        "updated": updated,
        "skipped": rejected,
        "catalog_movies": len(winners),
        "source": SOURCE_URL,
        "note": "Metadata only; entries do not confirm current tracker availability.",
    }
    return make_catalog(winners), new_state, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Create weekly Ukrainian Toloka-themed catalog for TMDb Helper")
    parser.add_argument("--pages", type=int, default=18, help="How many 15-title source pages to inspect")
    parser.add_argument("--max-items", type=int, default=60)
    parser.add_argument("--state", type=Path, default=Path("data/state.json"))
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog.json"))
    parser.add_argument("--report", type=Path, default=Path("data/report.json"))
    parser.add_argument("--demo", action="store_true", help="Offline sample (does not request internet)")
    parser.add_argument("--inspect", action="store_true", help="Read source and show filtered titles; no TMDb key, no file writes")
    args = parser.parse_args()
    if not 1 <= args.pages <= 100 or not 1 <= args.max_items <= 500:
        parser.error("pages must be 1..100 and max-items must be 1..500")
    token = os.environ.get("TMDB_READ_TOKEN", "").strip()
    if not token and not args.demo and not args.inspect:
        parser.error("TMDB_READ_TOKEN must be set (TMDb API Read Access Token)")
    if args.inspect:
        try:
            rows = gather(args.pages, args.demo)
            unique = {}
            for row in rows:
                if isinstance(row, dict) and source_eligible(row):
                    unique[imdb_id(row['imdb_id'])] = row
            print(f"SOURCE CHECK: {len(rows)} positions, {len(unique)} plausible unique HD films with Ukrainian audio")
            print("NOTE: TV vs movie still needs validation using TMDb at live build.")
            for n, (imdb, row) in enumerate(unique.items()):
                if n >= 15:
                    break
                print(f"  {imdb}: {row.get('title_uk') or row.get('title')} ({row.get('year', '?')})")
            return 0
        except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    try:
        state = read_state(args.state)
        rows = gather(args.pages, args.demo)
        catalog, next_state, report = process(rows, state, token, args.max_items, args.demo)
        # Never write an empty catalog. Network or schema failures leave old data intact.
        if not catalog:
            raise ValueError("Empty catalog; not replacing last successful data")
        dump_atomic(args.catalog, catalog)
        dump_atomic(args.state, next_state)
        dump_atomic(args.report, report)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Catalog ready: {len(catalog)} movies, +{report['added']} new, {report['updated']} updated")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
