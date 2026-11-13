"""Grading of the public pick lists, exactly as the locked prediction says: docs/research/2026-11-13-pick-lists-prediction.md
(commit 5f9183c, with its clarifications 1 to 21). Research only: nothing here sends anything to an exchange or a broker.

    python -m valedia_trader.research_loop.pick_grading <command> [--store DIR] [--picks FILE]

What the guards are for (the lead's ruling, clarification 19): the one-time rules (one reading per rule, one scoring,
one record) protect the operator from mistakes and from quiet self-deception; they cannot stop an operator who edits the
code or builds a second checkout, and do not claim to. What they do is not be reset by a slip or a flag, and leave a
record nobody can miss: every reading ever made is printed by `status`, by every evaluation and in the scoring log.

The stores live in one folder (`--store`, default `artifacts/pick_grading/`, which the repository's `artifacts/` line in
`.gitignore` keeps out of git): `grading.sqlite` (this module's own store, append-only, layout `LAYOUT`), `credits.sqlite`
(the credit ledger of `sports_lines.CreditLedger`), `kalshi.sqlite` (`kalshi_history.HistoryStore`) and `scoring.log`.
The pick store (`--picks`) is opened read-only through `pick_lists.Store`; comment text reaches grading only through its
`version_before(comment_id, moment)`. One record (clarification 13): a pick store has an identity taken from its own
first rows (`pick_store_identity`: the first thread's id and first-seen time and the first payload's kind, key, sha256
and fetch time). A grading store is bound to the identity of the first pick store it grades and refuses any other; the
real pick store (`DEFAULT_PICKS`) is graded into the default grading store (`DEFAULT_STORE`) only; and a pick store
whose identity the default grading store is bound to is refused with any other grading store, whatever its path (a
copy, a link, a copy with more rows).

Commands:

- `schedule`: The Odds API's free events list of each of the five leagues, stored as fetched, and the table of team
  codes of each of Kalshi's five game series, from its events list of every status (keyless, at most 30 requests a run;
  an event carries its title and no result, and no market is read). A league's table is stored only when its listing
  ended within the run's requests; it is stored as listed, and an event that does not give two readable codes ("CODE vs
  CODE (Mon DD)") is counted (`unreadable`, shown by `schedule` and `status`). `schedule --date YYYY-MM-DD` buys the historical list of a missed day (1 credit a
  league, the paid key), as of 06:00 US-Eastern that day. A game's start is the earliest `commence_time` any stored
  list gives for it, whenever that list was fetched (clarification 8); a game counts only if a list fetched before
  that start held it, and lists that differ on its start by more than 30 minutes flag it.
- `match`: one row a counted pick (`matches`), matched or not graded with one reason of `REASONS`. A comment's words are
  compared with every team known from the stored lists of the leagues its league field allows (all five when it names
  none), any date; a pick names team T only when no other known team is named at least as fully (a whole name beats a
  city or a nickname; a tie is not graded); then T must play exactly one game of the window (clarification 9). A
  selection that names teams of two different games, or bets against the team it names, is not graded (clarification
  18). Kalshi's codes count as names only from complete tables (clarification 16): every league's latest table was
  fetched in the last 7 days and every event of it gave two readable codes (clarification 21); a code written in capitals names a team only when it belongs to one team alone across all stored
  tables, or within the league the comment writes; a code that is also an English word counts only directly before
  the bet's marker or as the selection's only other word; in a selection written wholly in capitals no word is a code
  when it holds a word besides team codes and the bet's own markers (clarification 20: "LAL ML", "BOS -4.5" and
  "KC/BUF OVER 47.5" keep their codes; "IND STRONG ML" has none).
  No model proposes anything. The rule's fingerprint (`fingerprint()`, over the parsed code of every function a
  `match` run can reach from the entry point down: `_cli`, `main`, the store and grader set-up and `cmd_match`; the
  constants they read, the store's tables and the word list) is stored with every row. It is the same whatever way the
  module was started (imported or run with `python -m`).
- `audit-sample [--out DIR]`: 40 matched picks beside the comment text they were graded from, in a file outside the
  repository. The draw's seed comes from the fingerprint; there is one sample a fingerprint. `audit-pass --sample N
  --wrong K` records the lead's count for that sample, once. More than 2 wrong closes the fingerprint for good; only a
  changed rule gets a new reading (clarification 10). Every reading is listed with every evaluation.
- `prices`: Kalshi's single-game events paired with the matched moneyline games (`sports_lines.pair_games`, strict, no
  model), and one-minute candles up to the purchase moment and never past the game's start (Question A).
- `results`: Kalshi's settlement of the paired games and The Odds API's scores (`daysFrom=3`, 2 credits a league). A
  league's scores are bought only when one of its matched games started more than 3 hours ago, has no final yet and
  started less than 3 days ago, and no scores list of that league was asked for in the last 6 hours. A final listed 3
  days or more after the start is not used: the game is not graded (clarification 14). Before a choice, the final of
  a game that a pick of a test-period thread is matched to is dropped from the scores list before it is stored (the
  count dropped is shown), and no Kalshi settlement of that game's day is fetched.
  Question B: a pick on a game Kalshi lists (a stored code table's event or a stored market's event, matched to the game
  by its date and both team names) waits for Kalshi's result (`kalshi_not_settled`); a game Kalshi does not list is
  graded on the scores alone.
- `evaluate --period development|test`, `choose --version N`, `daily`, `status`. The test period is scored once: when
  the counts that can be known without reading a result reach the margin (clarification 15: Question A's chosen
  version, 158 contracts on 20 days; Question B, 315 graded picks) or at the last date, 2027-03-31, never again; the
  period ends on the thread date on which both were first reached; the pass lines stay 150 and 300. It is scored when
  its games are over (clarification 21): when it ends by date, not before 2027-04-04 00:00 US-Eastern; when it ends by
  the counts, not before 00:00 US-Eastern four days after the last counted thread's date (the same wait: a thread's
  games start until 06:00 the next day and a final is used if listed within three days of the start).
  `choose` pins the scoring code (`scoring_fingerprint()`, over everything `prices`, `results` and `evaluate` reach);
  `evaluate --period test` refuses a changed scoring code unless `--code-changed "<reason>"` is given, and then the
  reason, both fingerprints and the time are written to the scoring log and the result (clarification 17). `status`
  shows the counts so far without reading a result.

Gates: no command reads a result (Kalshi's settled listing, a score, an evaluation) before an `audit-pass` with at most
2 wrong exists for the current matching fingerprint, nor while a list was stored after the last `match` run. The first
command that reads a result freezes the fingerprint: from then on any command refuses when the fingerprint differs.
Results of test-period picks are not read before a choice is stored, no settlement of a day of the test period is
fetched before a choice (also for a development game on that day), and without a choice no scores are bought from
06:00 US-Eastern on the test period's first day.

The grading store refuses UPDATE, DELETE and an INSERT that repeats a natural key (so INSERT OR REPLACE cannot rewrite
it); writers hold `grading.lock` (one at a time); `status` opens the stores read-only; a store of another layout is
refused before anything is created beside it. A clock that reads earlier than the latest time in the grading store is refused. Nothing a commenter wrote is
printed or logged: output holds counts, league codes, the schedule's team names and hashes; comment text appears only
in the audit file, as quoted data.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import inspect
import json
import os
import random
import re
import sqlite3
import sys
import textwrap
import types
import unicodedata
import urllib.parse
from datetime import date, datetime, time as dtime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from valedia_trader.data_and_fees import pick_lists as pl
from valedia_trader.research_loop import REPO
from valedia_trader.research_loop import sports_lines as sl
from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import kalshi_history as kh
from valedia_trader.venues import odds_api as oa

__all__ = ["PERIODS", "REASONS", "GradingStore", "Refused", "ClockBehind", "fingerprint", "fingerprint_scope",
           "scoring_fingerprint", "scoring_scope", "pick_store_identity", "team_forms", "name_index", "named_teams",
           "match_text", "over_under", "thread_date", "window_end", "schedule_games", "evaluate_comment",
           "purchase_quote", "grade_b", "day_interval", "test_counts", "readings", "main"]

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
LEAGUES = dict(sl.LEAGUES)                  # league -> (Odds API sport key, Kalshi series)
PERIODS = {"development": (date(2026, 11, 13), date(2026, 12, 13)), "test": (date(2026, 12, 14), date(2027, 3, 31))}
DEFAULT_STORE = REPO / "artifacts" / "pick_grading"
DEFAULT_PICKS = REPO / "memory" / "pick_lists.sqlite"
LAYOUT = 3                                  # the grading store's layout (rev 1 wrote no mark, rev 2 wrote 2)
PROMPT_HASH = pl.PROMPT_HASH                # parses graded: only those made under the prompt in force
GRADED_MARKETS = ("moneyline", "spread", "total")
WINDOW_END_HOUR = 6                         # a game must start before 06:00 US-Eastern on the day after the thread's date
START_SPREAD = 1800                         # seconds: lists disagreeing on a start by more than this flag the game
BUY_AFTER = 60                              # the purchase candle ends at least this long after the pick was first seen
BUY_WITHIN = 600                            # ... and at most this long after it
MAX_ASK = 0.95
MAX_SPREAD_CENTS = 5
ODDS_MIN, ODDS_MAX = 1.30, 4.00
AUDIT_SIZE = 40
AUDIT_MAX_WRONG = 2
MONTHLY_CAP = 400                           # credits a key a month for grading
SCORES_DAYS = 3
SCORES_COST = 2
SCORES_AFTER = 3 * 3600                     # a game's scores are asked for only once it started more than this long ago
SCORES_EVERY = 6 * 3600                     # ... and at most once in this long a league
FINAL_WITHIN = 3 * 86400                    # a final listed this long after the start or later is not used
HISTORICAL_COST = 1
HISTORICAL_HOUR_ET = 6
CODE_REQUESTS = 30                          # keyless Kalshi requests for the code tables, a run
CODE_LOOKBACK = 30 * 86400                  # the events listed: those with a market closing after now minus this
CODE_FRESH = 7 * 86400                      # codes count only when every league has a table fetched this recently
CODE_FORM = re.compile(r"^[A-Z][A-Z0-9]{1,5}$")
EVENT_CODES = re.compile(r"^([A-Z][A-Z0-9]{1,5}) vs ([A-Z][A-Z0-9]{1,5})(?: \(|$)")
WORDS_FILE = Path("/usr/share/dict/words")  # English words, compared without case
FALLBACK_WORDS = ("a", "all", "am", "an", "and", "are", "as", "at", "be", "bet", "big", "but", "by", "can", "car",
                  "day", "den", "do", "for", "go", "had", "has", "he", "hit", "i", "if", "in", "is", "it", "let",
                  "lock", "man", "max", "me", "min", "my", "net", "no", "not", "now", "of", "on", "one", "or", "out",
                  "pick", "pit", "play", "sea", "so", "sun", "take", "the", "to", "top", "two", "up", "us", "van",
                  "was", "way", "we", "win", "yes", "you")
AGAINST_WORDS = ("fade", "against", "anti", "not", "vs")
SEED = 20261113
REPS = 10_000
VERSIONS = (1, 2, 3)
A_COUNT, A_DAYS, B_COUNT = 150, 20, 300     # the pass lines' counts
END_A_COUNT, END_B_COUNT = 158, 315         # the test period ends when these (5% above) are reached (clarification 15)
MIN_FORM_CHARS = 3                          # a team's name form shorter than this is never matched
NO_RUN_CAP = 10 ** 9                        # the ledger's run cap is not used here; the monthly cap is
TEST_CHOICE_CUTOFF = datetime(2026, 12, 14, 6, tzinfo=ET)
TEST_SCORED_FROM = date(2027, 4, 4)         # a test ending by date is scored from 00:00 US-Eastern this day (cl. 21)
SCORE_WAIT_DAYS = 4                         # a test ending by the counts: this many days after the last counted date

LEAGUE_WORDS = {                            # the league as written (lower case, letters and digits only)
    "nfl": ("NFL",), "nba": ("NBA",), "nhl": ("NHL",), "hockey": ("NHL",),
    "ncaaf": ("NCAAF",), "ncaafb": ("NCAAF",), "cfb": ("NCAAF",), "collegefootball": ("NCAAF",),
    "ncaab": ("NCAAMB",), "ncaam": ("NCAAMB",), "ncaamb": ("NCAAMB",), "cbb": ("NCAAMB",), "mcbb": ("NCAAMB",),
    "collegebasketball": ("NCAAMB",), "ncaa": ("NCAAF", "NCAAMB"), "college": ("NCAAF", "NCAAMB"),
    "basketball": ("NBA", "NCAAMB"),
}

REASONS = (                                 # why a counted pick is not graded (matching)
    "no_thread_date", "not_first_of_author", "no_market", "market_not_graded", "league_not_graded", "no_selection",
    "no_team_named", "name_shared", "no_game_in_window", "more_than_one_game", "side_unclear", "teams_of_two_games",
    "bets_against", "pick_seen_after_start", "version_not_a_pick", "version_names_other_team", "no_text_before_start",
    "no_start_known", "start_disagrees")
A_REASONS = ("test_waits_for_choice", "no_kalshi_pair", "no_candle", "no_candle_within_10_minutes", "no_bid_ask",
             "ask_above_0.95", "spread_above_5_cents", "no_kalshi_result", "no_score_yet", "no_final_in_3_days",
             "scores_differ", "winner_differs")
B_REASONS = ("test_waits_for_choice", "no_odds", "odds_out_of_range", "no_line", "no_over_under", "no_score_yet",
             "no_final_in_3_days", "scores_differ", "kalshi_not_settled", "winner_differs")

SCORING_LOG = sl.SCORING_LOG
STORE_FILE = "grading.sqlite"
LOCK_FILE = "grading.lock"


class Refused(RuntimeError):
    """A step the locked prediction does not allow now (a closed gate, a frozen rule, a test run without a choice...)."""


class ClockBehind(Refused):
    """The clock reads earlier than the latest time in the grading store: nothing is written."""


def _iso(t: datetime) -> str:
    return pl.iso(t)


def _ts(text: str) -> int:
    return int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp())


def _from_ts(ts: float) -> str:
    return _iso(datetime.fromtimestamp(ts, UTC))


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _system_clock() -> datetime:
    return datetime.now(UTC)


def _list_body(raw) -> list:
    try:
        body = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    data = body.get("data") if isinstance(body, dict) else body
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


# ---------------------------------------------------------------- the matching rule (all it reaches is fingerprinted)

def thread_date(title) -> date | None:
    """The date a thread's title carries ("Pick of the Day - 11/13/26 (Friday)" -> 2026-11-13), or None."""
    m = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?!\d)", title or "")
    if not m:
        return None
    y = int(m.group(3))
    try:
        return date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def window_end(day: date) -> str:
    """06:00 US-Eastern on the day after the thread's date, as a UTC time: no game starting then or later counts."""
    return _iso(datetime.combine(day + timedelta(days=1), dtime(WINDOW_END_HOUR), tzinfo=ET))


