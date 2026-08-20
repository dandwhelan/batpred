# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init
# fmt on

"""
Tests for the Strømligning rate provider.

stromligning.py turns a Danish supplier's 15-minute price attributes into the per-minute rate map
the planner runs on, and is wired into both fetch.py and compare.py - but had no test at all, which
left it at 9% coverage. It is the same shape as Nordpool and Energidataservice, both of which are
tested, so this follows the same pattern: publish sensor attributes, read back the minute map.
"""

from datetime import timedelta, timezone

from energydataservice import Energidataservice
from stromligning import Stromligning


def _check(name, value, expected):
    """Compare a value to its expectation, printing a diagnostic and returning 1 on mismatch."""
    if value != expected:
        print("ERROR: {}: got {!r}, expected {!r}".format(name, value, expected))
        return 1
    return 0


def _check_true(name, value):
    """Assert a value is truthy, printing a diagnostic and returning 1 when it is not."""
    if not value:
        print("ERROR: {}: expected a truthy value, got {!r}".format(name, value))
        return 1
    return 0


def _quarters(midnight, start_minute, count, first_price=1.0, step=0.5):
    """Build a run of 15-minute price entries in the shape Strømligning publishes."""
    entries = []
    price = first_price
    for index in range(count):
        start = midnight + timedelta(minutes=start_minute + index * 15)
        entries.append({"price": price, "start": start.isoformat(), "end": (start + timedelta(minutes=15)).isoformat()})
        price += step
    return entries


def _publish(my_predbat, entity_id, unit="øre/kWh", **attributes):
    """Publish one Strømligning sensor, as a state plus the attributes the reader looks for."""
    stored = {"state": 0.0}
    if unit is not None:
        stored["unit_of_measurement"] = unit
    stored.update(attributes)
    my_predbat.ha_interface.dummy_items[entity_id] = stored


def test_no_sensors_returns_nothing(my_predbat):
    """With no sensors configured there is no rate data, and nothing should blow up."""
    print("*** Running test: no Strømligning sensors returns nothing")
    failed = 0
    failed |= _check("no sensors: empty result", my_predbat.fetch_stromligning_rates(None, None), {})
    return failed


def test_todays_prices_become_per_minute_rates(my_predbat):
    """Each 15-minute price fills its own fifteen minutes of the rate map."""
    print("*** Running test: today's prices become per-minute rates")
    failed = 0
    midnight = my_predbat.midnight_utc

    _publish(my_predbat, "sensor.stromligning_today", prices_today=_quarters(midnight, 0, 4, first_price=1.0, step=1.0))
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)

    failed |= _check("today: minutes filled", len(rates), 60)
    failed |= _check("today: first quarter", [rates[minute] for minute in (0, 7, 14)], [1.0, 1.0, 1.0])
    failed |= _check("today: second quarter", rates[15], 2.0)
    failed |= _check("today: third quarter", rates[30], 3.0)
    failed |= _check("today: fourth quarter", rates[59], 4.0)
    failed |= _check("today: nothing past the last interval", rates.get(60, None), None)
    return failed


def test_prices_attribute_is_a_fallback(my_predbat):
    """Integrations that publish a plain 'prices' list are read too, for both days."""
    print("*** Running test: the generic prices attribute is a fallback")
    failed = 0
    midnight = my_predbat.midnight_utc

    _publish(my_predbat, "sensor.stromligning_today", prices=_quarters(midnight, 0, 2, first_price=5.0, step=0.0))
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)
    failed |= _check("fallback: today read from prices", rates.get(0), 5.0)

    _publish(my_predbat, "sensor.stromligning_tomorrow", prices=_quarters(midnight, 24 * 60, 2, first_price=9.0, step=0.0))
    rates = my_predbat.fetch_stromligning_rates(None, "sensor.stromligning_tomorrow")
    failed |= _check("fallback: tomorrow read from prices", rates.get(24 * 60), 9.0)
    return failed


def test_both_days_are_merged(my_predbat):
    """Today's and tomorrow's sensors are read into one continuous rate map."""
    print("*** Running test: today and tomorrow are merged")
    failed = 0
    midnight = my_predbat.midnight_utc

    _publish(my_predbat, "sensor.stromligning_today", prices_today=_quarters(midnight, 0, 2, first_price=2.0, step=0.0))
    _publish(my_predbat, "sensor.stromligning_tomorrow", prices_tomorrow=_quarters(midnight, 24 * 60, 2, first_price=7.0, step=0.0))

    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", "sensor.stromligning_tomorrow")
    failed |= _check("merged: today's rate", rates.get(0), 2.0)
    failed |= _check("merged: tomorrow's rate", rates.get(24 * 60), 7.0)
    return failed


