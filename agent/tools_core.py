"""The tools as plain Python functions, with no dependency on LangChain.

Every function returns a JSON-serialisable dict. Expected problems (unknown
booking, bad date) are returned as data so the model can explain them to the
customer; they never raise.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICIES_DIR = ROOT / "data" / "policies"
BOOKINGS_FILE = ROOT / "data" / "bookings.json"

SHOP_TZ = timezone(timedelta(hours=2))
# Fixed by the README so that answers are the same every time.
NOW = datetime(2026, 6, 10, 9, 0, tzinfo=SHOP_TZ)

# --- search_policies ---------------------------------------------------------

STOP_WORDS = frozenset(
    "a an and are at be can do does for from get how i if in is it me my of on or "
    "the to we what when where which who will with you your".split()
)
# Customers and documents use different words for the same thing.
SYNONYMS = {
    "ebike": "electric",
    "open": "open",
    "opening": "open",
    "hours": "open",
    "closed": "open",
    "cost": "price",
    "much": "price",
    "pay": "payment",
    "paying": "payment",
    "refund": "cancel",
    "cancellation": "cancel",
    "cancelled": "cancel",
    "cancelling": "cancel",
    "kid": "child",
    "kids": "child",
    "children": "child",
    "old": "age",
    "storm": "weather",
    "rain": "weather",
}


@dataclass(frozen=True)
class Section:
    source: str
    heading: str
    text: str
    tokens: Counter


def _tokenize(text: str) -> list[str]:
    text = re.sub(r"\be-bikes?\b", "ebike", text.lower())
    tokens = []
    for word in re.findall(r"[a-z0-9]+", text):
        if word in STOP_WORDS:
            continue
        if word not in SYNONYMS and len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        tokens.append(SYNONYMS.get(word, word))
    return tokens


def _load_sections(policies_dir: Path) -> list[Section]:
    """Split each Markdown file into one section per `#`/`##` heading."""
    sections = []
    for path in sorted(policies_dir.glob("*.md")):
        parts = re.split(r"^(#{1,2} .+)$", path.read_text(encoding="utf-8"), flags=re.M)
        for heading, body in zip(parts[1::2], parts[2::2]):
            heading = heading.lstrip("# ").strip()
            body = body.strip()
            if body:
                # The heading counts twice: it says best what a section is about.
                tokens = Counter(_tokenize(f"{heading} {heading} {body}"))
                sections.append(Section(path.name, heading, body, tokens))
    return sections


def search_policies(query: str, top_k: int = 3, policies_dir: Path = POLICIES_DIR) -> dict:
    """Rank the policy sections with BM25 and return the best `top_k`."""
    sections = _load_sections(policies_dir)
    query_tokens = set(_tokenize(query))
    avg_len = sum(sum(s.tokens.values()) for s in sections) / len(sections)

    scored = []
    for section in sections:
        length = sum(section.tokens.values())
        score = 0.0
        for token in query_tokens:
            tf = section.tokens[token]
            if tf:
                df = sum(1 for s in sections if token in s.tokens)
                idf = math.log(1 + (len(sections) - df + 0.5) / (df + 0.5))
                score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / avg_len))
        if score > 0:
            scored.append((score, section))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return {"found": False, "message": "No policy section matches this question."}
    return {
        "found": True,
        "results": [
            {"source": s.source, "section": s.heading, "text": s.text, "score": round(score, 2)}
            for score, s in scored[:top_k]
        ],
    }


# --- get_booking -------------------------------------------------------------


def _load_bookings(bookings_file: Path) -> dict[str, dict]:
    bookings = json.loads(bookings_file.read_text(encoding="utf-8"))
    return {b["id"]: b for b in bookings}


def _normalize_id(booking_id: str) -> str:
    """`cw-1002`, ` CW 1002 ` and `cw1002` all mean `CW-1002`."""
    cleaned = str(booking_id).strip().upper()
    match = re.fullmatch(r"CW[-\s]?(\d+)", cleaned)
    return f"CW-{match.group(1)}" if match else cleaned


def _not_found(booking_id: str) -> dict:
    return {
        "found": False,
        "booking_id": str(booking_id),
        "message": "No booking with this number exists.",
    }


def get_booking(booking_id: str, bookings_file: Path = BOOKINGS_FILE) -> dict:
    normalized = _normalize_id(booking_id)
    booking = _load_bookings(bookings_file).get(normalized)
    if booking is None:
        return _not_found(booking_id)
    return {"found": True, "booking": booking}


# --- calculate_refund --------------------------------------------------------

REFUND_METHODS = {
    "card": "Refunded to the same card within 5 working days.",
    "voucher": "Refunded as a new voucher of the same value, never as money.",
}


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _refund_percent(time_before_start: timedelta) -> int:
    """The cancellation table from cancellations.md. The limits are inclusive."""
    if time_before_start >= timedelta(hours=48):
        return 100
    if time_before_start >= timedelta(hours=24):
        return 50
    return 0


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=SHOP_TZ)


def calculate_refund(
    booking_id: str,
    cancel_at: str | None = None,
    now: datetime = NOW,
    bookings_file: Path = BOOKINGS_FILE,
) -> dict:
    """Work out what a customer gets back when cancelling at `cancel_at` (default: now)."""
    normalized = _normalize_id(booking_id)
    booking = _load_bookings(bookings_file).get(normalized)
    if booking is None:
        return _not_found(booking_id)

    if cancel_at is None:
        when = now
    else:
        try:
            when = _parse_datetime(cancel_at)
        except ValueError:
            return {
                "found": True,
                "booking_id": normalized,
                "error": f"'{cancel_at}' is not a valid ISO 8601 date and time.",
            }
        if when < now:
            return {
                "found": True,
                "booking_id": normalized,
                "error": f"The cancellation time cannot be in the past (now is {now.isoformat()}).",
            }

    no_refund = {
        "found": True,
        "booking_id": normalized,
        "status": booking["status"],
        "cancellable": False,
        "total_refund_eur": 0.0,
    }
    if booking["status"] != "confirmed":
        return no_refund | {
            "reason": f"Bookings with the status '{booking['status']}' cannot be cancelled, "
            "and nothing is refunded for them.",
        }

    start = datetime.fromisoformat(booking["start"])
    if when >= start:
        return no_refund | {"reason": "The rental has already started at this time."}

    time_before_start = start - when
    percent = _refund_percent(time_before_start)
    rental_refund = Decimal(str(booking["rental_price_eur"])) * percent / 100
    deposit_refund = Decimal(str(booking["deposit_eur"]))

    return {
        "found": True,
        "booking_id": normalized,
        "status": booking["status"],
        "cancellable": True,
        "cancelled_at": when.isoformat(),
        "hours_before_start": round(time_before_start.total_seconds() / 3600, 2),
        "refund_percent_of_rental": percent,
        "rental_refund_eur": _money(rental_refund),
        "deposit_refund_eur": _money(deposit_refund),
        "booking_fee_not_refunded_eur": booking["booking_fee_eur"],
        "total_refund_eur": _money(rental_refund + deposit_refund),
        "paid_with": booking["paid_with"],
        "refund_method": REFUND_METHODS[booking["paid_with"]],
    }


# --- get_opening_hours -------------------------------------------------------

CLOSED_DATES = {(12, 25), (12, 26), (1, 1)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def get_opening_hours(day: str | None = None, now: datetime = NOW) -> dict:
    """Opening hours from faq.md for a date or a weekday name (the next such day). Default: today."""
    today = now.date()
    if day is None:
        when = today
    elif day.strip().lower() in WEEKDAYS:
        days_ahead = (WEEKDAYS.index(day.strip().lower()) - today.weekday()) % 7
        when = today + timedelta(days=days_ahead)
    else:
        try:
            when = datetime.fromisoformat(day.strip()).date()
        except ValueError:
            return {"error": f"'{day}' is not a weekday name or an ISO 8601 date."}

    result = {"date": when.isoformat(), "weekday": WEEKDAYS[when.weekday()].capitalize()}
    if (when.month, when.day) in CLOSED_DATES:
        return result | {"open": False, "reason": "Closed on 25 and 26 December and on 1 January."}
    if 4 <= when.month <= 10:
        return result | {"open": True, "opens": "08:00", "closes": "20:00"}
    if when.weekday() == 6:
        return result | {"open": False, "reason": "Closed on Sundays from November to March."}
    return result | {"open": True, "opens": "09:00", "closes": "17:00"}


# --- calculate_price ---------------------------------------------------------

BIKE_PRICES = {
    "city": {"hour": 4, "day": 20, "deposit": 50},
    "e-bike": {"hour": 9, "day": 40, "deposit": 150},
    "kids": {"hour": 2, "day": 10, "deposit": 30},
}
CHILD_SEAT_PER_DAY = 3
ONLINE_BOOKING_FEE = 2
MAX_HOURLY_RENTAL = 6
LONG_RENTAL_DAYS = 7
LONG_RENTAL_DISCOUNT = Decimal("0.15")


def calculate_price(
    bike_type: str, duration_hours: float, bikes: int = 1, child_seats: int = 0
) -> dict:
    """Price a new rental with the rules from prices.md."""
    prices = BIKE_PRICES.get(bike_type)
    if prices is None:
        return {"error": f"Unknown bike type '{bike_type}'. Use one of: {', '.join(BIKE_PRICES)}."}
    if duration_hours <= 0 or bikes < 1 or child_seats < 0:
        return {"error": "The duration and the number of bikes must be positive."}

    # Assumption: a rental over 24 hours pays for every started day.
    days = math.ceil(duration_hours / 24)
    if duration_hours > MAX_HOURLY_RENTAL:
        bike_price = Decimal(prices["day"] * days * bikes)
        charged_as = f"{days} day(s)"
    else:
        hours = math.ceil(duration_hours)
        bike_price = Decimal(prices["hour"] * hours * bikes)
        charged_as = f"{hours} started hour(s)"

    # The README defines the rental price as including add-ons, so the discount covers them too.
    rental_price = bike_price + CHILD_SEAT_PER_DAY * days * child_seats
    has_discount = days >= LONG_RENTAL_DAYS and duration_hours > MAX_HOURLY_RENTAL
    if has_discount:
        rental_price *= 1 - LONG_RENTAL_DISCOUNT

    deposit = prices["deposit"] * bikes
    return {
        "bike_type": bike_type,
        "bikes": bikes,
        "child_seats": child_seats,
        "charged_as": charged_as,
        "long_rental_discount_applied": has_discount,
        "rental_price_eur": _money(rental_price),
        "deposit_eur": float(deposit),
        "online_booking_fee_eur": float(ONLINE_BOOKING_FEE),
        "total_to_pay_online_eur": _money(rental_price + deposit + ONLINE_BOOKING_FEE),
        "total_to_pay_in_shop_eur": _money(rental_price + deposit),
    }
