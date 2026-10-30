"""LangChain wrappers around the plain functions in tools_core.

The docstrings and argument descriptions are what the model reads when it
decides which tool to call, so they are part of the agent's instructions.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from agent import tools_core


@tool
def search_policies(query: str) -> str:
    """Search the CityWheels rules: opening hours, location, prices, deposits, age limits,
    payment, helmets, cancellations, weather, accidents and more.

    Args:
        query: What to look for, written in English, because the documents are in English.
    """
    return json.dumps(tools_core.search_policies(query))


@tool
def get_booking(booking_id: str) -> str:
    """Get the details of one booking, or a "not found" result if it does not exist.

    Args:
        booking_id: The booking number, for example CW-1001.
    """
    return json.dumps(tools_core.get_booking(booking_id))


@tool
def calculate_refund(booking_id: str, cancel_at: str | None = None) -> str:
    """Calculate what a customer gets back if they cancel a booking. Always use this for
    refund amounts; never work them out yourself. It does not cancel anything.

    Args:
        booking_id: The booking number, for example CW-1001.
        cancel_at: ISO 8601 date and time of a future cancellation, for example
            2026-06-11T09:00:00+02:00. Leave it out to cancel now.
    """
    return json.dumps(tools_core.calculate_refund(booking_id, cancel_at))


@tool
def get_opening_hours(day: str | None = None) -> str:
    """Get the opening hours of one day, with the season and closed days already applied.
    Always use this when a customer asks when the shop is open.

    Args:
        day: A weekday name such as Saturday (means the next such day) or a date such as
            2026-12-20. For a day in another month, such as "a Sunday in December", pass the
            date of such a day. Leave it out for today.
    """
    return json.dumps(tools_core.get_opening_hours(day))


@tool
def calculate_price(
    bike_type: str, duration_hours: float, bikes: int = 1, child_seats: int = 0
) -> str:
    """Calculate the price of a new rental, including any discount, the deposit and the
    booking fee. Always use this when a price needs any arithmetic.

    Args:
        bike_type: One of city, e-bike or kids.
        duration_hours: Length of the rental in hours. One day is 24 hours.
        bikes: How many bikes.
        child_seats: How many child seats.
    """
    return json.dumps(tools_core.calculate_price(bike_type, duration_hours, bikes, child_seats))


TOOLS = [search_policies, get_booking, calculate_refund, get_opening_hours, calculate_price]
