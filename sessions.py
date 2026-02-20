#!/usr/bin/env python3
"""chess_sessions.py

Fetch Chess.com games and group them into sessions.
Session = consecutive games with gaps <= --gap minutes.
"""

import argparse
import datetime as dt
from collections import defaultdict

import rich.box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from config import DEFAULT_USER, LOCAL_TZ
from parsing import (
    dprint,
    parse_pgn_times,
    extract_game_details,
    get_game_result,
    colorize_result,
    colorize_results_by_session,
)
from fetching import (
    clear_user_cache,
    fetch_month_games,
    fetch_most_recent_games,
    fetch_games_for_range,
    month_iter_backwards,
    is_skippable,
    get_country_lookup,
)


def _build_wld_summary(results):
    """Build a compact summary like '7 games // 6-1-0 (86%) // White: 3-1-0 (75%) // Black: 3-0-0 (100%)'"""
    wins = sum(1 for r, _ in results if r == "W")
    losses = sum(1 for r, _ in results if r == "L")
    draws = sum(1 for r, _ in results if r == "D")
    total = wins + losses + draws
    pct = round(wins / total * 100) if total else 0

    w_wins = sum(1 for r, s in results if r == "W" and s == "W")
    w_losses = sum(1 for r, s in results if r == "L" and s == "W")
    w_draws = sum(1 for r, s in results if r == "D" and s == "W")
    w_total = w_wins + w_losses + w_draws
    w_pct = round(w_wins / w_total * 100) if w_total else 0

    b_wins = sum(1 for r, s in results if r == "W" and s == "B")
    b_losses = sum(1 for r, s in results if r == "L" and s == "B")
    b_draws = sum(1 for r, s in results if r == "D" and s == "B")
    b_total = b_wins + b_losses + b_draws
    b_pct = round(b_wins / b_total * 100) if b_total else 0

    t = Text()
    t.append(f"{total} games", style="bold")
    t.append("  //  ", style="dim")
    t.append(f"{wins}-{losses}-{draws} ({pct}%)")
    t.append("  //  ", style="dim")
    t.append("White: ", style="bold")
    t.append(f"{w_wins}-{w_losses}-{w_draws} ({w_pct}%)")
    t.append("  //  ", style="dim")
    t.append("Black: ", style="bold")
    t.append(f"{b_wins}-{b_losses}-{b_draws} ({b_pct}%)")
    return t


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
        "--days", type=parse_days, default=None,
        help="Number of days to include, or 'all' for all available history",
    )
    parser.add_argument(
        "--user", type=str, default=DEFAULT_USER,
        help="Chess.com username (or set CHESS_USERNAME in environment)",
    )
    parser.add_argument("--gap", type=int, default=60)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--day-cutoff-hour", type=int, default=5)
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Delete cached monthly data for this user before running",
    )
    parser.add_argument(
        "--games", type=int, default=None,
        help="Show only the N most recent games",
    )
    parser.add_argument(
        "--details", action=argparse.BooleanOptionalAction, default=True,
        help="Show per-game detail lines inside each session",
    )
    args = parser.parse_args()

    console = Console()

    if not args.user:
        console.print("[red]Error:[/red] No username provided.")
        console.print("Set CHESS_USERNAME in your environment or pass --user <username>.")
        raise SystemExit(1)

    if args.no_cache:
        clear_user_cache(args.user)

    now = dt.datetime.now(LOCAL_TZ)
    day_shift = dt.timedelta(hours=args.day_cutoff_hour)
    today = (now - day_shift).date()

    # Default: no flags → today only
    if args.days is None and args.games is None:
        args.days = 1

    if args.days is None:
        parsed = fetch_most_recent_games(args.user, args.games, args.verbose)
    else:
        if args.days == "all":
            cutoff = None
        else:
            cutoff = today - dt.timedelta(days=args.days - 1)

        if cutoff is None:
            games = []
            for y, m in month_iter_backwards(today):
                games.extend(fetch_month_games(args.user, y, m, args.verbose))
        else:
            games = fetch_games_for_range(args.user, cutoff, today, args.verbose)

        parsed = []
        for g in games:
            if is_skippable(g):
                continue

            start, end = parse_pgn_times(g.get("pgn", ""), args.verbose)
            if not start or not end:
                continue

            local_start = start.astimezone(LOCAL_TZ)
            game_day = (local_start - day_shift).date()

            if cutoff is None or (cutoff <= game_day <= today):
                res, side = get_game_result(g, args.user)
                parsed.append((start, end, res, side, g))

        parsed.sort(key=lambda x: x[0])

        if args.games is not None and args.games > 0:
            parsed = parsed[-args.games:]

    if not parsed:
        console.print("No games found.")
        return

    # Pre-fetch country codes for all opponents (uses local cache, fetches misses)
    username_l = args.user.lower()
    opp_usernames = set()
    for _, _, _, _, g in parsed:
        w = g.get("white", {}).get("username", "")
        b = g.get("black", {}).get("username", "")
        if w.lower() == username_l:
            opp_usernames.add(b)
        elif b.lower() == username_l:
            opp_usernames.add(w)
    country_lookup = get_country_lookup(args.user, list(opp_usernames), args.verbose)

    # ── Group into sessions ──────────────────────────────────────────
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

    # ── Compute overall summary stats ────────────────────────────────
    flat = [
        (g[2], g[3])
        for day in sorted(sessions_by_day)
        for s in sessions_by_day[day]
        for g in s
    ]

    # Elo range
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

    # ── Print header ─────────────────────────────────────────────────
    console.print(f"[bold green]Chess.com results for {args.user}[/bold green]")
    console.print()

    # Result strip with session separators
    console.print(colorize_results_by_session(sessions_by_day))
    console.print()

    # Compact summary line
    console.print(_build_wld_summary(flat))

    if elo_start is not None and elo_end is not None and elo_delta is not None:
        sign = "+" if elo_delta >= 0 else ""
        elo_color = "green" if elo_delta >= 0 else "red"
        elo_text = Text()
        elo_text.append("Elo: ", style="bold")
        elo_text.append(f"{elo_start} → {elo_end} ")
        elo_text.append(f"({sign}{elo_delta})", style=elo_color)
        console.print(elo_text)

    # ── Per-day output ───────────────────────────────────────────────
    prev_my_rating = None
    for day in sorted(sessions_by_day):
        console.print()
        console.print()
        console.print(f"[bold]{day.strftime('%A, %B %-d')}[/bold]")

        for sess in sessions_by_day[day]:
            start = sess[0][0].astimezone(LOCAL_TZ)
            end = sess[-1][1].astimezone(LOCAL_TZ)

            time_str = (
                f"{start.strftime('%-I:%M%p').lower()} to {end.strftime('%-I:%M%p').lower()}"
            )
            console.rule(style="#444444")
            console.print(Text(time_str, style="#999999"))
            console.rule(style="#444444")

            if args.details:
                gt = Table(
                    box=None,
                    show_header=False,
                    pad_edge=False,
                    padding=(0, 1),
                )
                gt.add_column("", justify="left", width=3)
                gt.add_column("Acc", justify="right", width=6)
                gt.add_column("Opponent", justify="left", width=32)
                gt.add_column("CC", justify="left", width=20)
                gt.add_column("Opp Elo", justify="right", width=7)
                gt.add_column("My Elo", justify="right", width=14)

                for gg in sess:
                    d = extract_game_details(gg[4], args.user)

                    res_badge = colorize_result(gg[2], d.get("me_side"))

                    acc = d.get("my_accuracy")
                    acc_str = f"{acc:.1f}%" if isinstance(acc, float) else ""

                    opp_user = d.get("opp_user") or ""
                    country_display = country_lookup.get(opp_user.lower()) or ""
                    opp_rating = d.get("opp_rating_after")
                    opp_elo_str = str(opp_rating) if opp_rating is not None else ""

                    my_after = d.get("my_rating_after")

                    if my_after is not None:
                        result_letter = gg[2]
                        if result_letter == "W":
                            elo_style = "green"
                        elif result_letter == "L":
                            elo_style = "red"
                        else:
                            elo_style = ""
                        if prev_my_rating is not None:
                            delta = my_after - prev_my_rating
                            sign = "+" if delta >= 0 else ""
                            delta_str = f"{sign}{delta}".rjust(4)
                            my_elo_cell = Text(f"{my_after} {delta_str}", style=elo_style)
                        else:
                            my_elo_cell = Text(f"{my_after} {'--':>4}", style=elo_style)
                        prev_my_rating = my_after
                    else:
                        my_elo_cell = Text("")

                    gt.add_row(
                        res_badge,
                        acc_str,
                        opp_user,
                        country_display,
                        opp_elo_str,
                        my_elo_cell
                    )

                console.print(gt)

    console.print()


if __name__ == "__main__":
    main()