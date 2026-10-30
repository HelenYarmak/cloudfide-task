import json
from datetime import datetime

import pytest

from agent import tools_core as core

ALL_BOOKINGS = json.loads(core.BOOKINGS_FILE.read_text(encoding="utf-8"))
EARLIER = datetime(2026, 6, 1, 9, 0, tzinfo=core.SHOP_TZ)


# --- calculate_refund: amounts for every booking, worked out by hand -----------


@pytest.mark.parametrize(
    "booking_id, percent, rental, deposit, total",
    [
        ("CW-1001", 100, 40.0, 50.0, 90.0),  # 73 hours before the start
        ("CW-1002", 50, 40.0, 300.0, 340.0),  # 33 hours
        ("CW-1003", 0, 0.0, 50.0, 50.0),  # 6 hours
        ("CW-1004", 100, 23.0, 50.0, 73.0),  # 240 hours, voucher
        ("CW-1007", 100, 36.0, 150.0, 186.0),  # exactly 48 hours
        ("CW-1009", 100, 119.0, 50.0, 169.0),  # 121 hours
        ("CW-1010", 50, 20.0, 150.0, 170.0),  # exactly 24 hours
    ],
)
def test_refund_of_confirmed_bookings(booking_id, percent, rental, deposit, total):
    result = core.calculate_refund(booking_id)
    assert result["cancellable"] is True
    assert result["refund_percent_of_rental"] == percent
    assert result["rental_refund_eur"] == rental
    assert result["deposit_refund_eur"] == deposit
    assert result["total_refund_eur"] == total


@pytest.mark.parametrize(
    "cancel_at, percent",
    [
        ("2026-06-10T08:59:00+02:00", 100),  # 48 h 1 min before the start of CW-1007
        ("2026-06-10T09:00:00+02:00", 100),  # exactly 48 h
        ("2026-06-10T09:01:00+02:00", 50),  # 47 h 59 min
        ("2026-06-11T09:00:00+02:00", 50),  # exactly 24 h
        ("2026-06-11T09:01:00+02:00", 0),  # 23 h 59 min
        ("2026-06-12T08:59:00+02:00", 0),  # 1 min
    ],
)
def test_refund_limits_are_inclusive(cancel_at, percent):
    result = core.calculate_refund("CW-1007", cancel_at, now=EARLIER)
    assert result["refund_percent_of_rental"] == percent


def test_booking_fee_is_never_refunded():
    result = core.calculate_refund("CW-1001")
    assert result["booking_fee_not_refunded_eur"] == 2.0
    assert result["total_refund_eur"] == result["rental_refund_eur"] + result["deposit_refund_eur"]


def test_deposit_is_refunded_in_full_even_at_zero_percent():
    result = core.calculate_refund("CW-1003")
    assert result["refund_percent_of_rental"] == 0
    assert result["deposit_refund_eur"] == 50.0


@pytest.mark.parametrize(
    "booking_id, status",
    [("CW-1005", "cancelled"), ("CW-1006", "completed"), ("CW-1008", "in_progress")],
)
def test_other_statuses_cannot_be_cancelled_and_refund_nothing(booking_id, status):
    result = core.calculate_refund(booking_id)
    assert result["cancellable"] is False
    assert result["status"] == status
    assert result["total_refund_eur"] == 0.0


def test_card_refund_goes_back_to_the_card():
    result = core.calculate_refund("CW-1001")
    assert result["paid_with"] == "card"
    assert "same card within 5 working days" in result["refund_method"]


def test_voucher_refund_is_a_new_voucher():
    result = core.calculate_refund("CW-1004")
    assert result["paid_with"] == "voucher"
    assert "never as money" in result["refund_method"]


def test_refund_of_unknown_booking_is_not_found():
    result = core.calculate_refund("CW-9999")
    assert result["found"] is False
    assert "total_refund_eur" not in result


def test_cancelling_tomorrow_can_lower_the_refund():
    result = core.calculate_refund("CW-1002", "2026-06-11T09:00:00+02:00")
    assert result["refund_percent_of_rental"] == 0
    assert result["total_refund_eur"] == 300.0


def test_cancel_at_without_time_zone_means_shop_time():
    result = core.calculate_refund("CW-1007", "2026-06-11T09:00:00")
    assert result["refund_percent_of_rental"] == 50


def test_cancel_at_in_another_time_zone_is_converted():
    # 07:00 UTC is 09:00 in Warsaw: exactly 24 hours before the start of CW-1007.
    result = core.calculate_refund("CW-1007", "2026-06-11T07:00:00+00:00")
    assert result["refund_percent_of_rental"] == 50


def test_cancel_at_after_the_start_refunds_nothing():
    result = core.calculate_refund("CW-1003", "2026-06-10T15:00:00+02:00")
    assert result["cancellable"] is False
    assert result["total_refund_eur"] == 0.0


def test_cancel_at_in_the_past_is_an_error():
    result = core.calculate_refund("CW-1001", "2026-06-09T09:00:00+02:00")
    assert "error" in result
    assert "total_refund_eur" not in result


def test_invalid_cancel_at_is_an_error():
    result = core.calculate_refund("CW-1001", "tomorrow")
    assert "error" in result
    assert "total_refund_eur" not in result


# --- get_booking ---------------------------------------------------------------


