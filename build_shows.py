#!/usr/bin/env python3
"""Build a Ukrainian TV-series Kodi catalog using public Toloka metadata.

The source includes series and films. IMDb IDs and TMDb TV matching prevent
movie/TV confusion. We don't publish torrent URLs or credentials.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

from build_catalog import get_json, gather, imdb_id, dump_atomic, utcnow, QUALITY_RE, AUDIO_RE, TMDB_API

EXCLUDED_GENRES_UK = ('анімац', 'аніме', 'документ', 'реаліті', 'ток-шоу', 'ігрове шоу', 'новини', 'мульт')
EXCLUDED_TMDB_GENRES = {16, 99, 10764, 10767, 10763, 10762}  # animation, doc, reality, talk, news, kids
EXCLUDED_LANGUAGES = {'hi', 'kn', 'ml', 'ta', 'te'}
SEASON_RE = re.compile(r'(?i)(?:сезон|season)\s*[№#:]?\s*(\d{1,2})|\bS(\d{1,2})(?:E\d{1,3})?\b')
EP_RANGE_RE = re.compile(r'(?i)\b(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*(?:сері[йї]|епізод(?:и|ів)?|episodes?\b)')
EP_SINGLE_RE = re.compile(r'(?i)(?:\b(?:сері[яї]|episode|ep\.?)[\s#:№]*)(\d{1,3})\b')
S_E_RANGE_RE = re.compile(r'(?i)\bS(\d{1,2})E(\d{1,3})\s*[-–—]\s*E?(\d{1,3})\b')
S_E_SINGLE_RE = re.compile(r'(?i)\bS(\d{1,2})E(\d{1,3})\b')
CYR_AUDIO_RE = re.compile(r'(?i)(?<!\w)(?:\d*[xх])?укр(?!\w)|українськ\w*\s+(?:озвучення|дубляж)')


def season_episodes(title: str) -> dict[int, set[int] | None]:
    """Extract only identifiable seasons and explicitly advertised episode numbers.

    A None value means a season is present but the torrent title doesn't state
    which episodes are included; UI warns users about uncertainty.
    """
    matches = list(SEASON_RE.finditer(title))
    result = {}
    for m in matches:
        season = int(m.group(1) or m.group(2))
        if season == 0 or season > 60:
            continue
        # Bound the search for episode numbers to the immediate season notation.
        segment = title[m.end():m.end() + 95].split('/', 1)[0]
        eps = set()
        for rng in EP_RANGE_RE.finditer(segment):
            start, end = int(rng.group(1)), int(rng.group(2))
            if 1 <= start <= end <= 100:
                eps.update(range(start, end + 1))
        # Many Toloka titles say '(Сезон 1, 01-06)' without 'серій'.
        if not eps:
            simple_range = re.match(r'[,;\s(]*([0-9]{1,2})\s*[-–—]\s*([0-9]{1,2})(?!\d)', segment)
            if simple_range:
                start, end = int(simple_range.group(1)), int(simple_range.group(2))
                if 1 <= start <= end <= 100:
                    eps.update(range(start, end + 1))
        for one in EP_SINGLE_RE.finditer(segment):
            n = int(one.group(1))
            if 1 <= n <= 100:
                eps.add(n)
        result.setdefault(season, set() if eps else None)
        if eps:
            result[season] = (result[season] or set()) | eps
    for m in S_E_RANGE_RE.finditer(title):
        s, first, last = map(int, m.groups())
        if s and 1 <= first <= last <= 100:
            result[s] = (result.get(s) or set()) | set(range(first, last + 1))
    for m in S_E_SINGLE_RE.finditer(title):
        s, ep = map(int, m.groups())
        if s and 1 <= ep <= 100:
            result[s] = (result.get(s) or set()) | {ep}
    return result


def thread_data(row: dict) -> tuple[set[int], dict[int, set[int] | None]]:
    threads = row.get('threads') or []
    ids: set[int] = set()
    seasons: dict[int, set[int] | None] = {}
    if not isinstance(threads, list):
        return ids, seasons
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        title = str(thread.get('title') or '')
        # Ukrainian subtitles alone don't count as Ukrainian audio.
        audio_segment = re.split(r'(?i)\|\s*sub(?:titles?)?\b', title, maxsplit=1)[0]
        if not QUALITY_RE.search(title) or not (AUDIO_RE.search(audio_segment) or CYR_AUDIO_RE.search(audio_segment)):
            continue
        if re.search(r'(?i)\b(?:trailer|трейлер|анонс)\b', title):
            continue
        grouped = season_episodes(title)
        if not grouped:
            continue
        try:
            tid = int(thread['id'])
            if tid <= 0:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        ids.add(tid)
        for season, eps in grouped.items():
            if season not in seasons:
                seasons[season] = set() if eps else None
            if eps:
                seasons[season] = (seasons[season] or set()) | eps
    return ids, seasons


def source_candidate(row: dict, min_rating=7.5, min_votes=20000) -> bool:
    if not imdb_id(row.get('imdb_id')):
        return False
    if float(row.get('imdb_rating') or 0) < min_rating or int(row.get('imdb_votes') or 0) < min_votes:
        return False
    for genre in row.get('genres') or []:
        name = str(genre.get('name') or '' if isinstance(genre, dict) else genre).lower()
        if any(exclusion in name for exclusion in EXCLUDED_GENRES_UK):
            return False
    return bool(thread_data(row)[0])


def tv_id_by_imdb(imdb: str, token: str, demo=False) -> int | None:
    if demo:
        return {'tt0944947': 1399, 'tt4574334': 66732}.get(imdb)
    data = get_json(f'{TMDB_API}/find/{imdb}?' + urlencode({'external_source': 'imdb_id', 'language': 'uk-UA'}), token)
    tvs = data.get('tv_results') or []
    if len(tvs) != 1 or not isinstance(tvs[0], dict):
        return None
    tv = tvs[0]
    if tv.get('adult') or EXCLUDED_TMDB_GENRES.intersection(tv.get('genre_ids') or []):
        return None
    ident = tv.get('id')
    return ident if isinstance(ident, int) and ident > 0 else None


def get_tv_details(tmid: int, token: str, demo=False) -> dict:
    if demo:
        return {'name': f'Тестовий серіал {tmid}', 'first_air_date': '2025-01-01', 'overview': 'Тестовий опис українською.',
                'poster_path': '/test.jpg', 'backdrop_path': '/backdrop.jpg', 'original_language': 'en', 'genre_ids': []}
    return get_json(f'{TMDB_API}/tv/{tmid}?language=uk-UA&append_to_response=keywords', token)


def get_season_details(tmid: int, season: int, token: str, demo=False) -> dict:
    if demo:
        return {'name': f'Сезон {season}', 'episodes': [
            {'episode_number': i, 'name': f'Серія {i}', 'overview': 'Тестовий опис.', 'still_path': None}
            for i in range(1, 11)]}
    return get_json(f'{TMDB_API}/tv/{tmid}/season/{season}?language=uk-UA', token)


def prepare(rows: list[dict], state: dict, token: str, max_items: int, min_rating: float, min_votes: int,
            demo=False) -> tuple[list[dict], dict, dict]:
    now = utcnow()
    previous = state.get('shows', {})
    match_cache = dict(state.get('tmdb_cache', {}))
    details_cache = dict(state.get('details_cache', {}))
    grouped: dict[str, dict] = {}
    for pos, row in enumerate(rows):
        if not isinstance(row, dict) or not source_candidate(row, min_rating, min_votes):
            continue
        imdb = imdb_id(row.get('imdb_id'))
        threads, seasons = thread_data(row)
        if imdb not in grouped:
            grouped[imdb] = {'row': row, 'threads': set(), 'seasons': {}, 'pos': pos}
        grouped[imdb]['threads'].update(threads)
        for s, eps in seasons.items():
            if s not in grouped[imdb]['seasons']:
                grouped[imdb]['seasons'][s] = set() if eps else None
            if eps:
                grouped[imdb]['seasons'][s] = (grouped[imdb]['seasons'][s] or set()) | eps
    shows = dict(previous)
    skipped_tv = 0
    new = changed = 0
    for imdb, data in grouped.items():
        tmid = match_cache.get(imdb)
        if imdb not in match_cache:
            tmid = tv_id_by_imdb(imdb, token, demo)
            match_cache[imdb] = tmid
        if not tmid:
            skipped_tv += 1
            continue
        older = shows.get(imdb, {})
        added_threads = sorted(data['threads'] - set(older.get('thread_ids', [])))
        is_updated = bool(added_threads)
        old_seasons = older.get('seasons', {})
        combined = {int(s): (set(episodes) if episodes is not None else None)
                    for s, episodes in ((int(k), v) for k, v in old_seasons.items())}
        for s, eps in data['seasons'].items():
            if s not in combined:
                combined[s] = set() if eps else None
            if eps:
                combined[s] = (combined[s] or set()) | eps
        old_coverage = {str(k): v for k, v in old_seasons.items()}
        new_coverage = {str(k): sorted(v) if v else None for k, v in combined.items()}
        is_updated = is_updated or (bool(older) and old_coverage != new_coverage)
        if not older:
            new += 1
        elif is_updated:
            changed += 1
        shows[imdb] = {
            'tmdb_id': int(tmid), 'imdb_id': imdb, 'title': data['row'].get('title_uk') or data['row'].get('title'),
            'year': int(data['row'].get('year') or 0), 'thread_ids': sorted(set(older.get('thread_ids', [])) | data['threads']),
            'seasons': {str(k): new_coverage[str(k)] for k in sorted(combined)},
            'last_update': now if is_updated or not older else older['last_update'],
            'initial_order': older.get('initial_order', data['pos']),
        }
    if not shows:
        raise RuntimeError('No eligible series found; previous catalog retained')
    ordered = sorted(shows.values(), key=lambda it: (it['last_update'], -it['initial_order']), reverse=True)
    catalog = []
    dropped_details = 0
    for show in ordered:
        if len(catalog) >= max_items:
            break
        mid = show['tmdb_id']
        key = str(mid)
        cache = details_cache.setdefault(key, {})
        detail = cache.get('show')
        checked = cache.get('checked')
        age_days = 999
        if checked:
            try:
                age_days = (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(checked)).days
            except ValueError:
                pass
        if detail is None or age_days >= 14:
            try:
                detail = get_tv_details(mid, token, demo)
                cache['show'] = detail
                cache['checked'] = now
            except (RuntimeError, ValueError):
                if not detail:
                    dropped_details += 1
                    continue
        keywords = detail.get('keywords') or {}
        kw_names = [str(kw.get('name', '')).lower() for kw in keywords.get('results', []) if isinstance(kw, dict)]
        if any('superhero' in name or 'супергеро' in name for name in kw_names):
            continue
        if detail.get('original_language') in EXCLUDED_LANGUAGES:
            continue
        if detail.get('adult') or EXCLUDED_TMDB_GENRES.intersection(detail.get('genre_ids') or []):
            continue
        season_items = []
        for sno_text, coverage in sorted(show['seasons'].items(), key=lambda pair: int(pair[0]), reverse=True):
            sno = int(sno_text)
            details = cache.setdefault('seasons', {}).get(sno_text)
            if details is None or age_days >= 14:
                try:
                    details = get_season_details(mid, sno, token, demo)
                    cache['seasons'][sno_text] = details
                except (RuntimeError, ValueError):
                    if not details:
                        continue
            eps = []
            for episode in details.get('episodes') or []:
                no = episode.get('episode_number')
                if not isinstance(no, int) or no <= 0:
                    continue
                air_date = episode.get('air_date')
                if air_date:
                    try:
                        if dt.date.fromisoformat(air_date) > dt.datetime.now(dt.timezone.utc).date():
                            continue
                    except (ValueError, TypeError):
                        pass
                if coverage is not None and no not in coverage:
                    continue
                still = episode.get('still_path')
                eps.append({'episode': no, 'title': episode.get('name') or f'Серія {no}',
                            'description': episode.get('overview') or '',
                            'still_url': f'https://image.tmdb.org/t/p/w500{still}' if still else ''})
            if eps:
                season_items.append({'season': sno, 'title': details.get('name') or f'Сезон {sno}',
                                     'coverage_confirmed': coverage is not None,
                                     'episodes': sorted(eps, key=lambda e: e['episode'])})
        if not season_items:
            dropped_details += 1
            continue
        catalog.append({'id': mid, 'mediatype': 'tvshow', 'title': show['title'] or detail.get('name') or '',
                        'release_year': show['year'], 'imdb_id': show['imdb_id'],
                        'description': detail.get('overview') or '',
                        'poster_url': 'https://image.tmdb.org/t/p/w500' + (detail.get('poster_path') or '') if detail.get('poster_path') else '',
                        'fanart_url': 'https://image.tmdb.org/t/p/w1280' + (detail.get('backdrop_path') or '') if detail.get('backdrop_path') else '',
                        'seasons': season_items})
    if not catalog:
        raise RuntimeError('No series with valid seasons identified; previous catalog retained')
    # Save compact history; details cache retains translated show/season metadata across runs.
    new_state = {'shows': {x['imdb_id']: x for x in ordered[:max_items * 4]},
                 'tmdb_cache': match_cache, 'details_cache': details_cache}
    report = {'generated_utc': now, 'scanned_source_entries': len(rows),
              'plausible_unique_imdb': len(grouped), 'new': new, 'updated': changed,
              'not_tv': skipped_tv, 'catalog_series': len(catalog),
              'unresolved_details': dropped_details, 'source': 'https://tf.auct.eu/',
              'minimum_imdb_rating': min_rating, 'minimum_imdb_votes': min_votes,
              'note': 'An episode appearing in the catalog does not guarantee active sources or seeders.'}
    return catalog, new_state, report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--pages', type=int, default=100)
    ap.add_argument('--max-items', type=int, default=60)
    ap.add_argument('--min-rating', type=float, default=7.5)
    ap.add_argument('--min-votes', type=int, default=20000)
    ap.add_argument('--demo', action='store_true')
    ap.add_argument('--inspect', action='store_true')
    ap.add_argument('--state', default='data/shows_state.json', type=Path)
    ap.add_argument('--catalog', default='data/shows.json', type=Path)
    ap.add_argument('--report', default='data/shows_report.json', type=Path)
    args = ap.parse_args(argv)
    if not 1 <= args.pages <= 100 or not 1 <= args.max_items <= 300 or args.min_rating < 0 or args.min_votes < 0:
        ap.error('Invalid parameter range')
    token = os.environ.get('TMDB_READ_TOKEN', '').strip()
    if not token and not args.demo and not args.inspect:
        ap.error('TMDB_READ_TOKEN is required')
    if args.demo:
        rows = [
            {'imdb_id': 'tt0944947', 'title': 'Game of Thrones', 'title_uk': 'Гра престолів',
             'year': 2011, 'imdb_rating': 9.2, 'imdb_votes': 3000000,
             'genres': [{'name':'драма'}], 'threads': [
                {'id': 31415, 'title': 'Гра престолів (Сезон 3, 01-05 серій) WEB-DL 1080p Ukr/Eng'},
                {'id': 31416, 'title': 'Гра престолів (Сезон 3, 01-10 серій) 4K Ukr/Eng'}]},
            {'imdb_id': 'tt4574334', 'title': 'Stranger Things', 'title_uk':'Дивні дива',
             'year': 2016, 'imdb_rating': 8.6, 'imdb_votes': 1200000,
             'genres': [{'name':'драма'}], 'threads': [
                 {'id': 31417, 'title': 'Дивні дива (Сезон 2) WEB-DL 1080p Ukr/Eng'}]}
        ]
    else:
        rows = gather(args.pages)
    if args.inspect:
        qualified = [x for x in rows if source_candidate(x, args.min_rating, args.min_votes)]
        print('Source candidates (before TV validation):', len(qualified))
        for x in qualified[:15]:
            print(x.get('title_uk') or x.get('title'))
        return 0
    try:
        state = json.loads(args.state.read_text(encoding='utf-8')) if args.state.exists() else {}
        catalog, new_state, report = prepare(rows, state, token, args.max_items,
                                             args.min_rating, args.min_votes, demo=args.demo)
        # Reject publishing empty or broken output.
        dump_atomic(args.catalog, catalog)
        dump_atomic(args.state, new_state)
        dump_atomic(args.report, report)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print('ERROR:', str(exc), file=sys.stderr)
        return 1
    print(f'Series ready: {len(catalog)}, candidates: {report["plausible_unique_imdb"]}, added: {report["new"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