def leagues_named(text) -> tuple[str, ...] | None:
    """The leagues a pick's league field allows: all five when it is empty, those of `LEAGUE_WORDS` when it names one,
    None (not graded) for anything else."""
    if text is None or not str(text).strip():
        return tuple(LEAGUES)
    return LEAGUE_WORDS.get(re.sub(r"[^a-z0-9]", "", str(text).lower()))


@lru_cache(maxsize=None)
def team_forms(full: str) -> frozenset:
    """The forms by which a team may be named, as the schedule spells it: the whole name, a city (the first words,
    when `names_match` accepts them for the whole name, so "Florida" is not "Florida St Seminoles") and a nickname
    (the last words, when `nickname_match` accepts them with the words before as the city). Words are compared after
    `sports_lines._tokens`. A form shorter than three characters, or of digits only, is left out."""
    toks = tuple(sl._tokens(full))
    out = {toks} if toks else set()
    for k in range(1, len(toks)):
        city, nick = " ".join(toks[:k]), " ".join(toks[k:])
        if sl.names_match(city, full):
            out.add(toks[:k])
        if sl.nickname_match(city, nick, full):
            out.add(toks[k:])
    return frozenset(f for f in out if len("".join(f)) >= MIN_FORM_CHARS and not "".join(f).isdigit())


def _normal(text) -> str:
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return s.replace("&", "").replace("'", "")


def _spans(text) -> list[tuple[str, str, int, int]]:
    """The words of `text` exactly as `sports_lines._tokens` reads them: (word, spelling as written, start, end) in the
    normalised text, so a Kalshi code can be required in capitals at the same place."""
    s = _normal(text)
    out = []
    for m in re.finditer(r"[A-Za-z0-9]+", s):
        low = m.group().lower()
        word = sl._TOKEN_MAP.get(low, low)
        if word:
            out.append((word, m.group(), m.start(), m.end()))
    return out


def _words(text) -> list[tuple[str, str]]:
    return [(w, raw) for w, raw, _, _ in _spans(text)]


@lru_cache(maxsize=4)
def _read_words(path: str, stamp: tuple) -> frozenset:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return frozenset(FALLBACK_WORDS)
    return frozenset(w.strip().lower() for w in text.split() if w.strip())


def _english_words() -> frozenset:
    """The system word list (`WORDS_FILE`, lower case), or the small built-in `FALLBACK_WORDS` when it is missing."""
    try:
        st = os.stat(WORDS_FILE)
        stamp = (st.st_size, st.st_mtime_ns)
    except OSError:
        return frozenset(FALLBACK_WORDS)
    return _read_words(str(WORDS_FILE), stamp)


_CHUNK_SPLIT = re.compile(r"[\s/@,;:!?()\[\]\"]+")
_SIGNED = re.compile(r"^[+-]\d+(?:\.\d+)?$")
_LINE_FORM = re.compile(r"^[ouOU]?[+-]?(\d+(?:\.\d+)?)$")


def _chunks(s: str) -> list[str]:
    return [c.strip(".") for c in _CHUNK_SPLIT.split(s) if c.strip(".")]


def _is_marker(chunk: str, line) -> bool:
    """The bet's own marker: ML, moneyline, a signed number, or the line (also joined to o / u)."""
    c = chunk.lower()
    if c in ("ml", "moneyline") or _SIGNED.match(c):
        return True
    m = _LINE_FORM.match(c)
    return bool(m and line is not None and abs(float(m.group(1)) - abs(float(line))) < 1e-9)


_BET_WORDS = ("ml", "moneyline", "over", "under", "o", "u")
_NUMBER = re.compile(r"^[ou]?[+-]?\d+(?:\.\d+)?$")


def _another_word(s: str, codes: dict) -> bool:
    """Clarification 20: whether a text holds a word besides team codes (of any stored table) and the bet's own
    markers (ML, moneyline, over / under and o / u, numbers and a line, "/" and "@", "vs" between two codes)."""
    known = set().union(*(codes[lg] for lg in codes))
    chunks = _chunks(s)
    for i, c in enumerate(chunks):
        if c in known or c.lower() in _BET_WORDS or _NUMBER.match(c.lower()):
            continue
        if c.lower() == "vs" and 0 < i < len(chunks) - 1 and chunks[i - 1] in known and chunks[i + 1] in known:
            continue
        return True
    return False


def _where_a_pick_puts_its_team(s: str, start: int, end: int, raw: str, line) -> bool:
    """A word standing directly before the bet's marker, or the only word of the text besides that marker."""
    rest = _chunks(s[end:])
    if rest and _is_marker(rest[0], line) and not s[end:end + 1].isalnum():
        return True
    chunks = _chunks(s)
    return chunks.count(raw) == 1 and all(c == raw or _is_marker(c, line) for c in chunks)


def _code_teams(code: str, kalshi_name: str, names: Iterable[str]) -> set:
    """The teams of one league a Kalshi code may stand for: those whose name `names_match` accepts for Kalshi's name
    ("Vegas" for "Vegas Golden Knights"), and, for a name written as code and nickname ("CIN Bengals"), those whose
    name ends with that nickname after words of its own."""
    out = {n for n in names if kalshi_name and sl.names_match(kalshi_name, n)}
    parts = kalshi_name.split()
    if len(parts) > 1 and parts[0] == code:
        nick = sl._tokens(" ".join(parts[1:]))
        out |= {n for n in names if nick and len(sl._tokens(n)) > len(nick) and sl._tokens(n)[-len(nick):] == nick}
    return out


def _event_codes(e: dict) -> list[tuple[str, str]] | None:
    """[(code, Kalshi name)] of an events-list entry ("CIN vs WAS (Nov 23)", "Cincinnati vs Washington"); names are
    empty when the title does not split; None when the sub-title carries no two codes."""
    m = EVENT_CODES.match(str(e.get("sub_title") or ""))
    if not m:
        return None
    names = str(e.get("title") or "").split(" vs ")
    names = names if len(names) == 2 else ["", ""]
    return [(m.group(1), names[0].strip()), (m.group(2), names[1].strip())]


def _unreadable(events: Iterable) -> int:
    """The events of a listing that do not give two readable codes: no event ticker, or a sub-title not of the form
    "CODE vs CODE (Mon DD)" (clarification 21: one makes that league's table incomplete)."""
    return sum(1 for e in events if not (isinstance(e, dict) and e.get("event_ticker") and _event_codes(e) is not None))


def name_index(lists: Iterable[dict], code_tables: Iterable[dict] | None) -> dict:
    """Every team known: the teams of every stored events list ({league, raw}) of the five leagues, any date, by
    league, with each form that names them (`team_forms`; rank 2 for the whole name, 1 for a city or a nickname); and,
    when `code_tables` is given (None: the tables are not complete), the owners of each Kalshi code by league from all
    stored tables ({league, raw}). In each event the two codes go to the teams `_code_teams` finds, an event whose two
    codes' candidates fit exactly one stored game settling both; a code whose owner is found in no event stands for one
    unknown team of its league. A code names a team only when its owners in the leagues compared are that one team."""
    teams: dict[str, set] = {lg: set() for lg in LEAGUES}
    played: dict[str, set] = {lg: set() for lg in LEAGUES}
    for r in lists:
        if r["league"] not in teams:
            continue
        for e in _list_body(r["raw"]):
            sides = [e.get(s) for s in ("home_team", "away_team")]
            for name in sides:
                if isinstance(name, str) and name.strip():
                    teams[r["league"]].add(name)
            if all(isinstance(n, str) and n.strip() for n in sides):
                played[r["league"]].add(frozenset(sides))
    forms: dict[str, dict] = {lg: {} for lg in LEAGUES}
    longest = 1
    for lg, names in teams.items():
        for name in sorted(names):
            full = tuple(sl._tokens(name))
            for f in sorted(team_forms(name)):
                forms[lg].setdefault(f, []).append(((lg, name), 2 if f == full else 1))
                longest = max(longest, len(f))
    if code_tables is None:
        return {"forms": forms, "codes": None, "longest": longest}
    resolved: dict[tuple, set] = {}
    for r in code_tables:
        lg = r["league"]
        if lg not in teams:
            continue
        for e in _list_body(r["raw"]):
            pair = _event_codes(e)
            if pair is None:
                continue
            (c1, n1), (c2, n2) = pair
            s1, s2 = _code_teams(c1, n1, teams[lg]), _code_teams(c2, n2, teams[lg])
            fit = [(x, y) for x in sorted(s1) for y in sorted(s2) if frozenset((x, y)) in played[lg]]
            if len(fit) == 1:
                s1, s2 = {fit[0][0]}, {fit[0][1]}
            for code, hits in ((c1, s1), (c2, s2)):
                resolved.setdefault((lg, code), set()).update(hits)
    codes: dict[str, dict] = {lg: {} for lg in LEAGUES}
    for (lg, code), hits in sorted(resolved.items()):
        codes[lg][code] = frozenset((lg, n) for n in hits) if hits else frozenset({("code", lg, code)})
    return {"forms": forms, "codes": codes, "longest": longest}


def _named(text: str, leagues: Sequence[str], index: dict, line=None) -> tuple[dict, dict, list[str]]:
    """({team: how fully}, {team: first word index}, words) for one text. A form standing inside a longer form of
    another team is not counted ("Blue Devils" names Duke, not New Jersey). A code counts (rank 1) only as a whole word
    in capitals, from complete tables, when its owners in `leagues` are one known team, and not in a text written wholly
    in capitals that holds another word (`_another_word`, clarification 20); a code that is also an English word only
    where a pick puts its team."""
    s = _normal(text)
    spans = _spans(text)
    low = [w for w, _, _, _ in spans]
    letters = [c for c in s if c.isalpha()]
    all_caps = bool(letters) and all(c.isupper() for c in letters) and (
        index["codes"] is None or _another_word(s, index["codes"]))
    english = _english_words() if index["codes"] is not None and not all_caps else frozenset()
    hits = []
    for i in range(len(low)):
        for n in range(1, min(index["longest"], len(low) - i) + 1):
            f = tuple(low[i:i + n])
            for lg in leagues:
                for key, rank in index["forms"][lg].get(f, ()):
                    hits.append((i, i + n, key, rank))
        _, raw, a, b = spans[i]
        if index["codes"] is None or all_caps or not raw.isupper() or not CODE_FORM.match(raw):
            continue
        if raw.lower() in english and not _where_a_pick_puts_its_team(s, a, b, raw, line):
            continue
        owners = frozenset().union(*(index["codes"][lg].get(raw, frozenset()) for lg in leagues))
        if len(owners) == 1 and len(next(iter(owners))) == 2:
            hits.append((i, i + 1, next(iter(owners)), 1))
    ranks: dict = {}
    first: dict = {}
    for h in hits:
        if any(o[2] != h[2] and o[0] <= h[0] and h[1] <= o[1] and o[1] - o[0] > h[1] - h[0] for o in hits):
            continue
        ranks[h[2]] = max(ranks.get(h[2], 0), h[3])
        first[h[2]] = min(first.get(h[2], h[0]), h[0])
    return ranks, first, low


def named_teams(text: str, leagues: Sequence[str], index: dict, line=None) -> dict:
    """The known teams of `leagues` that `text` names, each with how fully (2: the whole name; 1: a city, a nickname
    or a Kalshi code, see `_named`)."""
    return _named(text, leagues, index, line)[0]


def _plays(key: tuple, g: dict) -> bool:
    return len(key) == 2 and key[0] == g["league"] and key[1] in (g["home"], g["away"])


def _resolve(pieces: Sequence[str], market: str, leagues: Sequence[str], window: Sequence[dict], index: dict,
             line=None) -> dict:
    """One version's words (the selection first, then a total's two sides) against every known team, then against the
    games of the window."""
    named: dict = {}
    for text in pieces:
        for k, r in _named(text, leagues, index, line)[0].items():
            named[k] = max(named.get(k, 0), r)
    if not named:
        return {"reason": "no_team_named"}
    top = max(named.values())
    best = sorted(k for k, r in named.items() if r == top)
    if market != "total":
        if len(best) == 2:
            together = [g for g in window if all(_plays(k, g) for k in best)]
            return {"reason": "side_unclear" if together else "name_shared"}
        if len(best) > 1:
            return {"reason": "name_shared"}
        key = best[0]
        games = [g for g in window if _plays(key, g)]
        if not games:
            return {"reason": "no_game_in_window"}
        if len(games) > 1:
            return {"reason": "more_than_one_game"}
        g = games[0]
        side = "home" if g["home"] == key[1] else "away"
        if (g["league"], g["away" if side == "home" else "home"]) in named:
            return {"reason": "side_unclear"}
        if any(not _plays(k, g) for k in named):
            return {"reason": "teams_of_two_games"}
        _, first, low = _named(pieces[0], leagues, index, line)
        if key in first and any(w in AGAINST_WORDS for w in low[:first[key]]):
            return {"reason": "bets_against"}
        return {"game": g, "side": side}
    if len(best) > 2:
        return {"reason": "name_shared"}
    games = [g for g in window if any(_plays(k, g) for k in best)]
    if not games:
        return {"reason": "no_game_in_window"}
    if len(games) > 1 or not all(_plays(k, games[0]) for k in best):
        return {"reason": "more_than_one_game"}
    if any(not _plays(k, games[0]) for k in named):
        return {"reason": "teams_of_two_games"}
    return {"game": games[0], "side": None}


