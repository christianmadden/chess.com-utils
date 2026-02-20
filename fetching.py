import datetime as dt
import json
import os
import shutil

import requests

from config import CACHE_ROOT, LOCAL_TZ
from parsing import dprint, parse_pgn_times, get_game_result


# ========================
# Cache helpers
# ========================

def _cache_path(username: str, year: int, month: int) -> str:
    safe_user = (username or "").strip().lower()
    user_dir = os.path.join(CACHE_ROOT, safe_user)
    return os.path.join(user_dir, f"{year}-{month:02d}.json")


def _read_cached_month(username: str, year: int, month: int, verbose: bool = False):
    path = _cache_path(username, year, month)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            dprint(verbose, f"Cache hit: {path}")
            return json.load(f)
    except Exception as e:
        dprint(verbose, f"Cache read failed ({path}): {e}. Will refetch.")
        return None


def _write_cached_month(username, year, month, payload, verbose=False):
    path = _cache_path(username, year, month)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        dprint(verbose, f"Cached: {path}")
    except Exception as e:
        dprint(verbose, f"Cache write failed ({path}): {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def clear_user_cache(username: str):
    safe_user = username.strip().lower()
    user_cache_dir = os.path.join(CACHE_ROOT, safe_user)
    if os.path.isdir(user_cache_dir):
        shutil.rmtree(user_cache_dir)
        print(f"Cache cleared: {user_cache_dir}")
    else:
        print("No cache to clear.")


# ========================
# Fetching
# ========================

def fetch_month_games(username, year, month, verbose=False):
    today = dt.datetime.now(LOCAL_TZ).date()
    is_current_month = (year == today.year and month == today.month)

    if not is_current_month:
        cached = _read_cached_month(username, year, month, verbose)
        if isinstance(cached, dict):
            return cached.get("games", [])

    url = f"https://api.chess.com/pub/player/{username}/games/{year}/{month:02d}"
    dprint(verbose, f"Fetching {url}")

    resp = requests.get(url, headers={"User-Agent": "chess-sessions/1.0"}, timeout=10)
    resp.raise_for_status()

    payload = resp.json()
    if isinstance(payload, dict):
        if not is_current_month:
            _write_cached_month(username, year, month, payload, verbose)
        return payload.get("games", [])

    return []


def month_iter(start_date: dt.date, end_date: dt.date):
    y, m = start_date.year, start_date.month
    end_y, end_m = end_date.year, end_date.month
    while (y, m) <= (end_y, end_m):
        yield y, m
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1


def month_iter_backwards(end_date: dt.date, max_months: int = 36):
    y, m = end_date.year, end_date.month
    for _ in range(max_months):
        yield y, m
        if m == 1:
            y -= 1
            m = 12
        else:
            m -= 1


def is_skippable(game: dict) -> bool:
    """Return True for coach/training/odd entries we want to ignore."""
    white = game.get("white", {}).get("username", "").lower()
    black = game.get("black", {}).get("username", "").lower()
    pgn = game.get("pgn", "")
    if 'Event "Play vs Coach"' in pgn:
        return True
    if white == "chess.com" or black == "chess.com":
        return True
    return False


def fetch_most_recent_games(username, n: int, verbose: bool = False):
    if n <= 0:
        return []

    now = dt.datetime.now(LOCAL_TZ)
    parsed = []
    for y, m in month_iter_backwards(now.date()):
        games = fetch_month_games(username, y, m, verbose)
        for g in games:
            if is_skippable(g):
                continue
            start, end = parse_pgn_times(g.get("pgn", ""), verbose)
            if not start or not end:
                continue
            res, side = get_game_result(g, username)
            parsed.append((start, end, res, side, g))
        if len(parsed) >= n:
            break

    parsed.sort(key=lambda x: x[0])
    return parsed[-n:]


def fetch_games_for_range(username, start_date, end_date, verbose=False):
    all_games = []
    for y, m in month_iter(start_date, end_date):
        all_games.extend(fetch_month_games(username, y, m, verbose))
    return all_games