"""AI-assisted triage (SDS 4.6; REQ-9 to REQ-14; NFR-15).

Three suggestions are offered to people, never applied on their own:

    suggest_category  ->  the category that best fits a new report's description
    check_duplicate   ->  which nearby open report, if any, describes the same problem
    suggest_priority  ->  High, Medium or Low, with short reasons, for the administrator

Rules that apply to every call:
  * Only report text and category names are sent. Email addresses and phone numbers in the
    text are masked first; account names, coordinates and photos are never sent (NFR-15).
  * Each request has a time limit (AI_TIMEOUT_SECONDS, default 10 s) and temperature 0.
  * Any failure (switched off, time-out, network error, invalid reply) returns None, and the
    caller carries on without a suggestion (REQ-13). This module never raises to its callers.
  * record_suggestion() stores what was suggested and what the person chose (REQ-14). It does
    not commit, so the record is saved in the same transaction as the report it belongs to.

The OpenAI Chat Completions endpoint is called with the standard library, so no extra
package is needed. Tests replace `_post_json` with a fake.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from flask import current_app

from ..extensions import db
from ..models import AISuggestion, Category, Priority, Report, Status

log = logging.getLogger(__name__)

KIND_CATEGORY, KIND_DUPLICATE, KIND_PRIORITY = "category", "duplicate", "priority"
MAX_DESCRIPTION_CHARS = 2000
MAX_REASONS, MAX_REASON_CHARS = 3, 80
EARTH_RADIUS_M = 6_371_000

# Email addresses, and phone numbers of 9 or more digits with optional +, spaces, dots or dashes
# (covers Kenyan forms such as 0712 345 678, +254 712 345678 and 020-1234567).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d(?:[\s.-]?\d){8,13}(?!\w)")
MASK = "[removed]"


@dataclass(frozen=True)
class PrioritySuggestion:
    priority: str                       # one of Priority.LABELS keys
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers (no AI involved)
def mask_personal_data(text: str) -> str:
    """Replace email addresses and phone numbers before text leaves the server (NFR-15)."""
    text = _EMAIL_RE.sub(MASK, text or "")
    return _PHONE_RE.sub(MASK, text)


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle (haversine) distance between two points, in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def nearby_open_reports(category_id: int, latitude: float, longitude: float,
                        exclude_id: int | None = None) -> list[Report]:
    """Open reports in the same category within DUPLICATE_RADIUS_M, nearest first.

    A bounding box narrows the query in the database; the exact distance is then worked
    out here, on the server, so coordinates are never sent to the AI service.
    """
    radius = current_app.config["DUPLICATE_RADIUS_M"]
    limit = current_app.config["DUPLICATE_MAX_CANDIDATES"]
    d_lat = math.degrees(radius / EARTH_RADIUS_M)
    d_lng = d_lat / max(math.cos(math.radians(latitude)), 1e-6)

    query = Report.query.filter(
        Report.category_id == category_id,
        Report.status.in_(Status.OPEN),
        Report.latitude.between(latitude - d_lat, latitude + d_lat),
        Report.longitude.between(longitude - d_lng, longitude + d_lng),
    )
    if exclude_id is not None:
        query = query.filter(Report.id != exclude_id)

    in_range = [(distance_m(latitude, longitude, r.latitude, r.longitude), r) for r in query.all()]
    in_range = [pair for pair in in_range if pair[0] <= radius]
    in_range.sort(key=lambda pair: (pair[0], pair[1].id))
    return [report for _, report in in_range[:limit]]


def is_enabled() -> bool:
    cfg = current_app.config
    return bool(cfg.get("AI_ENABLED") and cfg.get("OPENAI_API_KEY"))


# ---------------------------------------------------------------- the three suggestions
def suggest_category(description: str, categories: list[Category]) -> Category | None:
    """REQ-9: return one of the given active categories, or None."""
    if not categories or not description:
        return None
    names = [c.name for c in categories]
    reply = _ask(
        KIND_CATEGORY,
        "You sort community infrastructure reports from Kenya into categories. "
        'Reply only with JSON of the form {"category": "<name>"}, using exactly one of the given names.',
        {"categories": names, "description": _prepare(description)},
    )
    chosen = str((reply or {}).get("category", "")).strip().lower()
    return next((c for c in categories if c.name.lower() == chosen), None)


def check_duplicate(description: str, candidates: list[Report]) -> Report | None:
    """REQ-10: return the candidate report that describes the same problem, or None.

    Candidates are numbered 1..n for the request, so no reference numbers or ids are sent.
    """
    if not candidates or not description:
        return None
    numbered = [{"number": i, "description": _prepare(r.description)} for i, r in enumerate(candidates, 1)]
    reply = _ask(
        KIND_DUPLICATE,
        "You check whether a new infrastructure report describes the same problem, at the same place, "
        "as one of several existing reports. Reply only with JSON: "
        '{"duplicate_of": <number of the matching report>} or {"duplicate_of": null} if none match.',
        {"new_report": _prepare(description), "existing_reports": numbered},
    )
    number = (reply or {}).get("duplicate_of")
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(candidates):
        return None
    return candidates[number - 1]


def suggest_priority(description: str, category_name: str) -> PrioritySuggestion | None:
    """REQ-12: suggest High, Medium or Low with up to three short reasons."""
    if not description:
        return None
    reply = _ask(
        KIND_PRIORITY,
        "You help a county authority decide how urgently to fix infrastructure problems. "
        "High means danger to people or major disruption, Medium means a real problem that can wait "
        "a few days, Low means minor. Reply only with JSON: "
        '{"priority": "High" | "Medium" | "Low", "reasons": ["<short reason>", ...]} '
        f"with at most {MAX_REASONS} reasons.",
        {"category": category_name, "description": _prepare(description)},
    )
    if not reply:
        return None
    priority = str(reply.get("priority", "")).strip().lower()
    if priority not in Priority.LABELS:
        return None
    raw = reply.get("reasons") if isinstance(reply.get("reasons"), list) else []
    reasons = [str(r).strip()[:MAX_REASON_CHARS] for r in raw if str(r).strip()][:MAX_REASONS]
    return PrioritySuggestion(priority=priority, reasons=reasons)


# ---------------------------------------------------------------- record keeping (REQ-14)
def record_suggestion(kind: str, suggested_value: str | None, final_value: str | None,
                      report: Report | None = None, reasons: list[str] | None = None,
                      accepted: bool | None = None) -> AISuggestion:
    """Add an AISuggestion row to the session. The caller commits (same transaction as the report).

    `accepted` is True when the person kept the suggestion, False when they changed it, and
    None when there was no suggestion or no decision yet. If not given, it is worked out by
    comparing the suggested and final values (pass it explicitly for duplicate decisions).
    """
    if accepted is None and suggested_value is not None and final_value is not None:
        accepted = suggested_value.strip().lower() == final_value.strip().lower()
    row = AISuggestion(report=report, kind=kind, suggested_value=suggested_value,
                       final_value=final_value, accepted=accepted,
                       reasons="\n".join(reasons) if reasons else None)
    db.session.add(row)
    return row


# ---------------------------------------------------------------- talking to the API
def _prepare(text: str) -> str:
    return mask_personal_data(text)[:MAX_DESCRIPTION_CHARS]


def _ask(kind: str, instructions: str, payload: dict) -> dict | None:
    """Send one request and return the parsed JSON reply, or None on any problem (REQ-13).

    Every request writes one line to the log, used for AIT-6 (failure rate and response time):
        AI_CALL kind=category outcome=ok ms=812
    outcome is "ok", "failed" (time-out or network/API error) or "invalid" (unusable reply).
    """
    if not is_enabled():
        return None
    cfg = current_app.config
    body = {
        "model": cfg["AI_MODEL"],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    started = time.perf_counter()
    outcome, reply = "ok", None
    try:
        response = _post_json(cfg["AI_API_URL"], body, cfg["OPENAI_API_KEY"], cfg["AI_TIMEOUT_SECONDS"])
        content = response["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            reply = parsed
        else:
            outcome = "invalid"
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        outcome = "failed"
        # Never log the description itself; the error type is enough to diagnose.
        log.warning("AI request failed (%s); continuing without a suggestion.", type(error).__name__)
    except (ValueError, KeyError, IndexError, TypeError) as error:
        outcome = "invalid"
        log.warning("AI reply could not be used (%s); continuing without a suggestion.", type(error).__name__)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    log.info("AI_CALL kind=%s outcome=%s ms=%d", kind, outcome, elapsed_ms)
    return reply


def _post_json(url: str, body: dict, api_key: str, timeout: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310 (fixed https URL)
        return json.loads(response.read().decode("utf-8"))
