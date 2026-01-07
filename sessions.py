#!/usr/bin/env python3
"""chess_sessions.py

Fetch Chess.com games and group them into sessions.
Session = consecutive games with gaps <= --gap minutes.

Cache: monthly API responses are cached under ./json/<username>/ (override with CHESS_SESSIONS_CACHE_DIR).
Note: the current month is ALWAYS fetched live (never cached).
"""

import argparse
import datetime as dt
import os
import re
from collections import defaultdict
from zoneinfo import ZoneInfo

import requests
from rich.console import Console
from rich.style import Style
from rich.table import Table
from rich.text import Text

# ========================
# Config
# ========================

# Default username from .env
DEFAULT_USER = os.getenv("CHESS_USERNAME")

LOCAL_TZ = ZoneInfo("America/New_York")

# Cache directory for monthly API responses (safe to delete manually)
CACHE_ROOT = os.getenv(
    "CHESS_SESSIONS_CACHE_DIR", os.path.join(os.path.dirname(__file__), "json")
)

PGN_DATE_RE = re.compile(r'\[UTCDate "([\d\.]+)"\]')
PGN_START_RE = re.compile(r'\[UTCTime "([\d:]+)"\]')
PGN_END_RE = re.compile(r'\[EndTime "([\d:]+)"\]')

# ========================
# Helpers
# ========================


def dprint(enabled: bool, msg: str) -> None:
    if enabled:
        now = dt.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] {msg}")


def parse_pgn_times(pgn: str, verbose: bool = False):
    """Return (start_utc, end_utc) datetimes parsed from PGN tags, or (None, None)."""
    m_date = PGN_DATE_RE.search(pgn)
    m_start = PGN_START_RE.search(pgn)
    m_end = PGN_END_RE.search(pgn)

    if not (m_date and m_start and m_end):
        dprint(verbose, "PGN missing time info")
        return None, None

    start = dt.datetime.strptime(
        f"{m_date.group(1)} {m_start.group(1)}", "%Y.%m.%d %H:%M:%S"
    ).replace(tzinfo=dt.timezone.utc)

    end = dt.datetime.strptime(
        f"{m_date.group(1)} {m_end.group(1)}", "%Y.%m.%d %H:%M:%S"
    ).replace(tzinfo=dt.timezone.utc)

    return start, end


def get_game_result(game: dict, username: str):
    """Return (result_letter, side) where side is 'W'/'B'."""
    username = (username or "").lower()
    white = game.get("white", {}).get("username", "").lower()
    black = game.get("black", {}).get("username", "").lower()

    white_result = game.get("white", {}).get("result", "")
    black_result = game.get("black", {}).get("result", "")

    if username == white:
        side = "W"
        res = white_result
    elif username == black:
        side = "B"
        res = black_result
    else:
        return "D", None

    if res == "win":
        return "W", side
    if res in {"checkmated", "resigned", "timeout", "abandoned", "lose"}:
        return "L", side
    if res in {"agreed", "repetition", "stalemate", "insufficient"}:
        return "D", side

    return "D", side


def colorize_with_side(results):
    """Render a compact W/L/D string with background indicating side (white/black)."""
    text = Text()
    for res, side in results:
        # Background indicates side: dark for black, light for white
        bg = "grey23" if side == "B" else "grey85"
        if res == "W":
            style = Style(color="bright_green", bgcolor=bg)
        elif res == "L":
            style = Style(color="bright_red", bgcolor=bg)
        else:
            style = Style(color="grey70", bgcolor=bg)

        text.append(res, style=style)
    return text


# ========================
# Cache helpers
# ========================


def _cache_path(username: str, year: int, month: int) -> str:
    """Return the cache file path for a user's (year, month) game archive."""
    safe_user = (username or "").strip().lower()
    user_dir = os.path.join(CACHE_ROOT, safe_user)
    return os.path.join(user_dir, f"{year}-{month:02d}.json")


