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