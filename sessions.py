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

PGN_TAG_RE = re.compile(r'\[([^\s]+) "([^\"]*)"\]')

# Common tags we care about (may be absent depending on game type / archive)
# Examples:
#   [WhiteElo "406"] [BlackElo "387"]
#   [WhiteRatingDiff "+36"] [BlackRatingDiff "-36"]
#   [WhiteAccuracy "65.2"] [BlackAccuracy "58.1"]

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


def parse_pgn_tags(pgn: str) -> dict:
    """Parse PGN tag-pairs into a dict."""
    tags = {}
    if not pgn:
        return tags
    for k, v in PGN_TAG_RE.findall(pgn):
        tags[k] = v
    return tags


def _to_int(s: str):
    try:
        return int(str(s).strip())
    except Exception:
        return None


def _to_float(s: str):
    try:
        return float(str(s).strip())
    except Exception:
        return None


def _parse_rating_diff(s: str):
    """Parse a rating diff like '+8' or '-12' or '0' -> int or None."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    # Some PGNs include leading +
    try:
        return int(s)
    except Exception:
        # try to clean weird formatting
        s2 = s.replace("+", "")
        try:
            return int(s2)
        except Exception:
            return None


def extract_game_details(game: dict, username: str):
    """Return a dict of per-game details for display.

    Best-effort across different Chess.com payload shapes.
    """
    username_l = (username or "").lower()
    white_u = game.get("white", {}).get("username", "")
    black_u = game.get("black", {}).get("username", "")
    white_l = white_u.lower()
    black_l = black_u.lower()

    me_side = None
    opp_user = None
    opp_side = None
    if username_l == white_l:
        me_side = "W"
        opp_side = "B"
        opp_user = black_u
    elif username_l == black_l:
        me_side = "B"
        opp_side = "W"
        opp_user = white_u

    # Ratings (post-game) are often present in the JSON payload
    white_rating_after = _to_int(game.get("white", {}).get("rating"))
    black_rating_after = _to_int(game.get("black", {}).get("rating"))

    # PGN tags often include richer fields like rating diffs + accuracies
    pgn = game.get("pgn", "")
    tags = parse_pgn_tags(pgn)

    # Prefer PGN Elo tags if present
    white_elo_tag = _to_int(tags.get("WhiteElo"))
    black_elo_tag = _to_int(tags.get("BlackElo"))
    if white_elo_tag is not None:
        white_rating_after = white_elo_tag
    if black_elo_tag is not None:
        black_rating_after = black_elo_tag

    white_diff = _parse_rating_diff(tags.get("WhiteRatingDiff"))
    black_diff = _parse_rating_diff(tags.get("BlackRatingDiff"))

    # Accuracies (not always present)
    white_acc = _to_float(tags.get("WhiteAccuracy"))
    black_acc = _to_float(tags.get("BlackAccuracy"))

    # Some payloads include accuracies as a top-level object
    acc_obj = game.get("accuracies")
    if isinstance(acc_obj, dict):
        if white_acc is None:
            white_acc = _to_float(acc_obj.get("white"))
        if black_acc is None:
            black_acc = _to_float(acc_obj.get("black"))

    # Opponent rating after-game
    opp_rating_after = None
    if opp_side == "W":
        opp_rating_after = white_rating_after
    elif opp_side == "B":
        opp_rating_after = black_rating_after

    # My rating before/after if we have diff + after
    my_rating_after = None
    my_rating_before = None
    my_diff = None
    my_acc = None

    if me_side == "W":
        my_rating_after = white_rating_after
        my_diff = white_diff
        my_acc = white_acc
    elif me_side == "B":
        my_rating_after = black_rating_after
        my_diff = black_diff
        my_acc = black_acc

    if my_rating_after is not None and my_diff is not None:
        my_rating_before = my_rating_after - my_diff

    return {
        "me_side": me_side,
        "opp_user": opp_user,
        "opp_rating_after": opp_rating_after,
        "my_rating_before": my_rating_before,
        "my_rating_after": my_rating_after,
        "my_diff": my_diff,
        "my_accuracy": my_acc,
        "time_class": game.get("time_class"),
        "rated": game.get("rated"),
    }


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
            parsed.append((start, end, res, side, g))

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


# ========================
# Country lookup + cache
# ========================

_opponent_country_cache = {}  # username -> country code (e.g., 'FR')
_country_name_cache = {}      # country code -> friendly name (e.g., 'France')
_country_cache_dirty = False


def _country_cache_path(for_user: str) -> str:
    """Store country caches alongside the user's other cached data."""
    safe_user = (for_user or "").strip().lower() or "_unknown"
    user_dir = os.path.join(CACHE_ROOT, safe_user)
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "country_cache.json")