def test_kroner_prices_are_scaled_to_ore(my_predbat):
    """
    A feed quoting kroner is scaled by 100, because Predbat works in minor units throughout.

    Miss this and every rate is a hundred times too cheap, which would make the planner import
    around the clock.
    """
    print("*** Running test: kroner prices are scaled to øre")
    failed = 0
    midnight = my_predbat.midnight_utc
    entries = _quarters(midnight, 0, 1, first_price=1.5, step=0.0)

    _publish(my_predbat, "sensor.stromligning_today", unit="kr/kWh", prices_today=entries)
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)
    failed |= _check("kroner: scaled by 100", rates.get(0), 150.0)

    # Case should not matter
    _publish(my_predbat, "sensor.stromligning_today", unit="KR/kWh", prices_today=entries)
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)
    failed |= _check("kroner: case-insensitive", rates.get(0), 150.0)

    # Already in minor units: left alone
    _publish(my_predbat, "sensor.stromligning_today", unit="øre/kWh", prices_today=entries)
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)
    failed |= _check("øre: unscaled", rates.get(0), 1.5)

    # No unit published at all: assume minor units rather than inventing a scale
    _publish(my_predbat, "sensor.stromligning_today", unit=None, prices_today=entries)
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)
    failed |= _check("missing unit: unscaled", rates.get(0), 1.5)
    return failed


def test_out_of_order_entries_are_sorted(my_predbat):
    """Entries that arrive out of order still land on the right minutes."""
    print("*** Running test: out-of-order entries are sorted")
    failed = 0
    midnight = my_predbat.midnight_utc
    entries = _quarters(midnight, 0, 3, first_price=1.0, step=1.0)
    entries.reverse()

    _publish(my_predbat, "sensor.stromligning_today", prices_today=entries)
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)
    failed |= _check("sorted: first quarter", rates.get(0), 1.0)
    failed |= _check("sorted: last quarter", rates.get(30), 3.0)
    return failed


def test_unparseable_entries_are_skipped(my_predbat):
    """
    One malformed entry must not cost the whole feed.

    A supplier publishing a null or oddly formatted timestamp for a single interval is a bad
    afternoon, not a reason to plan with no tariff at all.
    """
    print("*** Running test: unparseable entries are skipped")
    failed = 0
    midnight = my_predbat.midnight_utc

    good = _quarters(midnight, 0, 1, first_price=3.0, step=0.0)
    bad = [
        {"price": 99.0, "start": "not-a-timestamp", "end": "also-not"},
        {"price": 98.0, "start": None, "end": None},
        {"price": 97.0, "start": (midnight + timedelta(minutes=15)).isoformat()},
    ]

    _publish(my_predbat, "sensor.stromligning_today", prices_today=bad + good)
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)

    failed |= _check("skipped: good entry kept", rates.get(0), 3.0)
    failed |= _check("skipped: only the good entry survives", len(rates), 15)
    return failed


def test_a_zulu_timestamp_is_understood(my_predbat):
    """Timestamps ending in Z are the same instant as +00:00, and must land on the same minutes."""
    print("*** Running test: a Zulu timestamp is understood")
    failed = 0
    midnight = my_predbat.midnight_utc
    start = midnight + timedelta(minutes=60)

    # midnight_utc is local midnight, so convert before stamping a Z on it - the point of the test
    # is that "Z" and "+00:00" name the same instant, not that they name the same wall clock
    def zulu(when):
        """Format an aware datetime as a UTC timestamp with a Z suffix."""
        return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entry = [{"price": 4.0, "start": zulu(start), "end": zulu(start + timedelta(minutes=15))}]
    _publish(my_predbat, "sensor.stromligning_today", prices_today=entry)
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)

    failed |= _check("zulu: parsed onto the right minute", rates.get(60), 4.0)
    failed |= _check("zulu: fills its quarter hour", len(rates), 15)
    return failed


def test_an_interval_ending_at_midnight_rolls_over(my_predbat):
    """
    A 23:00-00:00 interval whose end is stamped on the same day still covers the last hour.

    Some feeds write the closing midnight as the same date rather than the next, which would
    otherwise produce an interval that ends before it starts and fill nothing.
    """
    print("*** Running test: an interval ending at midnight rolls over")
    failed = 0
    midnight = my_predbat.midnight_utc
    start = midnight + timedelta(minutes=23 * 60 + 45)

    entry = [{"price": 6.0, "start": start.isoformat(), "end": midnight.isoformat()}]
    _publish(my_predbat, "sensor.stromligning_today", prices_today=entry)
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)

    failed |= _check("rollover: last quarter of the day filled", rates.get(23 * 60 + 45), 6.0)
    failed |= _check("rollover: right up to midnight", rates.get(24 * 60 - 1), 6.0)
    failed |= _check("rollover: not past midnight", rates.get(24 * 60, None), None)
    return failed