def match_text(fields: dict, window: Sequence[dict], index: dict) -> dict:
    """One version of a pick against the games of its window: {"game", "side"} (side None for a total) or {"reason"}.
    Moneyline and spread are read from the selection, a total from the selection and both sides of the event. A pick
    names team T only when no other known team (`name_index`) of the leagues its league field allows is named at least
    as fully; T must then play exactly one game of the window. For a moneyline or a spread T's opponent must not be
    named and no word of `AGAINST_WORDS` may stand before T; for a total the one or two teams named most fully must be
    of one game of the window. Any other team named must play in that same game."""
    market = fields.get("market")
    if not market:
        return {"reason": "no_market"}
    if market not in GRADED_MARKETS:
        return {"reason": "market_not_graded", "market": market}
    leagues = leagues_named(fields.get("league"))
    if leagues is None:
        return {"reason": "league_not_graded", "market": market}
    sel = fields.get("selection")
    try:
        line = float(fields["line"]) if fields.get("line") is not None else None
    except (TypeError, ValueError):
        line = None
    pieces = [str(sel or "")]
    if market == "total":
        ev = fields.get("event") if isinstance(fields.get("event"), dict) else {}
        pieces += [str(x) for x in (ev.get("side_a"), ev.get("side_b")) if x]
    if any(p.strip() for p in pieces):
        out = _resolve(pieces, market, leagues, window, index, line)
    else:
        out = {"reason": "no_selection"}
    out["market"] = market
    return out


_OU_WORD = re.compile(r"(?<![a-z0-9])(over|under)(?![a-z0-9])")
_OU_JOINED = re.compile(r"(?<![a-z0-9])([ou])(\d+(?:\.\d+)?)(?!\.?\d)(?=$|\s|[.,;:!?)])")


def over_under(selection, line) -> str | None:
    """"over" or "under" from a total's selection: the whole word, or o / u joined to the line ("o221.5") and
    followed by the end of the text, whitespace or one of . , ; : ! ? ) ("o45x" is not a total, "u221." is); None when
    neither or both stand."""
    s = str(selection or "").lower()
    found = {m.group(1) for m in _OU_WORD.finditer(s)}
    for m in _OU_JOINED.finditer(s):
        if line is not None and abs(float(m.group(2)) - abs(float(line))) < 1e-9:
            found.add("over" if m.group(1) == "o" else "under")
    return found.pop() if len(found) == 1 else None


def schedule_games(lists: Iterable[dict]) -> list[dict]:
    """Games from the stored events lists ({league, as_of, raw}). A game's start is the earliest commence_time any
    list gives for it, whenever that list was fetched; it is known only when a list fetched before that start held the
    game; lists that differ on it by more than 30 minutes flag it. Names and league come from the latest list."""
    obs: dict[str, list[tuple]] = {}
    for r in lists:
        try:
            a = _ts(r["as_of"])
        except (TypeError, ValueError):
            continue
        for e in _list_body(r["raw"]):
            if not e.get("id") or not e.get("commence_time"):
                continue
            try:
                c = _ts(e["commence_time"])
            except (TypeError, ValueError):
                continue
            obs.setdefault(str(e["id"]), []).append((a, c, r["league"], e.get("home_team"), e.get("away_team")))
    out = []
    for gid, ob in sorted(obs.items()):
        start = min(o[1] for o in ob)
        last = max(ob, key=lambda o: (o[0], o[1]))
        out.append({"id": gid, "league": last[2], "home": last[3], "away": last[4], "start": _from_ts(start),
                    "start_ts": start, "start_known": any(o[0] < start for o in ob),
                    "flagged": max(o[1] for o in ob) - start > START_SPREAD, "day": sl.eastern_date(_from_ts(start))})
    return out


def _counted(vm: dict, parse: dict | None) -> bool:
    return (vm.get("role") == "user" and not vm.get("deleted") and parse is not None and parse.get("status") == "ok"
            and parse.get("is_pick") == 1)


def evaluate_comment(picks, cid: str, first_seen: str, vmeta: Sequence[dict], parses: dict, fields_of: Callable,
                     wend: str, games: Sequence[dict], index: dict) -> dict:
    """One counted pick: matched to a game or not graded, with one reason. The window holds the games whose start
    (`schedule_games`: the earliest any list gives) falls after the comment was first seen and before `wend`. Each
    version seen before `wend` that is a counted pick is read with `version_before(comment, next version's first-seen
    time)` and matched by `match_text`; the pick is the first such version whose game's start has that very version as
    `version_before(comment, start)`: text first seen at or after a game's start is never used for it, and an edit
    seen after the start is ignored. `known_at` is the first-seen time of the earliest version from which every
    version up to the one used names the same game and side."""
    first_ts, wend_ts = _ts(first_seen), _ts(wend)
    window = [g for g in games if first_ts < g["start_ts"] < wend_ts]
    vs = sorted((v for v in vmeta if v["seen_at"] < wend), key=lambda v: v["version"])
    outcomes: list[dict | None] = []
    for i, vm in enumerate(vs):
        parse = parses.get(vm["id"])
        if not _counted(vm, parse):
            outcomes.append(None)
            continue
        v = picks.version_before(cid, vs[i + 1]["seen_at"] if i + 1 < len(vs) else wend)
        if v is None or v["id"] != vm["id"]:
            outcomes.append(None)
            continue
        o = match_text(fields_of(parse["id"]), window, index)
        o.update(version_id=v["id"], version=v["version"], seen_at=vm["seen_at"], decimal_odds=parse.get("decimal_odds"),
                 fields=fields_of(parse["id"]))
        outcomes.append(o)
    first = next((i for i, o in enumerate(outcomes) if o is not None), None)
    if first is None:
        return {"status": "not_graded", "reason": "version_not_a_pick"}
    for i in range(first, len(vs)):
        o = outcomes[i]
        if o is None or "game" not in o:
            continue
        g = o["game"]
        at_start = picks.version_before(cid, g["start"])
        if at_start is None or at_start["id"] != o["version_id"]:
            continue
        j = i
        while j - 1 >= first and outcomes[j - 1] is not None and "game" in outcomes[j - 1] and \
                outcomes[j - 1]["game"]["id"] == g["id"] and outcomes[j - 1]["side"] == o["side"]:
            j -= 1
        row = _matched_row(o, g, vs[j]["seen_at"])
        if not g["start_known"]:
            return dict(row, status="not_graded", reason="no_start_known")
        if g["flagged"]:
            return dict(row, status="not_graded", reason="start_disagrees")
        return row
    o = outcomes[first]
    if "game" not in o:
        return {"status": "not_graded", "reason": o["reason"], "market": o.get("market"), "version_id": o["version_id"],
                "version": o["version"]}
    g = o["game"]
    base = {"status": "not_graded", "market": o.get("market"), "game_id": g["id"], "league": g["league"],
            "start": g["start"], "version_id": o["version_id"], "version": o["version"]}
    if g["start_ts"] <= _ts(vs[first]["seen_at"]):
        return dict(base, reason="pick_seen_after_start")
    at_start = picks.version_before(cid, g["start"])
    if at_start is None:
        return dict(base, reason="no_text_before_start")
    k = next((i for i, vm in enumerate(vs) if vm["id"] == at_start["id"]), None)
    if k is None or outcomes[k] is None:
        return dict(base, reason="version_not_a_pick")
    return dict(base, reason="version_names_other_team")


def _matched_row(o: dict, g: dict, known_at: str) -> dict:
    fields = o.get("fields") or {}
    try:
        line = float(fields["line"]) if fields.get("line") is not None else None
    except (TypeError, ValueError):
        line = None
    side = o["side"]
    return {"status": "matched", "reason": None, "game_id": g["id"], "league": g["league"], "side": side,
            "team": g[side] if side else None, "market": o["market"], "line": line,
            "ou": over_under(fields.get("selection"), line) if o["market"] == "total" else None,
            "version_id": o["version_id"], "version": o["version"], "decimal_odds": o.get("decimal_odds"),
            "known_at": known_at, "start": g["start"]}


# ---------------------------------------------------------------- the fingerprints: everything a command can reach

_MATCH_ROOTS = ("_cli", "main", "cmd_match")    # a match run, from the entry point (`__main__` calls `_cli`) down
_NOT_A_MATCH_RUN = ("cmd_schedule", "cmd_audit_sample", "cmd_audit_pass", "cmd_prices", "cmd_results", "cmd_evaluate",
                    "cmd_choose", "cmd_daily", "status")          # the other commands `main` dispatches to
_SCORING_ROOTS = ("cmd_prices", "cmd_results", "cmd_evaluate")
_MISSING = object()


def _plain(v):
    """A constant's value as the fingerprint holds it: sets sorted, paths and other objects by type name only (so the
    fingerprint does not depend on where the repository lies)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted((_plain(x) for x in v), key=_canon)
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (date, datetime, timedelta, timezone)):
        return str(v)
    if isinstance(v, ZoneInfo):
        return str(v.key)
    if isinstance(v, re.Pattern):
        return [v.pattern, v.flags]
    return type(v).__name__


def _members(cls: type, label: str) -> Iterable[tuple[str, str, Callable]]:
    for name, v in vars(cls).items():
        f = v.fget if isinstance(v, property) else v.__func__ if isinstance(v, (staticmethod, classmethod)) else v
        if inspect.isfunction(inspect.unwrap(f)):
            yield name, "%s.%s.%s" % (label, cls.__name__, name), f


def _parsed(fn: Callable) -> tuple[ast.AST | None, str]:
    """A function's parsed code, docstrings removed: (tree, dump). Comments, blank lines and docstrings do not reach
    the dump; every executed statement, name and constant does. A function without source (generated) dumps its
    name only."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(fn))))
    except (OSError, TypeError):
        return None, "no source: %s" % getattr(fn, "__qualname__", "?")
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and body and \
                isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and \
                isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return tree, ast.dump(tree, annotate_fields=True, include_attributes=False)


def _scope(roots: Sequence[str], wide: bool, skip: Sequence[str] = ()) -> dict:
    """Every function and method the `roots` can reach, found from the parsed code: module-level names (not those in
    `skip`), the special methods (`__init__`...) of a class named, methods of this module's classes and of
    `pick_lists.Store` by attribute name (`wide`: also of the classes of sports_lines, kalshi_history, kalshi_data and
    odds_api), the helpers borrowed from those modules (and what they reach there), and every module-level constant on
    the way. A module's own names (`__name__`, `__file__`...) are left out: they say how the module was started, not
    what it does."""
    mods = {"pg": (globals(), __name__), "sl": (vars(sl), sl.__name__), "pl": (vars(pl), pl.__name__),
            "kh": (vars(kh), kh.__name__)}
    if wide:
        mods.update({"kd": (vars(kd), kd.__name__), "oa": (vars(oa), oa.__name__)})
    by_module = {mn: label for label, (_, mn) in mods.items()}
    methods: dict[str, list[tuple[str, Callable, str]]] = {}
    classes = [(v, "pg") for v in globals().values() if isinstance(v, type) and v.__module__ == __name__]
    classes.append((pl.Store, "pl"))
    if wide:
        for label in ("sl", "kh", "kd", "oa"):
            ns, mn = mods[label]
            classes += [(v, label) for v in ns.values() if isinstance(v, type) and v.__module__ == mn]
    for cls, label in classes:
        for name, qual, f in _members(cls, label):
            methods.setdefault(name, []).append((qual, f, label))
    funcs: dict[str, str] = {}
    consts: dict[str, Any] = {}
    todo: list[tuple[str, Callable, str]] = [("pg." + r, globals()[r], "pg") for r in roots]

    def take(label: str, name: str, v) -> None:
        if name.startswith("__") and name.endswith("__"):
            return
        f = inspect.unwrap(v) if callable(v) else v
        if inspect.isfunction(f) and f.__module__ == mods[label][1]:
            todo.append(("%s.%s" % (label, name), f, label))
        elif isinstance(v, type) and v.__module__ == mods[label][1]:
            todo.extend((qual, m, label) for n, qual, m in _members(v, label) if n.startswith("__") and n.endswith("__"))
        elif not callable(v) and not isinstance(v, types.ModuleType):
            consts["%s.%s" % (label, name)] = _plain(v)
    while todo:
        qual, fn, label = todo.pop()
        if qual in funcs:
            continue
        tree, funcs[qual] = _parsed(fn)
        if tree is None:
            continue
        ns = mods[label][0]
        aliases = {n: by_module[v.__name__] for n, v in ns.items()
                   if isinstance(v, types.ModuleType) and v.__name__ in by_module}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                v = ns.get(node.id, _MISSING)
                if v is not _MISSING and not (label == "pg" and node.id in skip):
                    take(label, node.id, v)
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id in aliases:
                    other = aliases[node.value.id]
                    v = mods[other][0].get(node.attr, _MISSING)
                    if v is not _MISSING:
                        take(other, node.attr, v)
                todo.extend(methods.get(node.attr, ()))
    consts["pg._SCHEMA"] = _plain(_SCHEMA)                          # the store's tables
    consts["pg._KEYS"] = _plain(_KEYS)
    return {"functions": sorted(funcs), "constants": sorted(consts), "sources": funcs, "values": consts}


def fingerprint_scope() -> dict:
    """What the matching fingerprint covers: everything a `match` run can reach from the entry point down (`_cli`,
    `main` with the store and grader set-up, `cmd_match`), not the other commands (see `_scope`)."""
    return _scope(_MATCH_ROOTS, wide=False, skip=_NOT_A_MATCH_RUN)


def scoring_scope() -> dict:
    """What the scoring fingerprint covers: everything `prices`, `results` and `evaluate` can reach."""
    return _scope(_SCORING_ROOTS, wide=True)


def fingerprint() -> str:
    """The matching rule's fingerprint: sha256 over the parsed code of every function `fingerprint_scope` finds, the
    values of the constants they read, the store's tables and the English word list the codes are judged by."""
    s = fingerprint_scope()
    return _sha(_canon({"code": s["sources"], "constants": s["values"], "words": _words_sha(_english_words())}))


@lru_cache(maxsize=4)
def _words_sha(words: frozenset) -> str:
    return _sha("\n".join(sorted(words)))


def scoring_fingerprint() -> str:
    """The scoring code's fingerprint (same kind as `fingerprint`), pinned by `choose`."""
    s = scoring_scope()
    return _sha(_canon({"code": s["sources"], "constants": s["values"]}))


def audit_seed(fp: str) -> int:
    """The seed of the audit's draw for a fingerprint: from the fingerprint, never chosen by the reader."""
    return int(_sha("pick-audit-draw:" + fp)[:12], 16)


# ---------------------------------------------------------------- the grading store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS layout (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, at TEXT NOT NULL, command TEXT NOT NULL,
    fingerprint TEXT NOT NULL, lists_upto INTEGER NOT NULL, codes_upto INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY, league TEXT NOT NULL, sport TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('live', 'historical')), asked TEXT, as_of TEXT NOT NULL, fetched_at TEXT NOT NULL,
    sha256 TEXT NOT NULL, raw TEXT NOT NULL, UNIQUE (league, kind, as_of, sha256));