def test_get_booking_returns_details():
    result = core.get_booking("CW-1002")
    assert result["found"] is True
    assert result["booking"]["bike_type"] == "e-bike"
    assert result["booking"]["bikes"] == 2


@pytest.mark.parametrize("variant", ["cw-1002", "  CW-1002 ", "CW1002", "cw 1002"])
def test_get_booking_accepts_common_spellings(variant):
    assert core.get_booking(variant)["booking"]["id"] == "CW-1002"


@pytest.mark.parametrize("bad_id", ["CW-9999", "", "hello", "1002", "CW-", "'; DROP TABLE"])
def test_get_booking_not_found(bad_id):
    result = core.get_booking(bad_id)
    assert result["found"] is False
    assert "booking" not in result


# --- search_policies -----------------------------------------------------------


@pytest.mark.parametrize(
    "query, source, section",
    [
        ("What time do you open on Saturday?", "faq.md", "When are you open?"),
        ("opening hours", "faq.md", "When are you open?"),
        ("how much is an e-bike for a day", "prices.md", "Bike rental"),
        ("age limit for e-bike", "faq.md", "Are there age limits?"),
        ("do I need a helmet", "faq.md", "Do I have to wear a helmet?"),
        ("refund when cancelling a booking", "cancellations.md", "Cancelling a booking"),
        ("storm warning refund", "cancellations.md", "Bad weather"),
        ("late return fee", "prices.md", "Late return"),
        ("do you sell bikes", "faq.md", "Do you sell bikes?"),
        ("deposit for a kids bike", "prices.md", "Deposit"),
    ],
)
def test_search_finds_the_right_section_in_the_top_results(query, source, section):
    results = core.search_policies(query)["results"]
    assert (source, section) in [(r["source"], r["section"]) for r in results]


def test_search_without_any_match_says_so():
    result = core.search_policies("capital Australia")
    assert result["found"] is False


def test_search_limits_the_number_of_results():
    assert len(core.search_policies("bike price deposit", top_k=2)["results"]) == 2


# --- get_opening_hours ---------------------------------------------------------


@pytest.mark.parametrize(
    "day, date, opens, closes",
    [
        (None, "2026-06-10", "08:00", "20:00"),  # today, a Wednesday
        ("Wednesday", "2026-06-10", "08:00", "20:00"),
        ("Saturday", "2026-06-13", "08:00", "20:00"),  # the question from the README
        (" sunday ", "2026-06-14", "08:00", "20:00"),  # open on Sundays in summer
        ("2026-04-01", "2026-04-01", "08:00", "20:00"),  # first day of the season
        ("2026-10-31", "2026-10-31", "08:00", "20:00"),  # last day of the season
        ("2026-11-02", "2026-11-02", "09:00", "17:00"),  # a Monday in winter
        ("2026-03-31", "2026-03-31", "09:00", "17:00"),
    ],
)
def test_opening_hours(day, date, opens, closes):
    result = core.get_opening_hours(day)
    assert result["date"] == date
    assert result["open"] is True
    assert (result["opens"], result["closes"]) == (opens, closes)


@pytest.mark.parametrize(
    "day", ["2026-12-20", "2026-12-25", "2026-12-26", "2027-01-01"]
)  # a winter Sunday and the holidays
def test_opening_hours_closed_days(day):
    result = core.get_opening_hours(day)
    assert result["open"] is False
    assert "opens" not in result


def test_opening_hours_rejects_nonsense():
    assert "error" in core.get_opening_hours("someday")


# --- calculate_price -----------------------------------------------------------


@pytest.mark.parametrize("booking", ALL_BOOKINGS, ids=lambda b: b["id"])
def test_price_rules_reproduce_every_example_booking(booking):
    duration = datetime.fromisoformat(booking["end"]) - datetime.fromisoformat(booking["start"])
    result = core.calculate_price(
        booking["bike_type"],
        duration.total_seconds() / 3600,
        booking["bikes"],
        booking["add_ons"].count("child_seat"),
    )
    assert result["rental_price_eur"] == booking["rental_price_eur"]
    assert result["deposit_eur"] == booking["deposit_eur"]


@pytest.mark.parametrize(
    "bike_type, hours, price",
    [
        ("city", 1, 4.0),
        ("city", 1.5, 8.0),  # every started hour
        ("city", 6, 24.0),  # still hourly
        ("city", 6.5, 20.0),  # longer than 6 hours: day price
        ("e-bike", 24, 40.0),
        ("e-bike", 25, 80.0),  # assumption: every started day
        ("kids", 24 * 6, 60.0),
        ("kids", 24 * 7, 59.5),  # 7 days: 15% off
    ],
)
def test_price_rules(bike_type, hours, price):
    assert core.calculate_price(bike_type, hours)["rental_price_eur"] == price


def test_discount_also_covers_add_ons():
    result = core.calculate_price("city", 24 * 7, child_seats=1)
    assert result["rental_price_eur"] == 136.85  # (140 + 21) * 0.85


def test_price_totals_include_deposit_and_fee():
    result = core.calculate_price("e-bike", 24, bikes=2)
    assert result["total_to_pay_online_eur"] == 382.0
    assert result["total_to_pay_in_shop_eur"] == 380.0


@pytest.mark.parametrize(
    "args", [("tandem", 24), ("city", 0), ("city", -1), ("city", 24, 0), ("city", 24, 1, -1)]
)
def test_price_rejects_bad_input(args):
    assert "error" in core.calculate_price(*args)