def test_rates_outside_the_forecast_window_are_dropped(my_predbat):
    """Prices further out than Predbat plans for are discarded rather than growing the map."""
    print("*** Running test: rates outside the forecast window are dropped")
    failed = 0
    midnight = my_predbat.midnight_utc
    horizon_days = my_predbat.forecast_days + 1

    beyond = _quarters(midnight, (horizon_days + 1) * 24 * 60, 2, first_price=8.0, step=0.0)
    inside = _quarters(midnight, 0, 1, first_price=2.0, step=0.0)

    _publish(my_predbat, "sensor.stromligning_today", prices_today=inside + beyond)
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)

    failed |= _check("horizon: inside kept", rates.get(0), 2.0)
    failed |= _check("horizon: beyond dropped", len(rates), 15)
    return failed


def test_empty_sensor_attributes_warn_and_return_nothing(my_predbat):
    """A sensor with no price attributes yields no rates rather than a crash."""
    print("*** Running test: empty sensor attributes return nothing")
    failed = 0

    _publish(my_predbat, "sensor.stromligning_today")
    _publish(my_predbat, "sensor.stromligning_tomorrow")
    rates = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", "sensor.stromligning_tomorrow")
    failed |= _check("empty attributes: no rates", rates, {})
    return failed


def test_an_adjust_key_is_accepted(my_predbat):
    """The adjust_key hook is accepted and leaves the rates alone, since nothing implements it yet."""
    print("*** Running test: an adjust key is accepted")
    failed = 0
    midnight = my_predbat.midnight_utc

    _publish(my_predbat, "sensor.stromligning_today", prices_today=_quarters(midnight, 0, 1, first_price=2.5, step=0.0))
    plain = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None)
    adjusted = my_predbat.fetch_stromligning_rates("sensor.stromligning_today", None, adjust_key="is_intelligent_adjusted")

    failed |= _check("adjust key: rates unchanged", adjusted, plain)
    return failed


def test_a_naive_midnight_still_works(my_predbat):
    """
    A naive midnight cannot be subtracted from an aware timestamp, so the reader falls back.

    config_root and the timezone setup differ between deployments, and one that hands over a naive
    midnight must still produce rates rather than a TypeError halfway through the fetch.
    """
    print("*** Running test: a naive midnight still works")
    failed = 0
    midnight = my_predbat.midnight_utc
    naive_midnight = midnight.replace(tzinfo=None)

    entries = _quarters(midnight, 30, 2, first_price=1.0, step=1.0)
    rates = my_predbat._minute_data_stromligning_rates(entries, my_predbat.forecast_days + 1, naive_midnight)

    failed |= _check("naive midnight: first interval", rates.get(30), 1.0)
    failed |= _check("naive midnight: second interval", rates.get(45), 2.0)
    failed |= _check("naive midnight: two quarters filled", len(rates), 30)
    return failed


def test_the_timestamp_parser_matches_the_one_that_shadows_it(my_predbat):
    """
    Stromligning._parse_iso is shadowed, so pin it against the implementation that actually runs.

    PredBat lists Energidataservice before Stromligning in its bases, and both define _parse_iso, so
    the Energidataservice one wins for every Stromligning call. The two are identical today; this
    test is what fails if someone edits one of them and quietly changes the other provider's
    behaviour - or believes they have changed Stromligning's when they have not.
    """
    print("*** Running test: the timestamp parser matches the one that shadows it")
    failed = 0

    shadowed = Stromligning._parse_iso
    effective = type(my_predbat)._parse_iso
    failed |= _check("parser: Energidataservice is the one that runs", effective, Energidataservice._parse_iso)

    samples = [
        None,
        "",
        "2026-08-20T01:00:00+00:00",
        "2026-08-20T01:00:00Z",
        "2026-08-20T01:00:00",
        "2026-08-20",
        "not-a-timestamp",
        12345,
    ]
    for sample in samples:
        failed |= _check("parser: same answer for {!r}".format(sample), shadowed(my_predbat, sample), effective(my_predbat, sample))
    return failed


def run_stromligning_tests(my_predbat):
    """Run the Strømligning rate provider tests, returning non-zero if any failed."""
    print("**** Running Strømligning tests ****")
    failed = 0
    saved_debug = my_predbat.debug_enable
    my_predbat.debug_enable = True  # exercises the debug logging branch too

    try:
        for test in (
            test_no_sensors_returns_nothing,
            test_todays_prices_become_per_minute_rates,
            test_prices_attribute_is_a_fallback,
            test_both_days_are_merged,
            test_kroner_prices_are_scaled_to_ore,
            test_out_of_order_entries_are_sorted,
            test_unparseable_entries_are_skipped,
            test_a_zulu_timestamp_is_understood,
            test_an_interval_ending_at_midnight_rolls_over,
            test_rates_outside_the_forecast_window_are_dropped,
            test_empty_sensor_attributes_warn_and_return_nothing,
            test_an_adjust_key_is_accepted,
            test_a_naive_midnight_still_works,
            test_the_timestamp_parser_matches_the_one_that_shadows_it,
        ):
            failed |= test(my_predbat)
    finally:
        my_predbat.debug_enable = saved_debug
        for entity in ("sensor.stromligning_today", "sensor.stromligning_tomorrow"):
            my_predbat.ha_interface.dummy_items.pop(entity, None)

    return failed
