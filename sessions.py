#!/usr/bin/env python3
"""
chess_sessions.py
Fetch Chess.com games for this month and group them into sessions.
Session = consecutive games with gaps <= --gap minutes.
"""

import argparse
import datetime as dt
import re
import requests
from zoneinfo import ZoneInfo  # Python 3.9+

# ANSI color codes (bright + bold)
COLOR_WIN = "\033[1;92m"    # bright green
COLOR_LOSS = "\033[1;91m"   # bright red
COLOR_DRAW = "\033[1;90m"   # bright gray
COLOR_RESET = "\033[0m"

# ---------- DEFAULT CONFIG ----------
DEFAULT_USER = "Massachuuu"
LOCAL_TZ = ZoneInfo("America/New_York")  # handles EST/EDT
PGN_DATE_RE = re.compile(r'\[UTCDate "([\d\.]+)"\]')
PGN_START_RE = re.compile(r'\[UTCTime "([\d:]+)"\]')
PGN_END_RE = re.compile(r'\[EndTime "([\d:]+)"\]')
# ------------------------------------


def dprint(enabled: bool, msg: str):
    """Debug print."""
    if enabled:
        now = dt.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] {msg}")


def parse_pgn_times(pgn: str, verbose=False):
    """
    Extract start and end datetimes (UTC) from a PGN string.
    Returns (start_utc, end_utc) or (None, None) on failure.
    """
    m_date = PGN_DATE_RE.search(pgn)
    m_start = PGN_START_RE.search(pgn)
    m_end = PGN_END_RE.search(pgn)
    if not (m_date and m_start and m_end):
        dprint(verbose, "PGN missing date or time fields.")
        return None, None

    date_str = m_date.group(1)    # e.g. 2025.11.10
    start_str = m_start.group(1)  # e.g. 01:19:42
    end_str = m_end.group(1)      # e.g. 01:52:22

    start_utc = dt.datetime.strptime(
        f"{date_str} {start_str}", "%Y.%m.%d %H:%M:%S"
    ).replace(tzinfo=dt.timezone.utc)
    end_utc = dt.datetime.strptime(
        f"{date_str} {end_str}", "%Y.%m.%d %H:%M:%S"
    ).replace(tzinfo=dt.timezone.utc)

    dprint(verbose, f"Parsed game: {start_utc=}, {end_utc=}")
    return start_utc, end_utc


def get_game_result(game, username):
    """
    Determine game result from the perspective of username.
    Returns "W", "L", or "D".
    """
    username = username.lower()
    white_user = game.get("white", {}).get("username", "").lower()
    black_user = game.get("black", {}).get("username", "").lower()
    white_result = game.get("white", {}).get("result", "")
    black_result = game.get("black", {}).get("result", "")

    # Map results to letters
    win_results = {"win"}
    lose_results = {"checkmated", "resigned", "timeout", "abandoned", "lose"}
    draw_results = {"agreed", "repetition", "stalemate", "insufficient"}

    if username == white_user:
        res = white_result
    elif username == black_user:
        res = black_result
    else:
        return "D"  # default to draw if user not found (should not happen)

    if res in win_results:
        return "W"
    elif res in lose_results:
        return "L"
    elif res in draw_results:
        return "D"
    else:
        # Unknown result, treat as draw
        return "D"


def colorize_results(results: str) -> str:
    out = ""
    for ch in results:
        if ch == "W":
            out += f"{COLOR_WIN}W{COLOR_RESET}"
        elif ch == "L":
            out += f"{COLOR_LOSS}L{COLOR_RESET}"
        elif ch == "D":
            out += f"{COLOR_DRAW}D{COLOR_RESET}"
        else:
            out += ch
    return out


def fetch_month_games(username: str, year: int, month: int, verbose=False):
    url = f"https://api.chess.com/pub/player/{username}/games/{year}/{month:02d}"
    dprint(verbose, f"Fetching: {url}")

    headers = {
        "User-Agent": "chess-sessions/1.0 (contact: you@example.com)"
    }

    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()

    games = resp.json().get("games", [])
    dprint(verbose, f"Fetched {len(games)} games for {year}-{month:02d}")
    return games


