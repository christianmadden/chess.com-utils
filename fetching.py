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


# ========================
# Country code lookup
# ========================

def _country_cache_path(username: str) -> str:
    safe_user = (username or "").strip().lower()
    return os.path.join(CACHE_ROOT, safe_user, "country_cache.json")


def _code_to_flag(code: str) -> str:
    """Convert a 2-letter ISO country code to a flag emoji."""
    if not code or len(code) != 2:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())


def get_country_lookup(our_username: str, opp_usernames: list, verbose: bool = False) -> dict:
    """Return {username.lower(): "🇺🇸 United States"} for all opponents, fetching uncached ones."""
    path = _country_cache_path(our_username)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    player_country = existing.get("player_country", {})
    country_name = existing.get("country_name", {})

    # Fetch player country codes for any uncached opponents
    uncached_players = [u for u in opp_usernames if u and u.lower() not in player_country]
    for opp in uncached_players:
        try:
            resp = requests.get(
                f"https://api.chess.com/pub/player/{opp}",
                headers={"User-Agent": "chess-sessions/1.0"}, timeout=10,
            )
            resp.raise_for_status()
            country_url = resp.json().get("country", "")
            code = country_url.rstrip("/").split("/")[-1] if country_url else None
            player_country[opp.lower()] = code
            dprint(verbose, f"Fetched country for {opp}: {code}")
        except Exception:
            player_country[opp.lower()] = None

    # Fetch full country names for any codes not yet in the name cache
    unknown_codes = {c for c in player_country.values() if c and c not in country_name}
    for code in unknown_codes:
        try:
            resp = requests.get(
                f"https://api.chess.com/pub/country/{code}",
                headers={"User-Agent": "chess-sessions/1.0"}, timeout=10,
            )
            resp.raise_for_status()
            country_name[code] = resp.json().get("name", code)
            dprint(verbose, f"Fetched name for {code}: {country_name[code]}")
        except Exception:
            country_name[code] = code

    # Persist updated cache
    if uncached_players or unknown_codes:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        try:
            existing["player_country"] = player_country
            existing["country_name"] = country_name
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    # Build display strings: "🇺🇸 United States"
    result = {}
    for opp in opp_usernames:
        if not opp:
            continue
        code = player_country.get(opp.lower())
        if code:
            result[opp.lower()] = f"{_code_to_flag(code)} {country_name.get(code, code)}"
        else:
            result[opp.lower()] = ""
    return result