"""Matching a movie in our database to a TMDB search result.

TMDB (themoviedb.org) is a free movie database whose posters may be used by
apps that credit it. The rule here is: a WRONG poster is worse than a
generated one, so we only accept a confident match and otherwise return None.
"""
import re
from datetime import date

LANG_CODES = {"telugu": "te", "hindi": "hi", "tamil": "ta", "english": "en", "kannada": "kn", "malayalam": "ml"}
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def pick_match(results: list[dict], title: str, language: str, today: date | None = None,
               max_age_days: int = 900) -> dict | None:
    """Best result for `title`, or None. A result must have a poster, the SAME title
    (ignoring case and punctuation), the right original language when we know it,
    and a release date that is not too old (our listings are recent releases,
    which stops an old film with the same name from winning)."""
    today = today or date.today()
    want_lang = LANG_CODES.get(language.lower())
    best, best_score = None, 0.0
    for r in results:
        if not r.get("poster_path"):
            continue
        names = {_norm(r.get("title") or ""), _norm(r.get("original_title") or "")}
        if _norm(title) not in names:
            continue
        if want_lang and r.get("original_language") and r["original_language"] != want_lang:
            continue
        try:
            released = date.fromisoformat(r.get("release_date") or "")
        except ValueError:
            continue
        if (today - released).days > max_age_days:
            continue
        score = float(r.get("popularity") or 0) + float(r.get("vote_count") or 0) / 100
        if best is None or score > best_score:
            best, best_score = r, score
    return best


def poster_url(match: dict) -> str:
    return IMAGE_BASE + match["poster_path"]
