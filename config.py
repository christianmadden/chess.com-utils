import os
import re
from zoneinfo import ZoneInfo

DEFAULT_USER = os.getenv("CHESS_USERNAME")

LOCAL_TZ = ZoneInfo("America/New_York")

CACHE_ROOT = os.getenv(
    "CHESS_SESSIONS_CACHE_DIR", os.path.join(os.path.dirname(__file__), "json")
)

PGN_DATE_RE = re.compile(r'\[UTCDate "([\d\.]+)"\]')
PGN_START_RE = re.compile(r'\[UTCTime "([\d:]+)"\]')
PGN_END_RE = re.compile(r'\[EndTime "([\d:]+)"\]')
PGN_TAG_RE = re.compile(r'\[([^\s]+) "([^\"]*)"\]')