CREATE TABLE IF NOT EXISTS bound (id INTEGER PRIMARY KEY CHECK (id = 1), identity TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS code_tables (id INTEGER PRIMARY KEY, league TEXT NOT NULL, series TEXT NOT NULL,
    fetched_at TEXT NOT NULL, sha256 TEXT NOT NULL, raw TEXT NOT NULL, UNIQUE (league, sha256));
CREATE TABLE IF NOT EXISTS code_fetches (id INTEGER PRIMARY KEY, league TEXT NOT NULL, fetched_at TEXT NOT NULL,
    table_id INTEGER NOT NULL, events INTEGER NOT NULL, requests INTEGER NOT NULL, UNIQUE (league, fetched_at));
CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, comment_id TEXT NOT NULL,
    thread_id TEXT NOT NULL, thread_date TEXT, author_key TEXT, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
    reason TEXT, league TEXT, game_id TEXT, side TEXT, team TEXT, market TEXT, line REAL, ou TEXT, version_id INTEGER,
    version INTEGER, decimal_odds REAL, known_at TEXT, start TEXT, at TEXT NOT NULL, UNIQUE (run_id, comment_id));
CREATE TABLE IF NOT EXISTS audits (id INTEGER PRIMARY KEY, at TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
    seed INTEGER NOT NULL, n INTEGER NOT NULL, keys TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_passes (id INTEGER PRIMARY KEY, at TEXT NOT NULL, audit_id INTEGER NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL, wrong INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS frozen (id INTEGER PRIMARY KEY CHECK (id = 1), fingerprint TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS result_reads (id INTEGER PRIMARY KEY, at TEXT NOT NULL, command TEXT NOT NULL,
    fingerprint TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS kalshi_pairs (game_id TEXT PRIMARY KEY, kalshi_event TEXT NOT NULL UNIQUE, league TEXT,
    day TEXT, home_ticker TEXT NOT NULL, away_ticker TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scores (id INTEGER PRIMARY KEY, league TEXT NOT NULL, fetched_at TEXT NOT NULL,
    sha256 TEXT NOT NULL, raw TEXT NOT NULL, UNIQUE (league, fetched_at, sha256));
CREATE TABLE IF NOT EXISTS choice (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL, at TEXT NOT NULL,
    scoring_fingerprint TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_results (id INTEGER PRIMARY KEY CHECK (id = 1), at TEXT NOT NULL, period_end TEXT NOT NULL,
    raw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS errors (id INTEGER PRIMARY KEY, at TEXT NOT NULL, command TEXT NOT NULL, league TEXT,
    kind TEXT NOT NULL, detail TEXT);
"""
_KEYS = {"layout": [("id",)], "runs": [("id",)], "schedules": [("id",), ("league", "kind", "as_of", "sha256")],
         "bound": [("id",)], "code_tables": [("id",), ("league", "sha256")],
         "code_fetches": [("id",), ("league", "fetched_at")], "matches": [("id",), ("run_id", "comment_id")],
         "audits": [("id",), ("fingerprint",)], "audit_passes": [("id",), ("audit_id",)], "frozen": [("id",)],
         "result_reads": [("id",)], "kalshi_pairs": [("game_id",), ("kalshi_event",)],
         "scores": [("id",), ("league", "fetched_at", "sha256")], "choice": [("id",)], "test_results": [("id",)],
         "errors": [("id",)]}
_TIMES = (("runs", "at"), ("schedules", "fetched_at"), ("bound", "at"), ("code_tables", "fetched_at"),
          ("code_fetches", "fetched_at"), ("matches", "at"),
          ("audits", "at"), ("audit_passes", "at"), ("frozen", "at"), ("result_reads", "at"), ("kalshi_pairs", "at"),
          ("scores", "fetched_at"), ("choice", "at"), ("test_results", "at"), ("errors", "at"))


def _triggers() -> str:
    out = []
    for t, keys in _KEYS.items():
        for verb in ("UPDATE", "DELETE"):
            out.append(f"CREATE TRIGGER IF NOT EXISTS no_{verb.lower()}_{t} BEFORE {verb} ON {t} BEGIN "
                       f"SELECT RAISE(ABORT, 'append-only record: no {verb} on {t}'); END;")
        cond = " OR ".join("(NEW.%s IS NOT NULL AND EXISTS (SELECT 1 FROM %s WHERE %s))"
                           % (k[0], t, " AND ".join(f"{c} = NEW.{c}" for c in k)) for k in keys)
        out.append(f"CREATE TRIGGER IF NOT EXISTS no_replace_{t} BEFORE INSERT ON {t} WHEN {cond} BEGIN "
                   f"SELECT RAISE(ABORT, 'append-only record: {t} already has this key'); END;")
    return "\n".join(out)


class GradingStore:
    """This module's own sqlite store: insert-only (triggers refuse UPDATE, DELETE and a repeated natural key), every
    write in one transaction that first compares its time with the latest time already stored (`writing`). A file
    holding tables but not this layout's mark (a rev-1 store, layout 1) is refused and left as it is."""

    def __init__(self, path: str | Path, readonly: bool = False):
        self.path = Path(path)
        if readonly:
            if not self.path.is_file():
                raise Refused(f"no grading store at {self.path}")
            self.conn = sqlite3.connect("file:" + urllib.parse.quote(str(self.path.resolve())) + "?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path), timeout=30)
        try:
            self._layout(create=not readonly)
        except BaseException:
            self.conn.close()
            raise
        self.conn.row_factory = sqlite3.Row

    def _layout(self, create: bool) -> None:
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not tables and create:
            self.conn.executescript(_SCHEMA + _triggers())
            self.conn.execute("INSERT INTO layout (id, version) VALUES (1, ?)", (LAYOUT,))
            self.conn.commit()
            return
        row = self.conn.execute("SELECT version FROM layout WHERE id = 1").fetchone() if "layout" in tables else None
        if row is None or row[0] != LAYOUT:
            raise Refused(f"{self.path} is a grading store of another layout (this code reads layout {LAYOUT}): set it "
                          "aside; nothing was read or written")
        if create:
            self.conn.executescript(_SCHEMA + _triggers())
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def rows(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args)]

    def one(self, sql: str, args=()):
        r = self.conn.execute(sql, args).fetchone()
        return None if r is None else dict(r)

    def latest_time(self) -> str | None:
        q = " UNION ALL ".join(f"SELECT MAX({c}) AS t FROM {t}" for t, c in _TIMES)
        return self.conn.execute(f"SELECT MAX(t) FROM ({q})").fetchone()[0]

    def refuse_behind(self, now: str) -> None:
        latest = self.latest_time()
        if latest and now < latest:
            raise ClockBehind(f"the clock reads {now}, earlier than the latest time in the grading store ({latest}): "
                              "nothing was written")

    @contextlib.contextmanager
    def writing(self, now: str):
        if self.conn.in_transaction:
            raise RuntimeError("a write transaction is already open")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.refuse_behind(now)
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    # reads used by several commands
    def frozen(self) -> dict | None:
        return self.one("SELECT fingerprint, at FROM frozen WHERE id = 1")

    def choice(self) -> int | None:
        r = self.one("SELECT version FROM choice WHERE id = 1")
        return None if r is None else int(r["version"])

    def closed(self, fp: str) -> bool:
        """The reading under `fp` found more than 2 wrong: that rule is closed for good."""
        return self.one("SELECT 1 AS x FROM audit_passes WHERE fingerprint = ? AND wrong > ?",
                        (fp, AUDIT_MAX_WRONG)) is not None

    def gate_open(self, fp: str) -> bool:
        return not self.closed(fp) and self.one(
            "SELECT 1 AS x FROM audit_passes p JOIN audits a ON a.id = p.audit_id WHERE a.fingerprint = ? "
            "AND p.fingerprint = ? AND p.wrong <= ?", (fp, fp, AUDIT_MAX_WRONG)) is not None

    def latest_matches(self, fp: str) -> list[dict]:
        """The current row of every pick matched under the fingerprint `fp` (the latest one written)."""
        return self.rows("SELECT * FROM matches WHERE id IN (SELECT MAX(id) FROM matches WHERE fingerprint = ? "
                         "GROUP BY comment_id) ORDER BY thread_date, thread_id, comment_id", (fp,))

    def pairs(self) -> dict[str, dict]:
        return {r["game_id"]: r for r in self.rows("SELECT * FROM kalshi_pairs")}

    def lists(self) -> list[dict]:
        return self.rows("SELECT league, as_of, raw FROM schedules ORDER BY id")

    def _latest_code_fetches(self, now: str | None = None) -> list[dict]:
        """Each league's latest code table fetch (at or before `now`): league, time, events and the table's raw."""
        cond, args = ("WHERE fetched_at <= ? ", (now,)) if now is not None else ("", ())
        return self.rows("SELECT f.league, f.fetched_at, f.events, t.raw FROM code_fetches f JOIN code_tables t ON "
                         "t.id = f.table_id WHERE f.id IN (SELECT MAX(id) FROM code_fetches %sGROUP BY league) "
                         "ORDER BY f.league" % cond, args)

    def code_tables(self, now: str) -> list[dict] | None:
        """Every stored code table ({league, raw}), when every league's latest table was fetched within `CODE_FRESH`
        of `now` and every event of it gave two readable codes (clarification 21); None otherwise (the codes are then
        names of nothing)."""
        since = _from_ts(_ts(now) - CODE_FRESH)
        complete = {r["league"] for r in self._latest_code_fetches(now)
                    if r["fetched_at"] >= since and _unreadable(_list_body(r["raw"])) == 0}
        if not set(LEAGUES) <= complete:
            return None
        return self.rows("SELECT league, raw FROM code_tables ORDER BY id")

    def code_readability(self) -> dict[str, dict]:
        """Each league's latest code table fetch: its time, events and how many of them gave no two readable codes."""
        return {r["league"]: {"fetched_at": r["fetched_at"], "events": r["events"],
                              "unreadable": _unreadable(_list_body(r["raw"]))} for r in self._latest_code_fetches()}

    def list_ids(self) -> tuple[int, int]:
        """The latest schedule list and code table fetch stored (0 for none)."""
        a = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM schedules").fetchone()[0]
        b = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM code_fetches").fetchone()[0]
        return int(a), int(b)

    def bound(self) -> str | None:
        r = self.one("SELECT identity FROM bound WHERE id = 1")
        return None if r is None else r["identity"]

    def scoring_pin(self) -> str | None:
        r = self.one("SELECT scoring_fingerprint FROM choice WHERE id = 1")
        return None if r is None else r["scoring_fingerprint"]


class _KalshiView:
    """Kalshi's stored markets and candles (kalshi_history's store) read only; an absent file reads as empty."""

    def __init__(self, path: Path):
        self.conn = None
        if Path(path).is_file():
            self.conn = sqlite3.connect("file:" + urllib.parse.quote(str(Path(path).resolve())) + "?mode=ro", uri=True)

    def _rows(self, sql: str, args=()) -> list:
        if self.conn is None:
            return []
        try:
            return self.conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []

    def markets(self) -> list[dict]:
        return [dict(json.loads(raw), tier=tier, series=series)
                for raw, tier, series in self._rows("SELECT raw, tier, series FROM markets ORDER BY ticker")]

    def candles(self, ticker: str) -> list[dict]:
        return [kd.normalize_candle(json.loads(raw))
                for (raw,) in self._rows("SELECT raw FROM candles WHERE ticker = ? ORDER BY end_ts", (ticker,))]

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


# ---------------------------------------------------------------- the context of one command

class Grader:
    def __init__(self, root: Path, picks_path: Path, clock: Callable[[], datetime], odds_client=None,
                 kalshi_client=None, monthly_cap: int = MONTHLY_CAP, command: str = ""):
        self.root, self.picks_path, self.clock, self.command = Path(root), Path(picks_path), clock, command
        self._odds, self._kalshi = odds_client, kalshi_client
        self.monthly_cap = int(monthly_cap)
        self.store = GradingStore(self.root / STORE_FILE)
        self.fp = fingerprint()
        self._ledger = self._kstore = None
        self.odds_stopped = False

    # lazily opened parts
    @property
    def odds(self) -> oa.OddsApiClient:
        if self._odds is None:
            self._odds = oa.OddsApiClient(max_retries=0)
        return self._odds

    @property
    def kalshi(self) -> kh.HistoryClient:
        if self._kalshi is None:
            self._kalshi = kh.HistoryClient()
        return self._kalshi

    @property
    def ledger(self) -> sl.CreditLedger:
        if self._ledger is None:
            self._ledger = sl.CreditLedger(self.root / "credits.sqlite", monthly_cap=self.monthly_cap,
                                           free_monthly_cap=self.monthly_cap, clock=self.clock)
        return self._ledger

    @property
    def kstore(self) -> kh.HistoryStore:
        if self._kstore is None:
            self._kstore = kh.HistoryStore(self.root / "kalshi.sqlite")
        return self._kstore

    def now(self) -> str:
        return _iso(self.clock())

    def close(self) -> None:
        for s in (self.store, self._kstore):
            if s is not None:
                s.close()
        if self._ledger is not None:
            self._ledger.db.close()

    def picks(self) -> pl.Store:
        return pl.Store(self.picks_path, readonly=True, create=False)

    def error(self, league: str | None, exc: BaseException) -> None:
        detail = str(exc)[:200] if isinstance(exc, (oa.OddsApiError, sl.CreditCapError, sl.LedgerStop, Refused,
                                                     kh.KalshiHistoryError)) else ""
        now = self.now()
        with self.store.writing(now) as c:
            c.execute("INSERT INTO errors (at, command, league, kind, detail) VALUES (?, ?, ?, ?, ?)",
                      (now, self.command, league, type(exc).__name__, detail))

    def run_row(self, c, now: str, upto: tuple[int, int]) -> int:
        return c.execute("INSERT INTO runs (at, command, fingerprint, lists_upto, codes_upto) VALUES (?, ?, ?, ?, ?)",
                         (now, self.command, self.fp, upto[0], upto[1])).lastrowid

    def odds_call(self, key: str, rule: str, cost: int, path: str, params: dict, send: Callable,
                  empty: Callable = lambda body: False):
        with self.ledger.lock():
            return self.ledger.attempt(key, rule, cost, NO_RUN_CAP, path, params, send, self.odds,
                                       request_key=_canon({"path": path, "params": params}), empty=empty)

    def need_gate(self) -> None:
        if self.store.closed(self.fp):
            raise Refused("the audit of the current matching fingerprint %s found more than %d wrong: that rule is "
                          "closed for good (audit-pass); only a changed rule gets a new reading"
                          % (self.fp[:12], AUDIT_MAX_WRONG))
        if not self.store.gate_open(self.fp):
            raise Refused("no result is read before an audit-pass with at most %d wrong exists for the current matching "
                          "fingerprint %s" % (AUDIT_MAX_WRONG, self.fp[:12]))

    def need_current(self) -> None:
        """The matches in use are those of a `match` run after the latest stored list, under the current rule."""
        run = self.store.one("SELECT lists_upto, codes_upto FROM runs WHERE fingerprint = ? ORDER BY id DESC LIMIT 1",
                             (self.fp,))
        if run is None or (run["lists_upto"], run["codes_upto"]) != self.store.list_ids():
            raise Refused("a schedule or Kalshi code list was stored after the last `match` run under the current "
                          "fingerprint: run `match` first")

    def reading_results(self, detail: dict) -> None:
        """Called before a command reads a result: freezes the fingerprint (first time) and records the read."""
        self.need_gate()
        self.need_current()
        now = self.now()
        with self.store.writing(now) as c:
            if self.store.frozen() is None:
                c.execute("INSERT INTO frozen (id, fingerprint, at) VALUES (1, ?, ?)", (self.fp, now))
            c.execute("INSERT INTO result_reads (at, command, fingerprint, detail) VALUES (?, ?, ?, ?)",
                      (now, self.command, self.fp, _canon(detail)))

    def scoring(self, kind: str, payload: dict) -> None:
        payload = dict(payload, grading_sha256=sl._sha256(self.store.path), fingerprint=self.fp)
        sl.append_scoring(self.root, kind, payload, self.clock)


@contextlib.contextmanager
def _no_retry(client):
    old = getattr(client, "max_retries", None)
    if old is not None:
        client.max_retries = 0
    try:
        yield
    finally:
        if old is not None:
            client.max_retries = old


# ---------------------------------------------------------------- schedule

def _kalshi_codes(gr: Grader) -> dict:
    """The table of team codes of each of the five game series, from Kalshi's events list of every status (events
    with a market closing after now minus `CODE_LOOKBACK`; an event carries its title and no result, and no market is
    read), each event kept as event ticker, title and sub-title only. At most `CODE_REQUESTS` requests a run; a league
    whose listing fails or does not end within them gets no table this run (recorded; the others go on). Every event
    listed is kept (one without a ticker too), so a table holds what was listed and its unreadable events can be
    counted (`_unreadable`). A table is stored once by content, and every fetch whose listing ended is recorded with its
    time (`code_fetches`)."""
    out: dict[str, Any] = {}
    client = gr.kalshi
    old = client.max_requests
    client.max_requests = client.requests + CODE_REQUESTS
    since = int(gr.clock().timestamp()) - CODE_LOOKBACK
    try:
        for league, (_, series) in LEAGUES.items():
            before = client.requests
            try:
                rows = client.pages("/events", {"series_ticker": series, "limit": 200, "min_close_ts": since}, "events")
            except (kh.KalshiHistoryError, kd.HttpError, OSError) as exc:
                gr.error(league, exc)
                out[league] = "error: %s" % type(exc).__name__
                continue
            kept, odd = {}, []
            for e in rows:
                row = {k: (e.get(k) if isinstance(e, dict) else None) for k in ("event_ticker", "title", "sub_title")}
                if row["event_ticker"]:
                    kept[str(row["event_ticker"])] = row
                else:
                    odd.append(row)
            events = [kept[k] for k in sorted(kept)] + sorted(odd, key=_canon)
            raw, now = _canon(events), gr.now()
            with gr.store.writing(now) as c:
                row = c.execute("SELECT id FROM code_tables WHERE league = ? AND sha256 = ?",
                                (league, _sha(raw))).fetchone()
                tid = row[0] if row else c.execute(
                    "INSERT INTO code_tables (league, series, fetched_at, sha256, raw) VALUES (?, ?, ?, ?, ?)",
                    (league, series, now, _sha(raw), raw)).lastrowid
                if c.execute("SELECT 1 FROM code_fetches WHERE league = ? AND fetched_at = ?",
                             (league, now)).fetchone() is None:
                    c.execute("INSERT INTO code_fetches (league, fetched_at, table_id, events, requests) "
                              "VALUES (?, ?, ?, ?, ?)", (league, now, tid, len(events), client.requests - before))
            out[league] = {"events": len(events), "unreadable": _unreadable(events)}
    finally:
        client.max_requests = old
    return out


def cmd_schedule(gr: Grader, day: date | None = None, hist_key: str = "paid") -> dict:
    out: dict[str, Any] = {}
    for league, (sport, _) in LEAGUES.items():
        if gr.odds_stopped:
            out[league] = "skipped: the ledger stopped the run"
            continue
        try:
            if day is None:
                path, params = f"/v4/sports/{sport}/events", {"dateFormat": "iso"}
                body = gr.odds_call("free", "events", 0, path, params,
                                    lambda s=sport: gr.odds.events(s, key="free", retries=0))
                kind, asked, as_of = "live", None, None
            else:
                asked = day.isoformat()
                if gr.store.one("SELECT 1 AS x FROM schedules WHERE league = ? AND kind = 'historical' AND asked = ?",
                                (league, asked)):
                    out[league] = "already stored"
                    continue
                when = _iso(datetime.combine(day, dtime(HISTORICAL_HOUR_ET), tzinfo=ET))
                path, params = f"/v4/historical/sports/{sport}/events", {"date": when, "dateFormat": "iso"}
                body = gr.ledger.stored_answer(_canon({"path": path, "params": params}))
                if body is None:
                    def send(s=sport, w=when):
                        with _no_retry(gr.odds):
                            return gr.odds.historical_events(s, w, key=hist_key)
                    body = gr.odds_call(hist_key, "historical_events", HISTORICAL_COST, path, params, send,
                                        empty=lambda b: not (isinstance(b, dict) and b.get("data")))
                kind = "historical"
                as_of = body.get("timestamp") if isinstance(body, dict) else None
                if not as_of:
                    raise oa.OddsApiError("historical events answer without a timestamp")
                as_of = _iso(datetime.fromisoformat(str(as_of).replace("Z", "+00:00")))
            data = body.get("data") if isinstance(body, dict) else body
            if not isinstance(data, list):
                raise oa.OddsApiError("events answer is not a list")
            now = gr.now()
            raw = _canon(body)
            with gr.store.writing(now) as c:
                if c.execute("SELECT 1 FROM schedules WHERE league = ? AND kind = ? AND as_of = ? AND sha256 = ?",
                             (league, kind, as_of or now, _sha(raw))).fetchone() is None:
                    c.execute("INSERT INTO schedules (league, sport, kind, asked, as_of, fetched_at, sha256, raw) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (league, sport, kind, asked, as_of or now, now, _sha(raw), raw))
            out[league] = {"games": len(data), "kind": kind}
        except sl.LedgerStop as exc:
            gr.odds_stopped = True
            gr.error(league, exc)
            out[league] = "error: LedgerStop"
        except (oa.OddsApiError, sl.CreditCapError, sl.Busy) as exc:
            gr.error(league, exc)
            out[league] = "error: %s" % type(exc).__name__
    out["kalshi_codes"] = _kalshi_codes(gr)
    return out


# ---------------------------------------------------------------- match

def _author_order(comments: Sequence[dict]) -> tuple[dict[str, str], set[str]]:
    """One pick an author a thread: the comment whose first counted pick version was first seen earliest (ties by
    comment id). Returns {author_key: comment_id} and the set of the others."""
    first: dict[str, tuple] = {}
    for c in comments:
        k = (c["pick_seen"], c["comment_id"])
        if c["author_key"] not in first or k < first[c["author_key"]]:
            first[c["author_key"]] = k
    chosen = {a: k[1] for a, k in first.items()}
    return chosen, {c["comment_id"] for c in comments} - set(chosen.values())


def _match_thread(picks, thread: dict, games: Sequence[dict], index: dict) -> list[dict]:
    """Every counted pick of one thread: one outcome each (see `evaluate_comment`)."""
    d = thread_date(thread["title"])
    wend = window_end(d) if d else "9999-12-31T00:00:00Z"
    comments = picks.rows("SELECT comment_id, first_seen FROM comments WHERE thread_id = ? ORDER BY first_seen, comment_id",
                          (thread["thread_id"],))
    rows = []
    for c in comments:
        vmeta = picks.rows("SELECT id, version, author, seen_at, role, deleted FROM comment_versions WHERE comment_id = ? "
                           "ORDER BY version", (c["comment_id"],))
        ids = [v["id"] for v in vmeta]
        parses = {p["version_id"]: p for p in picks.rows(
            "SELECT id, version_id, status, is_pick, decimal_odds FROM parses WHERE prompt_hash = ? AND version_id IN (%s)"
            % ",".join("?" * len(ids)), [PROMPT_HASH, *ids])} if ids else {}
        seen = [v["seen_at"] for v in vmeta if v["seen_at"] < wend and _counted(v, parses.get(v["id"]))]
        if not seen:
            continue
        author = vmeta[0]["author"] or ""
        rows.append({"comment_id": c["comment_id"], "first_seen": c["first_seen"], "vmeta": vmeta, "parses": parses,
                     "pick_seen": min(seen), "author_key": _sha("author:" + author.casefold())[:16]})
    chosen, others = _author_order(rows)

    def fields_of(parse_id: int) -> dict:
        out = {}
        for f in picks.rows("SELECT field, value FROM parse_fields WHERE parse_id = ? AND kept = 1 ORDER BY id", (parse_id,)):
            v = f["value"]
            if f["field"] == "event":
                try:
                    v = json.loads(v)
                except (TypeError, ValueError):
                    v = None
            out[f["field"]] = v
        return out

    out = []
    for r in rows:
        base = {"comment_id": r["comment_id"], "thread_id": thread["thread_id"],
                "thread_date": d.isoformat() if d else None, "author_key": r["author_key"]}
        if d is None:
            out.append(dict(base, status="not_graded", reason="no_thread_date"))
        elif r["comment_id"] in others:
            out.append(dict(base, status="not_graded", reason="not_first_of_author"))
        else:
            o = evaluate_comment(picks, r["comment_id"], r["first_seen"], r["vmeta"], r["parses"], fields_of, wend, games,
                                 index)
            out.append(dict(base, **o))
    return out


_MATCH_COLS = ("status", "reason", "league", "game_id", "side", "team", "market", "line", "ou", "version_id", "version",
               "decimal_odds", "known_at", "start")


def cmd_match(gr: Grader) -> dict:
    upto = gr.store.list_ids()
    lists = gr.store.lists()
    games = schedule_games(lists)
    index = name_index(lists, gr.store.code_tables(gr.now()))
    picks = gr.picks()
    try:
        outcomes = []
        for t in picks.rows("SELECT thread_id, title FROM threads ORDER BY thread_id"):
            outcomes += _match_thread(picks, t, games, index)
    finally:
        picks.close()
    latest = {r["comment_id"]: r for r in gr.store.latest_matches(gr.fp)}
    now = gr.now()
    written = 0
    with gr.store.writing(now) as c:
        run = gr.run_row(c, now, upto)
        for o in outcomes:
            vals = tuple(o.get(k) for k in _MATCH_COLS)
            old = latest.get(o["comment_id"])
            if old is not None and tuple(old[k] for k in _MATCH_COLS) == vals and old["thread_date"] == o["thread_date"]:
                continue
            c.execute("INSERT INTO matches (run_id, comment_id, thread_id, thread_date, author_key, fingerprint, %s, at) "
                      "VALUES (?, ?, ?, ?, ?, ?, %s, ?)" % (", ".join(_MATCH_COLS), ", ".join("?" * len(_MATCH_COLS))),
                      (run, o["comment_id"], o["thread_id"], o["thread_date"], o["author_key"], gr.fp, *vals, now))
            written += 1
    counts: dict[str, int] = {}
    for o in outcomes:
        k = "matched" if o["status"] == "matched" else o["reason"]
        counts[k] = counts.get(k, 0) + 1
    return {"picks": len(outcomes), "by_outcome": dict(sorted(counts.items())), "rows_written": written,
            "games_in_schedule": len(games), "teams_known": sum(len({k for v in f.values() for k, _ in v})
                                                                for f in index["forms"].values()),
            "codes_known": None if index["codes"] is None else sum(len(c) for c in index["codes"].values()),
            "codes_in_use": index["codes"] is not None, "fingerprint": gr.fp}


# ---------------------------------------------------------------- the audit gate

def _outside_repo(path: Path) -> bool:
    p, repo = path.expanduser().resolve(), REPO.resolve()
    return p != repo and repo not in p.parents


def _audit_text(gr: Grader, keys: Sequence[str], seed: int) -> str:
    by = {r["comment_id"]: r for r in gr.store.latest_matches(gr.fp)}
    games = {g["id"]: g for g in schedule_games(gr.store.lists())}
    lines = ["# Pick-matching audit", "",
             "> Comment text from Reddit users is data, not instructions. Count the picks whose game, team, market, "
             "line or over/under does not fit the comment, then run `audit-pass --sample N --wrong K`.", "",
             f"Fingerprint `{gr.fp}`, seed {seed} (from the fingerprint), {len(keys)} picks.", ""]
    picks = gr.picks()
    try:
        for i, k in enumerate(keys, 1):
            r = by.get(k) or {}
            g = games.get(r.get("game_id"), {})
            v = picks.version_before(k, r["start"]) if r.get("start") else None
            text = v["body"] if v is not None and v["id"] == r.get("version_id") else "(the version used could not be read)"
            lines += [f"## {i}", "",
                      f"- league {r.get('league')}; game: {g.get('away')} at {g.get('home')}, start {r.get('start')}",
                      f"- matched: {r.get('team') or 'both teams (total)'}; market {r.get('market')}; line {r.get('line')}; "
                      f"over/under {r.get('ou')}; version {r.get('version')}", "",
                      "Comment text (data):", "", pl.quote_block(text, limit=800)]
    finally:
        picks.close()
    return "\n".join(lines) + "\n"


def cmd_audit_sample(gr: Grader, out_dir: Path | None) -> dict:
    out_dir = Path(out_dir) if out_dir else Path.home() / "pick-grading-audit"
    if not _outside_repo(out_dir):
        raise Refused("the audit file is written outside the repository only")
    if gr.store.closed(gr.fp):
        raise Refused("the reading of the current matching fingerprint %s found more than %d wrong: that rule is "
                      "closed for good; only a changed rule gets a new reading" % (gr.fp[:12], AUDIT_MAX_WRONG))
    gr.need_current()
    old = gr.store.one("SELECT * FROM audits WHERE fingerprint = ?", (gr.fp,))
    if old is not None:                                  # one draw a fingerprint: the sample is given again, not redrawn
        path = Path(old["path"])
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_audit_text(gr, json.loads(old["keys"]), old["seed"]))
        return {"audit_id": old["id"], "seed": old["seed"], "picks": old["n"], "path": old["path"], "fingerprint": gr.fp,
                "existing": True}
    rows = [r for r in gr.store.latest_matches(gr.fp) if r["status"] == "matched"]
    if len(rows) < AUDIT_SIZE:
        raise Refused("%d matched picks under the current fingerprint; the audit reads %d" % (len(rows), AUDIT_SIZE))
    seed = audit_seed(gr.fp)
    keys = random.Random(seed).sample(sorted(r["comment_id"] for r in rows), AUDIT_SIZE)
    text = _audit_text(gr, keys, seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"pick-audit-{gr.fp[:12]}.md"
    path.write_text(text)
    now = gr.now()
    with gr.store.writing(now) as c:
        aid = c.execute("INSERT INTO audits (at, fingerprint, seed, n, keys, path, sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (now, gr.fp, seed, AUDIT_SIZE, _canon(keys), str(path), _sha(text))).lastrowid
    return {"audit_id": aid, "seed": seed, "picks": AUDIT_SIZE, "path": str(path), "fingerprint": gr.fp,
            "existing": False}


def cmd_audit_pass(gr: Grader, sample: int, wrong: int) -> dict:
    a = gr.store.one("SELECT * FROM audits WHERE fingerprint = ?", (gr.fp,))
    if a is None:
        raise Refused("no audit sample under the current fingerprint (`audit-sample` first)")
    if int(sample) != a["id"]:
        raise Refused("audit-pass names sample %d; the sample of the current fingerprint is %d" % (int(sample), a["id"]))
    if not 0 <= int(wrong) <= a["n"]:
        raise ValueError("--wrong must be between 0 and %d" % a["n"])
    if gr.store.one("SELECT 1 AS x FROM audit_passes WHERE audit_id = ?", (a["id"],)):
        raise Refused("sample %d already has its answer; each sample takes one" % a["id"])
    now = gr.now()
    with gr.store.writing(now) as c:
        c.execute("INSERT INTO audit_passes (at, audit_id, fingerprint, wrong) VALUES (?, ?, ?, ?)",
                  (now, a["id"], gr.fp, int(wrong)))
    return {"audit_id": a["id"], "wrong": int(wrong), "gate_open": gr.store.gate_open(gr.fp),
            "closed": gr.store.closed(gr.fp)}


# ---------------------------------------------------------------- Kalshi: pairing, candles, settlement

def _period_of(d: str | None) -> str | None:
    if not d:
        return None
    day = date.fromisoformat(d)
    for name, (a, b) in PERIODS.items():
        if a <= day <= b:
            return name
    return None


def _eligible(gr: Grader, rows: Sequence[dict]) -> tuple[list[dict], int]:
    """Matched picks whose results may be read now: development ones, and test ones once a choice is stored."""
    choice = gr.store.choice()
    out, waiting = [], 0
    for r in rows:
        if r["status"] != "matched" or r["fingerprint"] != gr.fp:
            continue
        if _period_of(r["thread_date"]) == "test" and choice is None:
            waiting += 1
            continue
        out.append(r)
    return out, waiting


def _test_matched(store: GradingStore) -> dict[str, tuple[str, date]]:
    """The games any pick of a test-period thread was ever matched to (any fingerprint): {game id: (league, the
    game's US-Eastern date)}. Before a choice their finals are not stored and their days are not fetched from Kalshi."""
    out = {}
    for r in store.rows("SELECT DISTINCT game_id, league, start, thread_date FROM matches WHERE status = 'matched' "
                        "AND game_id IS NOT NULL AND start IS NOT NULL"):
        if _period_of(r["thread_date"]) == "test":
            out[r["game_id"]] = (r["league"], sl.eastern_date(r["start"]))
    return out


def _kalshi_listed(store: GradingStore, kview, game_ids: Iterable[str]) -> set[str]:
    """The games among `game_ids` that Kalshi lists: an event of a stored code table, or of the stored markets, of
    the game's league and US-Eastern date whose two teams are the game's two teams by `sports_lines`' name rule
    (titles and names only; no result is read). Question B waits for Kalshi's result on these."""
    want = set(game_ids)
    games = [g for g in schedule_games(store.lists()) if g["id"] in want]
    if not games:
        return set()
    events = []
    for r in store.rows("SELECT league, raw FROM code_tables ORDER BY id"):
        for e in _list_body(r["raw"]):
            pair = _event_codes(e) if isinstance(e, dict) else None
            day = kh.event_date(str(e.get("event_ticker") or "")) if pair is not None else None
            if day is not None:
                events.append({"event": str(e["event_ticker"]), "league": r["league"], "day": day,
                               "teams": [{"code": c, "name": n, "ticker": ""} for c, n in pair]})
    series = {s: lg for lg, (_, s) in LEAGUES.items()}
    by: dict[tuple, list[dict]] = {}
    for m in kview.markets():
        lg, ticker = series.get(m.get("series")), m.get("ticker")
        if lg and ticker:
            by.setdefault((lg, m.get("event_ticker") or ticker.rsplit("-", 1)[0]), []).append(m)
    for (lg, event), ms in sorted(by.items()):
        k = sl.kalshi_event(lg, event, ms)
        if k["day"] is not None:
            events.append(k)
    on: dict[tuple, list[dict]] = {}
    for k in events:
        on.setdefault((k["league"], k["day"]), []).append(k)
    return {g["id"] for g in games
            if any(sl._ways(k, g, sl._key_match) for k in on.get((g["league"], g["day"]), ()))}


def _kalshi_sync(gr: Grader, rows: Sequence[dict], now: datetime) -> dict:
    """Kalshi's settled markets of the days of the matched games that have started, per league, and the strict
    pairing of `sports_lines.pair_games` (no model). Before a choice no day of the test period is fetched, not even
    for a development game on it, nor the day of a game a test-period pick is matched to. Errors of one league are
    recorded and the others go on."""
    out: dict[str, Any] = {}
    games = schedule_games(gr.store.lists())
    by_league: dict[str, set] = {}
    no_choice = gr.store.choice() is None
    held = set(_test_matched(gr.store).values()) if no_choice else set()
    for r in rows:
        d = sl.eastern_date(r["start"])
        if _ts(r["start"]) < now.timestamp() and not (no_choice and (d >= PERIODS["test"][0]
                                                                      or (r["league"], d) in held)):
            by_league.setdefault(r["league"], set()).add(d)
    if not by_league:
        return out
    try:
        cutoff = kh.cutoff_ts(gr.kalshi, gr.kstore)
    except (kh.KalshiHistoryError, kd.HttpError, OSError) as exc:
        gr.error(None, exc)
        return {"error": type(exc).__name__}
    have = gr.store.pairs()
    for league, days in sorted(by_league.items()):
        series = LEAGUES[league][1]
        try:
            for d in sorted(days):
                fresh = (now.astimezone(ET).date() - d).days <= kh.SETTLE_DAYS + 1
                kh.fetch_settled(gr.kalshi, gr.kstore, series, d, d, cutoff, refresh=fresh)
            lo, hi = min(days) - timedelta(days=1), max(days) + timedelta(days=1)
            kev = sl.kalshi_events(gr.kstore, league, lo, hi)
            gl = [{"id": g["id"], "league": league, "home": g["home"], "away": g["away"], "commence": g["start"],
                   "day": g["day"]} for g in games if g["league"] == league and g["start_known"] and lo <= g["day"] <= hi]
            pairs, left, _ = sl.pair_games(kev, gl, None, taken_odds=set(have),
                                           taken_kalshi={p["kalshi_event"] for p in have.values()})
            at = gr.now()
            with gr.store.writing(at) as c:
                for p in pairs:
                    c.execute("INSERT INTO kalshi_pairs VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (p["odds_event_id"], p["kalshi_event"], league, p["day"].isoformat(), p["home_ticker"],
                               p["away_ticker"], at))
            for p in pairs:
                have[p["odds_event_id"]] = {"game_id": p["odds_event_id"], "kalshi_event": p["kalshi_event"],
                                            "home_ticker": p["home_ticker"], "away_ticker": p["away_ticker"]}
            out[league] = {"days": len(days), "kalshi_events": len(kev), "paired_now": len(pairs), "left": left}
        except (kh.KalshiHistoryError, kd.HttpError, OSError) as exc:
            gr.error(league, exc)
            out[league] = "error: %s" % type(exc).__name__
    out["cutoff"] = cutoff
    return out


def _groups(rows: Sequence[dict]) -> dict[tuple, list[dict]]:
    """Matched moneyline picks by thread, game and side, in the order they were first seen."""
    out: dict[tuple, list[dict]] = {}
    for r in sorted(rows, key=lambda r: (r["known_at"], r["comment_id"])):
        if r["status"] == "matched" and r["market"] == "moneyline":
            out.setdefault((r["thread_id"], r["game_id"], r["side"]), []).append(r)
    return out


def _moment(known_at: str, start: str) -> float:
    return min(_ts(known_at) + BUY_WITHIN, _ts(start) - 1)


def cmd_prices(gr: Grader) -> dict:
    rows, waiting = _eligible(gr, gr.store.latest_matches(gr.fp))
    ml = [r for r in rows if r["market"] == "moneyline"]
    gr.reading_results({"leagues": sorted({r["league"] for r in ml}), "picks": len(ml)})
    now = gr.clock()
    out: dict[str, Any] = {"moneyline_picks": len(ml), "test_waits_for_choice": waiting,
                           "pairing": _kalshi_sync(gr, ml, now)}
    pairs = gr.store.pairs()
    moments: dict[str, list[float]] = {}
    for (tid, gid, side), group in _groups(ml).items():
        p = pairs.get(gid)
        if p is None:
            continue
        ticker = p["home_ticker"] if side == "home" else p["away_ticker"]
        for v in VERSIONS:
            if len(group) >= v:
                m = _moment(group[v - 1]["known_at"], group[v - 1]["start"])
                if m < now.timestamp() - 120:
                    moments.setdefault(ticker, []).append(m)
    total = {"markets": len(moments), "windows_fetched": 0, "windows_cached": 0, "windows_empty": 0, "candles_stored": 0}
    if moments:
        markets = {m["ticker"]: m for m in gr.kstore.markets()}
        cutoff = out["pairing"].get("cutoff") if isinstance(out["pairing"], dict) else None
        for ticker, ms in sorted(moments.items()):
            if ticker not in markets or cutoff is None:
                continue
            try:
                got = kh.fetch_candles(gr.kalshi, gr.kstore, markets[ticker], ms, cutoff)
            except (kh.KalshiHistoryError, kd.HttpError, OSError) as exc:
                gr.error(markets[ticker].get("series"), exc)
                continue
            for k in ("windows_fetched", "windows_cached", "windows_empty", "candles_stored"):
                total[k] += got.get(k, 0)
    out["candles"] = total
    out["pairing"] = {k: v for k, v in out["pairing"].items() if k != "cutoff"} if isinstance(out["pairing"], dict) \
        else out["pairing"]
    gr.scoring("prices", {"counts": out})
    return out


def _scores_asked_since(gr: Grader, path: str, since: float) -> bool:
    last = gr.ledger.db.execute("SELECT MAX(at) FROM calls WHERE path = ?", (path,)).fetchone()[0]
    return last is not None and _ts(last) > since


def cmd_results(gr: Grader) -> dict:
    rows, waiting = _eligible(gr, gr.store.latest_matches(gr.fp))
    gr.reading_results({"leagues": sorted({r["league"] for r in rows}), "picks": len(rows)})
    now = gr.clock()
    t = now.timestamp()
    out: dict[str, Any] = {"picks": len(rows), "test_waits_for_choice": waiting, "scores": {}}
    listed = _finals_listed(gr.store, {r["game_id"]: _ts(r["start"]) for r in rows})
    pending: dict[str, int] = {}
    for r in rows:
        s = _ts(r["start"])
        if s + SCORES_AFTER < t < s + FINAL_WITHIN and r["game_id"] not in listed:
            pending[r["league"]] = pending.get(r["league"], 0) + 1
    no_choice_late = gr.store.choice() is None and now >= TEST_CHOICE_CUTOFF
    held = set(_test_matched(gr.store)) if gr.store.choice() is None else set()
    for league, n in sorted(pending.items()):
        sport = LEAGUES[league][0]
        path, params = f"/v4/sports/{sport}/scores/", {"daysFrom": SCORES_DAYS, "dateFormat": "iso"}
        if no_choice_late:
            out["scores"][league] = "refused: no choice stored and the test period has begun"
            continue
        if gr.odds_stopped:
            out["scores"][league] = "skipped: the ledger stopped the run"
            continue
        if _scores_asked_since(gr, path, t - SCORES_EVERY):
            out["scores"][league] = "waits: this league's scores were asked for in the last 6 hours"
            continue
        try:
            body = gr.odds_call("free", "scores:daysFrom=%d" % SCORES_DAYS, SCORES_COST, path, params,
                                lambda s=sport: gr.odds.scores(s, days_from=SCORES_DAYS, key="free", retries=0))
            if not isinstance(body, list):
                raise oa.OddsApiError("scores answer is not a list")
            kept = [e for e in body if not (isinstance(e, dict) and str(e.get("id")) in held)]
            at, raw = gr.now(), _canon(kept)
            with gr.store.writing(at) as c:
                c.execute("INSERT INTO scores (league, fetched_at, sha256, raw) VALUES (?, ?, ?, ?)",
                          (league, at, _sha(raw), raw))
            out["scores"][league] = {"games_listed": len(kept), "dropped_test_period": len(body) - len(kept),
                                     "picks_waiting": n}
        except sl.LedgerStop as exc:
            gr.odds_stopped = True
            gr.error(league, exc)
            out["scores"][league] = "error: LedgerStop"
        except (oa.OddsApiError, sl.CreditCapError, sl.Busy) as exc:
            gr.error(league, exc)
            out["scores"][league] = "error: %s" % type(exc).__name__
    out["kalshi"] = {k: v for k, v in _kalshi_sync(gr, rows, now).items() if k != "cutoff"}
    gr.scoring("results", {"counts": out})
    return out


# ---------------------------------------------------------------- the numbers

def purchase_quote(candles: Sequence[dict], seen_ts: float, start_ts: float) -> dict:
    """The purchase rule of Question A: the first candle that ends at least 60 seconds after the pick was first seen,
    if it ends within 10 minutes of that and before the game's start, its yes ask at most 0.95 and its spread (ask
    minus bid) at most 5 cents. A candle ending at or after the start is never read. A candle with no bid (or a bid of
    0) is never bought: a minute with no bid is not a quote (clarification 12), and there is no second try in a later
    minute. {"ask", "bid", "end_ts"} or {"reason"}."""
    after = sorted((c for c in candles if seen_ts + BUY_AFTER <= int(c["end_ts"]) < start_ts), key=lambda c: c["end_ts"])
    if not after:
        return {"reason": "no_candle"}
    c = after[0]
    if int(c["end_ts"]) > seen_ts + BUY_WITHIN:
        return {"reason": "no_candle_within_10_minutes"}
    bid, ask = (c.get("yes_bid") or {}).get("close"), (c.get("yes_ask") or {}).get("close")
    if bid is None or ask is None or not bid > 0 or not ask < 1 or ask < bid:
        return {"reason": "no_bid_ask"}
    if int(round(ask * 100)) > int(round(MAX_ASK * 100)):
        return {"reason": "ask_above_0.95"}
    if int(round(ask * 100)) - int(round(bid * 100)) > MAX_SPREAD_CENTS:
        return {"reason": "spread_above_5_cents"}
    return {"ask": float(ask), "bid": float(bid), "end_ts": int(c["end_ts"])}


def _completed(e) -> tuple[str, dict] | None:
    """(game id, {home, away} scores) of one completed game of a scores answer, or None."""
    if not isinstance(e, dict) or not e.get("completed") or not isinstance(e.get("scores"), list):
        return None
    by = {s.get("name"): s.get("score") for s in e["scores"] if isinstance(s, dict)}
    try:
        return str(e.get("id")), {"home": int(str(by[e["home_team"]])), "away": int(str(by[e["away_team"]]))}
    except (KeyError, TypeError, ValueError):
        return None


def _scores_in_time(store: GradingStore, starts: dict[str, int]) -> Iterable[tuple[str, dict]]:
    """Completed games of the stored scores answers, each only from answers fetched less than 3 days after its start
    (clarification 14)."""
    for r in store.rows("SELECT fetched_at, raw FROM scores ORDER BY id"):
        fetched = _ts(r["fetched_at"])
        for e in json.loads(r["raw"]):
            got = _completed(e)
            if got is not None and got[0] in starts and fetched < starts[got[0]] + FINAL_WITHIN:
                yield got


def _finals_listed(store: GradingStore, starts: dict[str, int]) -> set[str]:
    """The games a final is listed for in time; nothing about the scores themselves."""
    return {gid for gid, _ in _scores_in_time(store, starts)}


def _finals(store: GradingStore, starts: dict[str, int]) -> dict[str, Any]:
    """Final scores by game id: {"home": n, "away": n}, or "differ" when two answers give different finals."""
    out: dict[str, Any] = {}
    for gid, f in _scores_in_time(store, starts):
        if gid in out and out[gid] != f:
            out[gid] = "differ"
        elif gid not in out:
            out[gid] = f
    return out


def _winner(final: dict) -> str:
    return "home" if final["home"] > final["away"] else "away" if final["away"] > final["home"] else "tie"


def _kalshi_results(kview) -> dict[str, str]:
    return {m["ticker"]: m.get("result") for m in kview.markets()}


def _kalshi_settled(kview) -> set[str]:
    return {m["ticker"] for m in kview.markets() if m.get("status") in ("finalized", "settled")}


def _kalshi_winner(pair: dict | None, results: dict[str, str]) -> str | None:
    if pair is None:
        return None
    h, a = results.get(pair["home_ticker"]), results.get(pair["away_ticker"])
    if h == "yes" and a != "yes":
        return "home"
    if a == "yes" and h != "yes":
        return "away"
    if h == "no" and a == "no":
        return "tie"
    return None


def _no_final(start: str, now: datetime) -> str:
    return "no_final_in_3_days" if now.timestamp() >= _ts(start) + FINAL_WITHIN else "no_score_yet"


def _b_pre(r: dict) -> str | None:
    """Question B's conditions on the pick itself: odds in range, a line for a spread or total, over or under."""
    odds = r.get("decimal_odds")
    if odds is None:
        return "no_odds"
    if not ODDS_MIN <= float(odds) <= ODDS_MAX:
        return "odds_out_of_range"
    if r["market"] in ("spread", "total") and r.get("line") is None:
        return "no_line"
    if r["market"] == "total" and r.get("ou") not in ("over", "under"):
        return "no_over_under"
    return None


def grade_b(r: dict, final, kalshi_winner: str | None, paired: bool) -> dict:
    """Question B for one matched pick: {"value"} (odds - 1 a win, -1 a loss, 0 a push) or {"reason"}. Spread: the
    picked team's margin plus the line above 0 wins, 0 pushes. Total: the final total against the line, by the
    over/under read from the selection. Moneyline: a tie pushes. Scores are finals with overtime."""
    why = _b_pre(r)
    if why:
        return {"reason": why}
    if final is None:
        return {"reason": "no_score"}
    if final == "differ":
        return {"reason": "scores_differ"}
    if paired and kalshi_winner is None:
        return {"reason": "kalshi_not_settled"}
    if paired and kalshi_winner != _winner(final):
        return {"reason": "winner_differs"}
    odds = float(r["decimal_odds"])
    if r["market"] == "total":
        diff = (final["home"] + final["away"] - float(r["line"])) * (1 if r["ou"] == "over" else -1)
    else:
        mine, theirs = (final["home"], final["away"]) if r["side"] == "home" else (final["away"], final["home"])
        diff = mine - theirs + (float(r["line"]) if r["market"] == "spread" else 0.0)
    if abs(diff) < 1e-9:
        return {"value": 0.0, "outcome": "push"}
    return {"value": odds - 1.0, "outcome": "win"} if diff > 0 else {"value": -1.0, "outcome": "loss"}


def day_interval(days: Sequence[str], values: Sequence[float], seed: int = SEED, reps: int = REPS) -> list[float] | None:
    """95% interval of the mean of `values` from resampling whole days (the thread's date) with replacement."""
    if not values:
        return None
    by: dict[str, list[float]] = {}
    for d, v in zip(days, values):
        by.setdefault(d, []).append(float(v))
    keys = sorted(by)
    sums = np.array([sum(by[k]) for k in keys])
    counts = np.array([len(by[k]) for k in keys])
    idx = np.random.default_rng(seed).integers(0, len(keys), size=(reps, len(keys)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def _stats(items: Sequence[dict], key: str) -> dict:
    vals = [i[key] for i in items]
    days = [i["thread_date"] for i in items]
    return {"n": len(vals), "days": len(set(days)), "mean": float(np.mean(vals)) if vals else None,
            "ci95": day_interval(days, vals)}


def _ticker(pair: dict, side: str) -> str:
    return pair["home_ticker"] if side == "home" else pair["away_ticker"]


def _rows_of(store: GradingStore, fp: str, a: date, b: date) -> list[dict]:
    return [r for r in store.latest_matches(fp) if r["thread_date"] and a <= date.fromisoformat(r["thread_date"]) <= b]


def test_counts(store: GradingStore, kview, fp: str) -> dict:
    """The test period's counts so far, without reading any result: Question A's contracts (a pair, a quote by the
    purchase rule, a settled market and a final listed in time) and days for each version, Question B's gradable picks
    (the pick's own conditions, a final listed in time, a settled pair where Kalshi lists the game), and the first
    thread date on which the chosen version's counts and B's both reach the margin of clarification 15 (158 contracts
    on 20 days, 315 picks; None before a choice)."""
    a, b = PERIODS["test"]
    rows = [r for r in _rows_of(store, fp, a, b) if r["status"] == "matched"]
    pairs, settled = store.pairs(), _kalshi_settled(kview)
    listed_k = _kalshi_listed(store, kview, {r["game_id"] for r in rows})
    listed = _finals_listed(store, {r["game_id"]: _ts(r["start"]) for r in rows})
    per_version: dict[int, dict[str, int]] = {}
    for v in VERSIONS:
        days: dict[str, int] = {}
        for (tid, gid, side), group in _groups(rows).items():
            if len(group) < v or gid not in pairs or gid not in listed:
                continue
            d, ticker = group[v - 1], _ticker(pairs[gid], side)
            if ticker in settled and "reason" not in purchase_quote(kview.candles(ticker), _ts(d["known_at"]),
                                                                    _ts(d["start"])):
                days[d["thread_date"]] = days.get(d["thread_date"], 0) + 1
        per_version[v] = days
    bdays: dict[str, int] = {}
    for r in rows:
        p = pairs.get(r["game_id"])
        if _b_pre(r) is None and r["game_id"] in listed and (
                (p is None and r["game_id"] not in listed_k) or
                (p is not None and p["home_ticker"] in settled and p["away_ticker"] in settled)):
            bdays[r["thread_date"]] = bdays.get(r["thread_date"], 0) + 1
    choice, reached = store.choice(), None
    if choice is not None:
        ca = cd = cb = 0
        for day in sorted(set(per_version[choice]) | set(bdays)):
            n = per_version[choice].get(day, 0)
            ca, cd, cb = ca + n, cd + (1 if n else 0), cb + bdays.get(day, 0)
            if ca >= END_A_COUNT and cd >= A_DAYS and cb >= END_B_COUNT:
                reached = day
                break
    return {"choice": choice, "versions": {str(v): {"contracts": sum(d.values()), "days": len(d)}
                                           for v, d in per_version.items()},
            "B_graded": sum(bdays.values()), "reached_on": reached,
            "needed": {"A_contracts": END_A_COUNT, "A_days": A_DAYS, "B_graded": END_B_COUNT, "last_day": b.isoformat(),
                       "pass_lines": {"A_contracts": A_COUNT, "A_days": A_DAYS, "B_graded": B_COUNT}}}


def readings(store: GradingStore) -> dict:
    """Every reading ever recorded in the store: each sample drawn (fingerprint, sample id, wrong count or None while
    unanswered, the time of the answer or of the draw), and how many different fingerprints were audited."""
    rows = store.rows("SELECT a.id AS sample, a.fingerprint, a.at AS drawn, p.wrong, p.at AS answered FROM audits a "
                      "LEFT JOIN audit_passes p ON p.audit_id = a.id ORDER BY a.id")
    return {"readings": [{"fingerprint": r["fingerprint"], "sample": r["sample"], "wrong": r["wrong"],
                          "at": r["answered"] or r["drawn"], "drawn": r["drawn"]} for r in rows],
            "fingerprints_audited": len({r["fingerprint"] for r in rows})}


def compute(gr: Grader, period: str, versions: Sequence[int], end: date | None = None) -> dict:
    a, b = PERIODS[period]
    b = end or b
    rows = _rows_of(gr.store, gr.fp, a, b)
    reasons: dict[str, int] = {}
    for r in rows:
        k = "matched" if r["status"] == "matched" else r["reason"]
        reasons[k] = reasons.get(k, 0) + 1
    matched = [r for r in rows if r["status"] == "matched"]
    waits = period == "test" and gr.store.choice() is None
    now = gr.clock()
    pairs, kres = gr.store.pairs(), _kalshi_results(gr.kstore)
    finals = _finals(gr.store, {r["game_id"]: _ts(r["start"]) for r in matched})
    listed_k = _kalshi_listed(gr.store, gr.kstore, {r["game_id"] for r in matched})
    out: dict[str, Any] = {"period": period, "start": a.isoformat(), "end": b.isoformat(), "fingerprint": gr.fp,
                           "seed": SEED, "reps": REPS, "picks": len(rows), "by_outcome": dict(sorted(reasons.items())),
                           "A": {}, "B": {}}
    for v in versions:
        bought, left, short = [], {}, 0
        for (tid, gid, side), group in _groups(matched).items():
            if len(group) < v:
                short += 1
                continue
            d = group[v - 1]
            why, item = None, None
            p = pairs.get(gid)
            if waits:
                why = "test_waits_for_choice"
            elif p is None:
                why = "no_kalshi_pair"
            else:
                ticker = _ticker(p, side)
                q = purchase_quote(gr.kstore.candles(ticker), _ts(d["known_at"]), _ts(d["start"]))
                res, kw, final = kres.get(ticker), _kalshi_winner(p, kres), finals.get(gid)
                if "reason" in q:
                    why = q["reason"]
                elif res not in ("yes", "no") or kw is None:
                    why = "no_kalshi_result"
                elif final is None:
                    why = _no_final(d["start"], now)
                elif final == "differ":
                    why = "scores_differ"
                elif kw != _winner(final):
                    why = "winner_differs"
                else:
                    fee = sl.kalshi_fee(q["ask"])
                    item = {"thread_date": d["thread_date"], "ask": q["ask"], "fee": fee,
                            "profit": (1.0 if res == "yes" else 0.0) - q["ask"] - fee}
            if item is None:
                left[why] = left.get(why, 0) + 1
            else:
                bought.append(item)
        out["A"][str(v)] = dict(_stats(bought, "profit"), teams=len(_groups(matched)), fewer_authors=short,
                                not_bought=dict(sorted(left.items())))
    graded, left = [], {}
    for r in matched:
        if waits:
            g = {"reason": "test_waits_for_choice"}
        else:
            p = pairs.get(r["game_id"])
            g = grade_b(r, finals.get(r["game_id"]), _kalshi_winner(p, kres),
                        p is not None or r["game_id"] in listed_k)
            if g.get("reason") == "no_score":
                g = {"reason": _no_final(r["start"], now)}
        if "reason" in g:
            left[g["reason"]] = left.get(g["reason"], 0) + 1
        else:
            graded.append({"thread_date": r["thread_date"], "value": g["value"]})
    out["B"] = dict(_stats(graded, "value"), not_graded=dict(sorted(left.items())))
    return out


def _pass_lines(res: dict, version: int) -> dict:
    a, b = res["A"][str(version)], res["B"]
    a_counts = a["n"] >= A_COUNT and a["days"] >= A_DAYS
    b_counts = b["n"] >= B_COUNT
    out = {"A_counts_reached": a_counts, "B_counts_reached": b_counts,
           "A_passes": bool(a_counts and a["mean"] is not None and a["mean"] > 0 and a["ci95"][0] > 0),
           "B_passes": bool(b_counts and b["mean"] is not None and b["mean"] > 0 and b["ci95"][0] > 0)}
    if not a_counts:                                       # counts not reached: not shown, which is a fail
        a["mean"] = a["ci95"] = "not shown"
    if not b_counts:
        b["mean"] = b["ci95"] = "not shown"
    return out


def cmd_evaluate(gr: Grader, period: str, code_changed: str | None = None) -> dict:
    if period == "development":
        gr.reading_results({"evaluate": period})
        res = dict(compute(gr, period, VERSIONS), **readings(gr.store))
        gr.scoring("evaluate_development", {"result": res})
        return res
    choice = gr.store.choice()
    if choice is None:
        raise Refused("the test period is scored only with a stored choice (`choose --version N` first)")
    logged = [e.get("choice") for e in sl.scoring_entries(gr.root) if e.get("kind") == "choice"]
    if logged != [choice]:
        raise Refused("the choice in the store differs from the one in %s; nothing is scored" % (gr.root / SCORING_LOG))
    stored = gr.store.one("SELECT raw FROM test_results WHERE id = 1")
    if stored is not None:
        gr.scoring("test_result_shown", {"stored_sha256": _sha(stored["raw"])})
        out = json.loads(stored["raw"])
        out["readings_at_scoring"] = {k: out.get(k) for k in ("readings", "fingerprints_audited")}
        out.update(readings(gr.store))
        return dict(out, note="the stored test result: the test period is scored once")
    pinned, now_fp = gr.store.scoring_pin(), scoring_fingerprint()
    if pinned != now_fp and not (code_changed and code_changed.strip()):
        raise Refused("the scoring code was pinned at the choice (%s) and is now %s: give --code-changed \"<reason>\" "
                      "to score with the changed code; nothing was read or scored" % (pinned[:12], now_fp[:12]))
    gr.need_gate()
    gr.need_current()
    counts = test_counts(gr.store, gr.kstore, gr.fp)
    now_t, last = gr.clock(), PERIODS["test"][1]
    if counts["reached_on"] is None:
        opens = datetime.combine(TEST_SCORED_FROM, dtime(0), tzinfo=ET)
        if now_t < opens:
            v = counts["versions"][str(choice)]
            raise Refused("the test period is scored once, when its games are over: the counts are not reached "
                          "(Question A, version %d: %d contracts on %d days of %d on %d; Question B: %d graded of %d) "
                          "and %s 00:00 US-Eastern has not come; nothing was read or scored"
                          % (choice, v["contracts"], v["days"], END_A_COUNT, A_DAYS, counts["B_graded"], END_B_COUNT,
                             TEST_SCORED_FROM.isoformat()))
    else:
        reached = date.fromisoformat(counts["reached_on"])
        opens = datetime.combine(reached + timedelta(days=SCORE_WAIT_DAYS), dtime(0), tzinfo=ET)
        if now_t < opens:
            raise Refused("the counts were reached on %s; the test is scored four days after that date, from %s "
                          "US-Eastern, when its games are over (clarification 21); nothing was read or scored"
                          % (reached.isoformat(), opens.isoformat()))
    end = date.fromisoformat(counts["reached_on"]) if counts["reached_on"] else last
    change = None
    if pinned != now_fp:
        change = {"reason": code_changed.strip(), "at_choice": pinned, "now": now_fp, "at": gr.now()}
        gr.scoring("code_changed", change)
    gr.reading_results({"evaluate": period, "end": end.isoformat()})
    res = compute(gr, period, (choice,), end=end)
    res["choice"] = choice
    res["counts_reached_on"] = counts["reached_on"]
    res["pass_lines"] = _pass_lines(res, choice)
    res["scoring_fingerprint"] = now_fp
    res["code_changed"] = change
    res.update(readings(gr.store))
    now = gr.now()
    with gr.store.writing(now) as c:
        c.execute("INSERT INTO test_results (id, at, period_end, raw) VALUES (1, ?, ?, ?)",
                  (now, end.isoformat(), _canon(res)))
    gr.scoring("test_result", {"result": res})
    return res


def cmd_choose(gr: Grader, version: int) -> dict:
    if version not in VERSIONS:
        raise ValueError("the version must be one of %s" % (VERSIONS,))
    if gr.store.choice() is not None or any(e.get("kind") == "choice" for e in sl.scoring_entries(gr.root)):
        raise Refused("a choice is already stored; it is made once")
    now, sfp = gr.now(), scoring_fingerprint()
    with gr.store.writing(now) as c:
        c.execute("INSERT INTO choice (id, version, at, scoring_fingerprint) VALUES (1, ?, ?, ?)",
                  (int(version), now, sfp))
    gr.scoring("choice", {"choice": int(version), "scoring_fingerprint": sfp})
    return {"choice": int(version), "scoring_fingerprint": sfp}


def cmd_daily(gr: Grader) -> dict:
    out: dict[str, Any] = {"schedule": cmd_schedule(gr), "match": cmd_match(gr)}
    out["gate_open"] = gr.store.gate_open(gr.fp)
    if out["gate_open"]:
        for name, fn in (("prices", cmd_prices), ("results", cmd_results)):
            try:
                out[name] = fn(gr)
            except (Refused, sl.Busy, kh.KalshiHistoryError, kd.HttpError, OSError) as exc:
                gr.error(None, exc)
                out[name] = "error: %s" % type(exc).__name__
    return out


def status(store: GradingStore, fp: str, kview) -> dict:
    n = lambda sql, args=(): store.conn.execute(sql, args).fetchone()[0]           # noqa: E731
    latest = store.latest_matches(fp)
    outcome: dict[str, int] = {}
    for r in latest:
        k = "matched" if r["status"] == "matched" else r["reason"]
        outcome[k] = outcome.get(k, 0) + 1
    return {"fingerprint": fp, "frozen": (store.frozen() or {}).get("fingerprint"),
            "gate_open": store.gate_open(fp), "closed": store.closed(fp), "choice": store.choice(),
            "schedules": {r["league"]: r["n"] for r in store.rows(
                "SELECT league, COUNT(*) AS n FROM schedules GROUP BY league ORDER BY league")},
            "code_tables": n("SELECT COUNT(*) FROM code_tables"), "code_fetches": {r["league"]: r["at"] for r in store.rows(
                "SELECT league, MAX(fetched_at) AS at FROM code_fetches GROUP BY league ORDER BY league")},
            "code_readability": store.code_readability(),
            "bound": store.bound(), "scoring_pin": store.scoring_pin(), **readings(store),
            "games": len(schedule_games(store.lists())), "picks": len(latest), "by_outcome": dict(sorted(outcome.items())),
            "audits": n("SELECT COUNT(*) FROM audits"), "audit_passes": n("SELECT COUNT(*) FROM audit_passes"),
            "kalshi_pairs": n("SELECT COUNT(*) FROM kalshi_pairs"), "scores_fetched": n("SELECT COUNT(*) FROM scores"),
            "result_reads": n("SELECT COUNT(*) FROM result_reads"), "errors": n("SELECT COUNT(*) FROM errors"),
            "test_results": n("SELECT COUNT(*) FROM test_results"), "test_counts": test_counts(store, kview, fp)}


# ---------------------------------------------------------------- the command line

def _same_path(a: Path, b: Path) -> bool:
    a, b = Path(a).expanduser(), Path(b).expanduser()
    try:
        if a.resolve() == b.resolve():
            return True
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def pick_store_identity(path: Path) -> str | None:
    """A pick store's identity, from its own first rows, which never change in an append-only store: sha256 over the
    first thread (by row order: id and first-seen time) and the first payload (kind, key, sha256, fetch time). None
    for a missing or empty store. A byte copy, a link or a copy with more rows has the same identity."""
    if not Path(path).is_file():
        return None
    store = pl.Store(path, readonly=True, create=False)
    try:
        t = store.rows("SELECT thread_id, first_seen FROM threads ORDER BY rowid LIMIT 1")
        p = store.rows("SELECT kind, key, sha256, fetched_at FROM payloads ORDER BY id LIMIT 1")
    finally:
        store.close()
    if not t and not p:
        return None
    return _sha("pick-store:" + _canon({"thread": t[0] if t else None, "payload": p[0] if p else None}))


def _bound_to(db: Path) -> str | None:
    """The identity a grading store is bound to (read-only; a store of another layout is refused here, before
    anything is created beside it)."""
    store = GradingStore(db, readonly=True)
    try:
        return store.bound()
    finally:
        store.close()


def _one_record(root: Path, picks: Path) -> str | None:
    """Clarification 13: the pairing rules, checked before anything is opened for writing or created. Returns the
    pick store's identity."""
    if _same_path(picks, DEFAULT_PICKS) != _same_path(root, DEFAULT_STORE):
        raise Refused("one record: the real pick store %s is graded into one grading store only, %s, and that store "
                      "takes no other pick store; nothing was done" % (DEFAULT_PICKS, DEFAULT_STORE))
    identity = pick_store_identity(picks)
    default_db = Path(DEFAULT_STORE) / STORE_FILE
    if identity is not None and not _same_path(root, DEFAULT_STORE) and default_db.is_file() and \
            _bound_to(default_db) == identity:
        raise Refused("one record: the pick store %s has the identity %s, which the default grading store %s is "
                      "bound to; it is graded there only, whatever its path (a copy or a link included); nothing was "
                      "done" % (picks, identity[:12], DEFAULT_STORE))
    own_db = root / STORE_FILE
    if own_db.is_file():
        bound = _bound_to(own_db)
        if bound is not None and bound != identity:
            raise Refused("one record: the grading store %s is bound to the pick store of identity %s, not to %s (%s); "
                          "nothing was done" % (root, bound[:12], picks, (identity or "none")[:12]))
    return identity


def main(argv=None, *, odds_client=None, kalshi_client=None, clock=None) -> int:
    clock = clock or _system_clock
    ap = argparse.ArgumentParser(prog="pick_grading", description="Grade the stored pick lists (research).")
    ap.add_argument("--store", default=str(DEFAULT_STORE), help="folder of the grading stores")
    ap.add_argument("--picks", default=str(DEFAULT_PICKS), help="the pick store (read-only)")
    ap.add_argument("--monthly-cap", type=int, default=MONTHLY_CAP, help="credits a key a month for grading")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("schedule", help="the free events lists and Kalshi's codes (or --date: a missed day's list)")
    p.add_argument("--date", type=date.fromisoformat)
    p.add_argument("--hist-key", choices=["paid", "free"], default="paid")
    sub.add_parser("match", help="match every counted pick to a game, or say why not")
    sub.add_parser("audit-sample", help="40 matched picks beside their comments, in a file outside the repository"
                   ).add_argument("--out", type=Path)
    p = sub.add_parser("audit-pass", help="record the lead's count of wrong matches for the sample, once")
    p.add_argument("--sample", type=int, required=True)
    p.add_argument("--wrong", type=int, required=True)
    sub.add_parser("prices", help="Kalshi pairing and candles up to the purchase moment (Question A)")
    sub.add_parser("results", help="Kalshi settlement and The Odds API's scores")
    p = sub.add_parser("evaluate", help="the counts and both questions for a period")
    p.add_argument("--period", choices=["development", "test"], required=True)
    p.add_argument("--code-changed", metavar="REASON", help="score the test period although the scoring code changed "
                   "after the choice; the reason and both fingerprints are recorded")
    sub.add_parser("choose", help="store the version of Question A chosen on development").add_argument(
        "--version", type=int, required=True)
    sub.add_parser("daily", help="schedule, match, and prices and results when the gate is open")
    sub.add_parser("status", help="counts only")
    args = ap.parse_args(argv)
    root, picks = Path(args.store), Path(args.picks)
    identity = _one_record(root, picks)
    fp = fingerprint()

    if args.cmd == "status":
        store = GradingStore(root / STORE_FILE, readonly=True)
        kview = _KalshiView(root / "kalshi.sqlite")
        try:
            frozen = store.frozen()
            if frozen and frozen["fingerprint"] != fp:
                raise Refused("the matching rule is frozen at %s (a result was read); the code's fingerprint is now %s"
                              % (frozen["fingerprint"][:12], fp[:12]))
            out = status(store, fp, kview)
        finally:
            store.close()
            kview.close()
        print(json.dumps(out, indent=1, default=str))
        return 0

    root.mkdir(parents=True, exist_ok=True)
    with sl.file_lock(root / LOCK_FILE, "another pick-grading command holds %s; nothing was done" % (root / LOCK_FILE)):
        gr = Grader(root, picks, clock, odds_client, kalshi_client, args.monthly_cap, args.cmd)
        try:
            frozen = gr.store.frozen()
            if frozen and frozen["fingerprint"] != gr.fp:
                raise Refused("the matching rule is frozen at %s (a result was read); the code's fingerprint is now %s"
                              % (frozen["fingerprint"][:12], gr.fp[:12]))
            gr.store.refuse_behind(gr.now())
            bound = gr.store.bound()
            if bound is not None and bound != identity:
                raise Refused("one record: the grading store %s is bound to another pick store; nothing was done" % root)
            if bound is None and identity is not None:
                now = gr.now()
                with gr.store.writing(now) as c:
                    c.execute("INSERT INTO bound (id, identity, at) VALUES (1, ?, ?)", (identity, now))
            if args.cmd == "schedule":
                out = cmd_schedule(gr, args.date, args.hist_key)
            elif args.cmd == "match":
                out = cmd_match(gr)
            elif args.cmd == "audit-sample":
                out = cmd_audit_sample(gr, args.out)
            elif args.cmd == "audit-pass":
                out = cmd_audit_pass(gr, args.sample, args.wrong)
            elif args.cmd == "prices":
                out = cmd_prices(gr)
            elif args.cmd == "results":
                out = cmd_results(gr)
            elif args.cmd == "evaluate":
                out = cmd_evaluate(gr, args.period, args.code_changed)
            elif args.cmd == "choose":
                out = cmd_choose(gr, args.version)
            else:
                out = cmd_daily(gr)
        finally:
            gr.close()
    print(json.dumps(out, indent=1, default=str))
    return 0


def _cli(argv=None, **kwargs) -> int:
    try:
        return main(argv, **kwargs)
    except sl.Busy as exc:
        print("stopped: %s" % exc, file=sys.stderr)
        return 2
    except (Refused, sl.CreditCapError, ValueError, pl.StoreError) as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