def _read_cached_month(username: str, year: int, month: int, verbose: bool = False):
    path = _cache_path(username, year, month)
    if not os.path.exists(path):
        return None

    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            dprint(verbose, f"Cache hit: {path}")
            return json.load(f)
    except Exception as e:
        # Corrupt cache? Ignore it and refetch.
        dprint(verbose, f"Cache read failed ({path}): {e}. Will refetch.")
        return None


def _write_cached_month(
    username: str, year: int, month: int, payload: dict, verbose: bool = False
) -> None:
    path = _cache_path(username, year, month)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    tmp = f"{path}.tmp"
    try:
        import json

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        dprint(verbose, f"Cached: {path}")
    except Exception as e:
        # Best-effort: caching should never break the script
        dprint(verbose, f"Cache write failed ({path}): {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


# ========================
# Fetching
# ========================


def fetch_month_games(username, year, month, verbose=False):
    """Fetch all games for a given (year, month) from Chess.com.

    Past months are cached on disk; the current month is always fetched live.
    """
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
    """Yield (year, month) tuples covering all months from start_date to end_date inclusive."""
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
    """Yield (year, month) tuples going backwards from end_date's month for up to max_months."""
    y, m = end_date.year, end_date.month
    for _ in range(max_months):
        yield y, m
        if m == 1:
            y -= 1
            m = 12
        else:
            m -= 1


def fetch_most_recent_games(username, n: int, verbose: bool = False):
    """Fetch and parse the most recent N games across months (no day filtering).

    Uses the monthly archives endpoint, walking backwards month-by-month until enough
    valid games are collected.
    """
    if n <= 0:
        return []

    now = dt.datetime.now(LOCAL_TZ)

    parsed = []
    for y, m in month_iter_backwards(now.date()):
        games = fetch_month_games(username, y, m, verbose)
        for g in games:
            white = g.get("white", {}).get("username", "").lower()
            black = g.get("black", {}).get("username", "").lower()
            pgn = g.get("pgn", "")

            # Skip coach/training/odd entries
            if 'Event "Play vs Coach"' in pgn:
                continue
            if white == "chess.com" or black == "chess.com":
                continue

            start, end = parse_pgn_times(pgn, verbose)
            if not start or not end:
                continue

            res, side = get_game_result(g, username)
            parsed.append((start, end, res, side))

        # If we have at least N parsed games, we can stop early.
        if len(parsed) >= n:
            break

    parsed.sort(key=lambda x: x[0])
    return parsed[-n:]


def fetch_games_for_range(username, start_date: dt.date, end_date: dt.date, verbose=False):
    """Fetch games across all months that overlap [start_date, end_date]."""
    all_games = []
    for y, m in month_iter(start_date, end_date):
        all_games.extend(fetch_month_games(username, y, m, verbose))
    return all_games


# ========================
# Main
# ========================


def main():
    parser = argparse.ArgumentParser()
    def parse_days(value):
        if isinstance(value, str) and value.lower() == "all":
            return "all"
        try:
            v = int(value)
            if v <= 0:
                raise ValueError
            return v
        except ValueError:
            raise argparse.ArgumentTypeError("--days must be a positive integer or 'all'")
    parser.add_argument(
        "--days",
        type=parse_days,
        default=1,
        help="Number of days to include, or 'all' for all available history",
    )
    parser.add_argument(
        "--user",
        type=str,
        default=DEFAULT_USER,
        help="Chess.com username (or set CHESS_USERNAME in environment)",
    )
    parser.add_argument("--gap", type=int, default=60)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--day-cutoff-hour", type=int, default=5)
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Delete cached monthly data for this user before running",
    )
    parser.add_argument(
        "--games",
        type=int,
        default=None,
        help="Limit output to the most recent N games (overrides --days filtering)",
    )
    args = parser.parse_args()

    console = Console()

    if not args.user:
        print("Error: No username provided.")
        print("Set CHESS_USERNAME in your environment or pass --user <username>.")
        raise SystemExit(1)

    # Optionally clear cached data for this user
    if args.no_cache:
        safe_user = args.user.strip().lower()
        user_cache_dir = os.path.join(CACHE_ROOT, safe_user)
        if os.path.isdir(user_cache_dir):
            import shutil

            shutil.rmtree(user_cache_dir)
            print(f"Cache cleared: {user_cache_dir}")
        else:
            print("No cache to clear.")

    now = dt.datetime.now(LOCAL_TZ)
    day_shift = dt.timedelta(hours=args.day_cutoff_hour)
    today = (now - day_shift).date()

    # If --days is omitted and --games is provided, fetch last N games regardless of days.
    if args.days is None and args.games is not None and args.games > 0:
        parsed = fetch_most_recent_games(args.user, args.games, args.verbose)
    else:
        if args.days == "all":
            cutoff = None
        else:
            days = args.days
            cutoff = today - dt.timedelta(days=days - 1)

        if cutoff is None:
            # Fetch all available months backwards (uses cache after first run)
            games = []
            for y, m in month_iter_backwards(today):
                games.extend(fetch_month_games(args.user, y, m, args.verbose))
        else:
            games = fetch_games_for_range(args.user, cutoff, today, args.verbose)

        parsed = []
        for g in games:
            white = g.get("white", {}).get("username", "").lower()
            black = g.get("black", {}).get("username", "").lower()
            pgn = g.get("pgn", "")

            # Skip coach/training/odd entries
            if 'Event "Play vs Coach"' in pgn:
                continue
            if white == "chess.com" or black == "chess.com":
                continue

            start, end = parse_pgn_times(pgn, args.verbose)
            if not start or not end:
                continue

            local_start = start.astimezone(LOCAL_TZ)
            game_day = (local_start - day_shift).date()

            if cutoff is None or (cutoff <= game_day <= today):
                res, side = get_game_result(g, args.user)
                parsed.append((start, end, res, side))

        parsed.sort(key=lambda x: x[0])

        # Optionally limit to the most recent N games within the day window
        if args.games is not None and args.games > 0:
            parsed = parsed[-args.games:]

    if not parsed:
        print("No games found.")
        return

    # Group into sessions
    sessions = []
    gap = dt.timedelta(minutes=args.gap)
    cur = []

    for game in parsed:
        if not cur:
            cur = [game]
            continue
        if game[0] - cur[-1][1] <= gap:
            cur.append(game)
        else:
            sessions.append(cur)
            cur = [game]
    if cur:
        sessions.append(cur)

    # Group by day
    sessions_by_day = defaultdict(list)
    for sess in sessions:
        day = (sess[0][0].astimezone(LOCAL_TZ) - day_shift).date()
        sessions_by_day[day].append(sess)

    flat = [
        (g[2], g[3])
        for day in sorted(sessions_by_day)
        for s in sessions_by_day[day]
        for g in s
    ]
    wins = sum(1 for r, _ in flat if r == "W")
    losses = sum(1 for r, _ in flat if r == "L")
    draws = sum(1 for r, _ in flat if r == "D")
    total = wins + losses + draws
    win_pct = round(wins / total * 100) if total else 0

    console.print()
    console.print(
        colorize_with_side(flat),
        f" -- {wins} wins, {losses} losses, {draws} draws -- {win_pct}% win rate",
    )
    console.print()

    for day in sorted(sessions_by_day):
        console.rule(day.strftime("%A, %B %d"))
        for sess in sessions_by_day[day]:
            start = sess[0][0].astimezone(LOCAL_TZ)
            end = sess[-1][1].astimezone(LOCAL_TZ)
            res = [(g[2], g[3]) for g in sess]

            time_str = (
                f"{start.strftime('%-I:%M%p').lower()} to {end.strftime('%-I:%M%p').lower()}"
            )
            console.print(
                f"[bold]- {time_str:<20} {len(sess)} games[/bold] ",
                colorize_with_side(res),
            )
        print()


if __name__ == "__main__":
    main()