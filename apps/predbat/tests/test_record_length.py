# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init

# Everything record_length reads - snapshotted and restored around the tests
RECORD_LENGTH_STATE = (
    "minutes_now",
    "forecast_minutes",
    "forecast_plan_hours",
    "rate_import_saving_minutes",
    "calculate_horizon_ignore_sessions",
)

# Minutes from midnight today: free session 13:00-15:00, then the regular 23:30-05:30 night window on two nights
FREE_START = 13 * 60
FREE_END = 15 * 60
NIGHT1_START = 23 * 60 + 30
NIGHT2_START = NIGHT1_START + 24 * 60


def _windows():
    """IOG style day with a one-off 0p free session ahead of the regular 7.9p night windows."""
    return [
        {"start": FREE_START, "end": FREE_END, "average": 0.0},
        {"start": NIGHT1_START, "end": NIGHT1_START + 6 * 60, "average": 7.9},
        {"start": NIGHT2_START, "end": NIGHT2_START + 6 * 60, "average": 7.9},
    ]


def _check(my_predbat, name, charge_window, charge_limit, best_price, ignore, saving_minutes, expected_abs):
    """Run record_length and compare the absolute end minute against expected_abs."""
    my_predbat.calculate_horizon_ignore_sessions = ignore
    my_predbat.rate_import_saving_minutes = saving_minutes
    got = my_predbat.record_length(charge_window, charge_limit, best_price) + my_predbat.minutes_now
    if got != expected_abs:
        print("ERROR: record_length {}: expected end {} got {}".format(name, my_predbat.time_abs_str(expected_abs), my_predbat.time_abs_str(got)))
        return 1
    return 0


def run_record_length_tests(my_predbat):
    """
    record_length anchors the plan end to the first charge window at best_price. A one-off free session
    must not become that anchor when calculate_horizon_ignore_sessions is on, and nothing changes when it is off.
    """
    print("**** Running record_length tests ****")
    saved = {name: getattr(my_predbat, name) for name in RECORD_LENGTH_STATE}
    failed = 0
    try:
        my_predbat.minutes_now = 10 * 60
        my_predbat.forecast_minutes = 48 * 60
        my_predbat.forecast_plan_hours = 24
        free_minutes = set(range(FREE_START, FREE_END))
        windows = _windows()
        limits = [10.0, 10.0, 10.0]

        # Off (default): the 0p free session anchors the plan, ending 24 hours after it starts (tomorrow 13:00)
        failed |= _check(my_predbat, "off anchors to free session", windows, limits, 0.0, False, free_minutes, FREE_START + 24 * 60)

        # On: the free session is skipped, the plan runs until the start of the second night window (tomorrow 23:30)
        failed |= _check(my_predbat, "on skips free session", windows, limits, 0.0, True, free_minutes, NIGHT2_START)

        # On but no session minutes known: identical to the off behaviour
        failed |= _check(my_predbat, "on without sessions", windows, limits, 0.0, True, set(), FREE_START + 24 * 60)

        # On, regular day with no free session: anchored to tonight's window either way
        regular = windows[1:]
        failed |= _check(my_predbat, "regular day off", regular, [10.0, 10.0], 7.9, False, set(), NIGHT2_START)
        failed |= _check(my_predbat, "regular day on", regular, [10.0, 10.0], 7.9, True, set(), NIGHT2_START)

        # On, but only the free session window charges: no regular window to anchor to, so best_price is left alone
        # and with no anchor the plan falls back to the full forecast
        failed |= _check(my_predbat, "on, regular windows not charging", windows, [10.0, 0, 0], 0.0, True, free_minutes, my_predbat.minutes_now + my_predbat.forecast_minutes)

        # On, a session window that is already the in-progress one is still skipped
        my_predbat.minutes_now = 14 * 60
        failed |= _check(my_predbat, "on, inside free session", windows, limits, 0.0, True, free_minutes, NIGHT2_START)
        failed |= _check(my_predbat, "off, inside free session", windows, limits, 0.0, False, free_minutes, FREE_END + 24 * 60)
    finally:
        for name, value in saved.items():
            setattr(my_predbat, name, value)

    return failed