def main():
    parser = argparse.ArgumentParser(
        description="Show Chess.com sessions for the last N days (within current month)."
    )
    parser.add_argument("--days", type=int, default=1,
                        help="How many recent days to include (default: 1 = today)")
    parser.add_argument("--user", type=str, default=DEFAULT_USER,
                        help="Chess.com username")
    parser.add_argument("--gap", type=int, default=60,
                        help="Gap (in minutes) between games that defines a new session (default: 60)")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug output")
    parser.add_argument(
        "--day-cutoff-hour", type=int, default=5,
        help="Hour of day that defines the start of a new day (e.g. 5 = 5am). Default: 5 (Whoop-style day)"
    )
    args = parser.parse_args()

    day_shift = dt.timedelta(hours=args.day_cutoff_hour)
    now_local = dt.datetime.now(LOCAL_TZ)
    today_local = (now_local - day_shift).date()

    year = today_local.year
    month = today_local.month
    cutoff_date = today_local - dt.timedelta(days=args.days - 1)

    dprint(args.verbose, f"Local today: {today_local}, cutoff: {cutoff_date}")
    dprint(args.verbose, f"User: {args.user}, Gap: {args.gap} min")

    games = fetch_month_games(args.user, year, month, verbose=args.verbose)

    parsed_games = []
    for g in games:
        
        white = g.get("white", {}).get("username", "").lower()
        black = g.get("black", {}).get("username", "").lower()
        # Skip bot games
        if white == "chess.com" or black == "chess.com":
            dprint(args.verbose, "Skipping bot game")
            continue
        
        pgn = g.get("pgn", "")
        start_utc, end_utc = parse_pgn_times(pgn, verbose=args.verbose)
        if not start_utc:
            continue

        start_local = start_utc.astimezone(LOCAL_TZ)
        game_date_local = (start_local - day_shift).date()

        if cutoff_date <= game_date_local <= today_local:
            result = get_game_result(g, args.user)
            parsed_games.append((start_utc, end_utc, result))
            dprint(args.verbose, f"Keeping game on {game_date_local} (local)")
        else:
            dprint(args.verbose, f"Skipping game on {game_date_local} (local)")

    parsed_games.sort(key=lambda x: x[0])

    if not parsed_games:
        print("No games found in that date range.")
        return

    # ---- group into sessions ----
    sessions = []
    gap_delta = dt.timedelta(minutes=args.gap)
    current = []

    for start_utc, end_utc, result in parsed_games:
        if not current:
            current = [(start_utc, end_utc, result)]
            continue

        last_end_utc = current[-1][1]
        if start_utc - last_end_utc <= gap_delta:
            current.append((start_utc, end_utc, result))
        else:
            sessions.append(current)
            current = [(start_utc, end_utc, result)]

    if current:
        sessions.append(current)

    # ---- group sessions by calendar day ----
    from collections import defaultdict
    sessions_by_day = defaultdict(list)
    for sess in sessions:
        sess_start_local = sess[0][0].astimezone(LOCAL_TZ)
        sess_date = (sess_start_local - day_shift).date()
        sessions_by_day[sess_date].append(sess)

    # ---- output ----
    print(f"\nSessions for {args.user} in the last {args.days} days\n")

    overall_results = "".join("".join(game[2] for game in sess) for day in sorted(sessions_by_day) for sess in sessions_by_day[day])
    wins = overall_results.count("W")
    losses = overall_results.count("L")
    draws = overall_results.count("D")
    total_games = wins + losses + draws
    win_rate_percent = round(wins / total_games * 100) if total_games > 0 else 0

    print(f"{colorize_results(overall_results)} -- {wins} wins, {losses} losses, {draws} draws -- {win_rate_percent}% win rate\n")

    COL_TIME = 20
    COL_GAMES = 10

    for day in sorted(sessions_by_day):
        day_sessions = sessions_by_day[day]
        daily_results = "".join(
            "".join(game[2] for game in sess) for sess in day_sessions
        )
        print(day.strftime("%A, %B %d"))

        for sess in day_sessions:
            s_start_local = sess[0][0].astimezone(LOCAL_TZ)
            s_end_local = sess[-1][1].astimezone(LOCAL_TZ)
            duration = s_end_local - s_start_local

            start_str = s_start_local.strftime("%-I:%M%p").lower()
            end_str = s_end_local.strftime("%-I:%M%p").lower()
            games_count = len(sess)
            sess_results = "".join(game[2] for game in sess)

            dash = "- "
            time_col = f"{start_str} to {end_str}".ljust(COL_TIME)
            games_col = f"{games_count} game{'s' if games_count != 1 else ''}".ljust(COL_GAMES)
            results_col = colorize_results(sess_results)

            print(f"{dash}{time_col} {games_col} {results_col}")
        print()

    if args.verbose:
        print("Done.")


if __name__ == "__main__":
    main()