def load_country_cache(for_user: str, verbose: bool = False) -> None:
    """Load country caches from disk (best-effort)."""
    global _opponent_country_cache, _country_name_cache
    path = _country_cache_path(for_user)
    if not os.path.exists(path):
        return
    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f) if f else {}
        if isinstance(payload, dict):
            pc = payload.get("player_country")
            cn = payload.get("country_name")
            if isinstance(pc, dict):
                _opponent_country_cache.update(pc)
            if isinstance(cn, dict):
                _country_name_cache.update(cn)
        dprint(verbose, f"Loaded country cache: {path}")
    except Exception as e:
        dprint(verbose, f"Failed to load country cache ({path}): {e}")


def save_country_cache(for_user: str, verbose: bool = False) -> None:
    """Save country caches to disk (best-effort)."""
    global _country_cache_dirty
    if not _country_cache_dirty:
        return

    path = _country_cache_path(for_user)
    tmp = f"{path}.tmp"
    try:
        import json

        payload = {
            "player_country": _opponent_country_cache,
            "country_name": _country_name_cache,
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        _country_cache_dirty = False
        dprint(verbose, f"Saved country cache: {path}")
    except Exception as e:
        dprint(verbose, f"Failed to save country cache ({path}): {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def get_country_name(country_code: str, verbose: bool = False):
    """Best-effort: map a country code like 'FR' to a display name like 'France'.

    Uses Chess.com country endpoint. Results are cached in-memory.
    """
    code = (country_code or "").strip().upper()
    if not code:
        return None
    if code in _country_name_cache:
        return _country_name_cache[code]

    url = f"https://api.chess.com/pub/country/{code}"
    try:
        resp = requests.get(url, headers={"User-Agent": "chess-sessions/1.0"}, timeout=10)
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        name = payload.get("name")
        if isinstance(name, str) and name.strip():
            global _country_cache_dirty
            _country_name_cache[code] = name.strip()
            _country_cache_dirty = True
            return _country_name_cache[code]
    except Exception as e:
        dprint(verbose, f"Country name lookup failed for {code}: {e}")

    _country_name_cache[code] = None
    return None


def get_player_country(username: str, verbose: bool = False):
    """Best-effort: fetch a player's country (last path segment of country URL).

    Uses Chess.com player endpoint. Results are cached in-memory.
    """
    u = (username or "").strip().lower()
    if not u:
        return None
    if u in _opponent_country_cache:
        return _opponent_country_cache[u]

    url = f"https://api.chess.com/pub/player/{u}"
    try:
        resp = requests.get(url, headers={"User-Agent": "chess-sessions/1.0"}, timeout=10)
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        country_url = payload.get("country")
        if isinstance(country_url, str) and country_url.strip():
            code = country_url.rstrip("/").split("/")[-1]
            global _country_cache_dirty
            _opponent_country_cache[u] = code
            _country_cache_dirty = True
            return code
    except Exception as e:
        dprint(verbose, f"Country lookup failed for {u}: {e}")

    _opponent_country_cache[u] = None
    return None


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
    parser.add_argument(
        "--details",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show per-game detail lines inside each session (opponent, accuracy, ratings)",
    )
    parser.add_argument(
        "--with-country",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When used with --details, include opponent country names (uses disk cache; may add API calls)",
    )
    args = parser.parse_args()

    console = Console()

    if not args.user:
        print("Error: No username provided.")
        print("Set CHESS_USERNAME in your environment or pass --user <username>.")
        raise SystemExit(1)

    # Load country cache after user validation
    load_country_cache(args.user, args.verbose)

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
                parsed.append((start, end, res, side, g))

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

    # Side breakdown
    w_wins = sum(1 for r, s in flat if r == "W" and s == "W")
    w_losses = sum(1 for r, s in flat if r == "L" and s == "W")
    w_draws = sum(1 for r, s in flat if r == "D" and s == "W")
    w_total = w_wins + w_losses + w_draws
    w_pct = round(w_wins / w_total * 100) if w_total else 0

    b_wins = sum(1 for r, s in flat if r == "W" and s == "B")
    b_losses = sum(1 for r, s in flat if r == "L" and s == "B")
    b_draws = sum(1 for r, s in flat if r == "D" and s == "B")
    b_total = b_wins + b_losses + b_draws
    b_pct = round(b_wins / b_total * 100) if b_total else 0

    # Elo range (best-effort): use first/last games where we can infer before/after
    elo_start = None
    elo_end = None
    for g in parsed:
        details = extract_game_details(g[4], args.user)
        if details.get("my_rating_before") is not None:
            elo_start = details["my_rating_before"]
            break
    for g in reversed(parsed):
        details = extract_game_details(g[4], args.user)
        if details.get("my_rating_after") is not None:
            elo_end = details["my_rating_after"]
            break
    elo_delta = (elo_end - elo_start) if (elo_start is not None and elo_end is not None) else None

    console.print()
    console.print(f"Chess.com stats for {args.user}")
    console.print()
    console.print(colorize_with_side(flat))
    console.print()

    # Summary table
    t = Table(show_header=True, header_style="bold")
    t.add_column("", justify="left")
    t.add_column("W", justify="right")
    t.add_column("L", justify="right")
    t.add_column("D", justify="right")
    t.add_column("Win%", justify="right")

    t.add_row("All", str(wins), str(losses), str(draws), f"{win_pct}%")
    t.add_row("White", str(w_wins), str(w_losses), str(w_draws), f"{w_pct}%")
    t.add_row("Black", str(b_wins), str(b_losses), str(b_draws), f"{b_pct}%")

    console.print(t)

    if elo_start is not None and elo_end is not None and elo_delta is not None:
        sign = "+" if elo_delta >= 0 else ""
        console.print()
        console.print(f"Elo: {elo_start} to {elo_end} ({sign}{elo_delta})")

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
            # Session stats
            sw = sum(1 for r, _ in res if r == "W")
            sl = sum(1 for r, _ in res if r == "L")
            sd = sum(1 for r, _ in res if r == "D")
            stotal = sw + sl + sd
            spct = round(sw / stotal * 100) if stotal else 0

            # Session Elo (best-effort)
            sess_elo_start = None
            sess_elo_end = None
            for gg in sess:
                d = extract_game_details(gg[4], args.user)
                if d.get("my_rating_before") is not None:
                    sess_elo_start = d["my_rating_before"]
                    break
            for gg in reversed(sess):
                d = extract_game_details(gg[4], args.user)
                if d.get("my_rating_after") is not None:
                    sess_elo_end = d["my_rating_after"]
                    break
            sess_delta = (
                sess_elo_end - sess_elo_start
                if (sess_elo_start is not None and sess_elo_end is not None)
                else None
            )

            elo_str = ""
            if sess_delta is not None:
                sign = "+" if sess_delta >= 0 else ""
                elo_str = f" -- Elo: {sess_elo_start} to {sess_elo_end} ({sign}{sess_delta})"

            console.print(
                f"[bold]- {time_str:<20} {len(sess)} games[/bold] -- {sw}W / {sl}L / {sd}D / {spct}%{elo_str} ",
                colorize_with_side(res),
            )

            # Optional per-game details (table)
            if args.details:
                gt = Table(show_header=True, header_style="bold", pad_edge=False)
                gt.add_column("#", justify="right", width=3)
                gt.add_column("Res", justify="left", width=3)
                gt.add_column("Side", justify="left", width=6)
                gt.add_column("Acc", justify="right", width=10)
                gt.add_column("GameRt", justify="right", width=7)
                gt.add_column("Opp", justify="left")
                gt.add_column("Country", justify="left", width=12)
                gt.add_column("Elo", justify="left", width=18)

                for i, gg in enumerate(sess, start=1):
                    gdict = gg[4]
                    d = extract_game_details(gdict, args.user)

                    # Result cell with background indicating side
                    res_text = colorize_with_side([(gg[2], d.get("me_side"))])

                    side_word = "white" if d.get("me_side") == "W" else "black"

                    acc = d.get("my_accuracy")
                    acc_str = f"{acc:.1f}%" if isinstance(acc, float) else "n/a"

                    game_rating = d.get("my_rating_after")
                    game_rating_str = f"{game_rating}" if isinstance(game_rating, int) else "n/a"

                    opp_user = d.get("opp_user") or "(unknown)"
                    opp_rating = d.get("opp_rating_after")
                    opp_str = f"{opp_user}({opp_rating})" if opp_rating is not None else opp_user

                    country_str = ""
                    if args.with_country and opp_user and opp_user != "(unknown)":
                        code = get_player_country(opp_user, args.verbose)
                        if code:
                            name = get_country_name(code, args.verbose)
                            country_str = name or code

                    mr0 = d.get("my_rating_before")
                    mr1 = d.get("my_rating_after")
                    md = d.get("my_diff")
                    elo_move = ""
                    if mr0 is not None and mr1 is not None and md is not None:
                        sign = "+" if md >= 0 else ""
                        elo_move = f"{mr0}→{mr1} ({sign}{md})"

                    gt.add_row(
                        str(i),
                        res_text,
                        side_word,
                        acc_str,
                        game_rating_str,
                        opp_str,
                        country_str,
                        elo_move,
                    )

                console.print(gt)
        print()

    # Save country cache after all output
    save_country_cache(args.user, args.verbose)


if __name__ == "__main__":
    main()