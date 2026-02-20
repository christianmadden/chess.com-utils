import datetime as dt

from rich.style import Style
from rich.text import Text

from config import PGN_DATE_RE, PGN_START_RE, PGN_END_RE, PGN_TAG_RE


def dprint(enabled: bool, msg: str) -> None:
    if enabled:
        now = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
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

    if end < start:
        end += dt.timedelta(days=1)

    return start, end


def parse_pgn_tags(pgn: str) -> dict:
    """Parse PGN tag-pairs into a dict."""
    if not pgn:
        return {}
    return {k: v for k, v in PGN_TAG_RE.findall(pgn)}


def _to_int(s):
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return None


def _to_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return None


def _parse_rating_diff(s):
    """Parse a rating diff like '+8' or '-12' or '0' -> int or None."""
    if s is None:
        return None
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return None


def extract_game_details(game: dict, username: str):
    """Return a dict of per-game details for display."""
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

    white_rating_after = _to_int(game.get("white", {}).get("rating"))
    black_rating_after = _to_int(game.get("black", {}).get("rating"))

    pgn = game.get("pgn", "")
    tags = parse_pgn_tags(pgn)

    white_elo_tag = _to_int(tags.get("WhiteElo"))
    black_elo_tag = _to_int(tags.get("BlackElo"))
    if white_elo_tag is not None:
        white_rating_after = white_elo_tag
    if black_elo_tag is not None:
        black_rating_after = black_elo_tag

    white_diff = _parse_rating_diff(tags.get("WhiteRatingDiff"))
    black_diff = _parse_rating_diff(tags.get("BlackRatingDiff"))

    white_acc = _to_float(tags.get("WhiteAccuracy"))
    black_acc = _to_float(tags.get("BlackAccuracy"))

    acc_obj = game.get("accuracies")
    if isinstance(acc_obj, dict):
        if white_acc is None:
            white_acc = _to_float(acc_obj.get("white"))
        if black_acc is None:
            black_acc = _to_float(acc_obj.get("black"))

    opp_rating_after = None
    if opp_side == "W":
        opp_rating_after = white_rating_after
    elif opp_side == "B":
        opp_rating_after = black_rating_after

    my_rating_after = None
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

    my_rating_before = (
        (my_rating_after - my_diff)
        if (my_rating_after is not None and my_diff is not None)
        else None
    )

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


def colorize_result(res: str, side: str) -> Text:
    """Return a single padded, colorized result letter with side-indicating background."""
    bg = "#333333" if side == "B" else "#eeeeee"
    if res == "W":
        style = Style(color="#00aa00", bgcolor=bg)
    elif res == "L":
        style = Style(color="#ff0000", bgcolor=bg)
    else:
        style = Style(color="#666666", bgcolor=bg)

    text = Text()
    text.append(f" {res} ", style=style)
    return text


def colorize_results_by_session(sessions_by_day):
    """Render a W/L/D strip with session groups separated by a divider."""
    text = Text()
    for day in sorted(sessions_by_day):
        for sess in sessions_by_day[day]:
            for g in sess:
                text.append_text(colorize_result(g[2], g[3]))
    return text