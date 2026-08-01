# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt: off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init
# fmt: on

"""Unit tests for update_time() and the clock frames the rest of Predbat measures against.

Predbat keeps two views of the current time: ``now``/``midnight`` (naive) and
``now_utc``/``midnight_utc`` (timezone aware). Large parts of the codebase measure a time in
minutes from one and compare it against a time measured from the other, which is only sound
while the two describe the same wall clock.

``now`` used to come from ``datetime.now()`` - the *host's* timezone - while ``now_utc`` came
from the timezone configured in apps.yaml. On a normal Home Assistant install those are the
same and nothing goes wrong, which is why the mismatch went unnoticed. In a container running
UTC with ``timezone: Europe/London`` they differ by an hour during BST, and for the hour before
local midnight ``midnight`` and ``midnight_utc`` land on *different dates* - so a manual
override slot resolved against one and compared against the other is a full day out and gets
silently rejected.

These tests fix the invariant in place by driving update_time() with deliberately extreme
configured timezones, so a regression shows up on any host rather than only between 23:00 and
midnight in summer.
"""

from datetime import timedelta

# Timezones chosen to sit either side of the host on any machine, so at least one of them puts
# the configured wall clock on a different calendar date to the host's.
EXTREME_TIMEZONES = [
    "Pacific/Apia",  # UTC+13, among the furthest zones ahead
    "Pacific/Midway",  # UTC-11, near the furthest behind
    "Asia/Kolkata",  # UTC+5:30, a half-hour offset
    "Europe/London",
    "UTC",
]


class TimezoneOverride:
    """Runs update_time() under a given configured timezone and restores the fixture afterwards."""

    def __init__(self, my_predbat, timezone):
        """Record the timezone to apply to the shared fixture."""
        self.base = my_predbat
        self.timezone = timezone

    def __enter__(self):
        """Apply the timezone and refresh the fixture's clock."""
        self.saved_timezone = self.base.args.get("timezone", None)
        self.base.args["timezone"] = self.timezone
        self.base.update_time()
        return self.base

    def __exit__(self, exc_type, exc_value, traceback):
        """Restore the original timezone and clock."""
        if self.saved_timezone is None:
            self.base.args.pop("timezone", None)
        else:
            self.base.args["timezone"] = self.saved_timezone
        self.base.update_time()
        return False


def _check_clock_invariants(base, timezone):
    """Assert the naive and aware clocks describe the same wall clock, returning an error string."""
    if base.now.replace(tzinfo=None) != base.now_utc.replace(tzinfo=None):
        return "now {} and now_utc {} describe different wall clocks under {}".format(base.now, base.now_utc, timezone)

    if base.midnight.date() != base.midnight_utc.date():
        return "midnight {} and midnight_utc {} fall on different dates under {}".format(base.midnight.date(), base.midnight_utc.date(), timezone)

    # minutes_now must mean the same thing measured from either midnight
    minutes_from_naive = int((base.now - base.midnight).total_seconds() / 60)
    minutes_from_aware = int((base.now_utc - base.midnight_utc).total_seconds() / 60)
    if minutes_from_naive != minutes_from_aware:
        return "minutes since midnight disagree under {}: {} naive vs {} aware".format(timezone, minutes_from_naive, minutes_from_aware)

    # And it must match the published value, allowing for the PREDICT_STEP rounding
    if abs(minutes_from_naive - base.minutes_now) > 5:
        return "minutes_now {} does not match {} minutes since midnight under {}".format(base.minutes_now, minutes_from_naive, timezone)

    if base.minutes_to_midnight != 24 * 60 - base.minutes_now:
        return "minutes_to_midnight {} is inconsistent with minutes_now {} under {}".format(base.minutes_to_midnight, base.minutes_now, timezone)
    return None


def _test_clock_frames_agree(my_predbat):
    """The naive and aware clocks agree whatever timezone is configured.

    Regression test: ``now`` came from the host clock and ``now_utc`` from the configured
    timezone, so on a host whose timezone differed the two could sit on different dates.
    """
    print("Test: update_time keeps the naive and aware clocks in step")
    for timezone in EXTREME_TIMEZONES:
        with TimezoneOverride(my_predbat, timezone) as base:
            error = _check_clock_invariants(base, timezone)
            if error:
                print("ERROR: {}".format(error))
                return 1
    return 0


def _test_manual_slot_round_trip(my_predbat):
    """A manual override slot selected from the dropdown sticks, whatever timezone is configured.

    This is the user-visible symptom of the clock mismatch: manual_times resolves the label
    against one midnight and compares it against the other, so once they fall on different dates
    every future slot is rejected and the selection silently reverts to "off".
    """
    print("Test: a manual override slot survives the dropdown round trip in any timezone")
    for timezone in EXTREME_TIMEZONES:
        with TimezoneOverride(my_predbat, timezone) as base:
            base.manual_select("manual_charge", "off")

            # Build the label for a slot six hours out - always inside the ~48 hour option window
            target = base.now_utc + timedelta(hours=6)
            target = target.replace(minute=(target.minute // 30) * 30, second=0, microsecond=0)
            label = target.strftime("%a %H:%M")

            options = base.config_index.get("manual_charge", {}).get("options", [])
            if label not in options:
                print("ERROR: {} is not offered under {}, options start {}".format(label, timezone, options[:4]))
                base.manual_select("manual_charge", "off")
                return 1

            base.manual_select("manual_charge", label)
            value = base.config_index.get("manual_charge").get("value", "")
            base.manual_select("manual_charge", "off")

            if label not in value:
                print("ERROR: selecting {} under {} left the value as {}".format(label, timezone, value))
                return 1
    return 0


def _test_manual_rate_round_trip(my_predbat):
    """A manual rate override also survives the round trip in any timezone.

    manual_rates carried the same mismatch as manual_times, so it is pinned the same way.
    """
    print("Test: a manual rate override survives the round trip in any timezone")
    for timezone in EXTREME_TIMEZONES:
        with TimezoneOverride(my_predbat, timezone) as base:
            base.manual_select("manual_import_rates", "off")

            target = base.now_utc + timedelta(hours=6)
            target = target.replace(minute=(target.minute // 30) * 30, second=0, microsecond=0)
            label = target.strftime("%a %H:%M")

            rate_minutes = base.manual_rates("manual_import_rates", new_value="+{}=12.5".format(label), default_rate=0)
            base.manual_select("manual_import_rates", "off")

            if not rate_minutes:
                print("ERROR: the rate override for {} was dropped under {}".format(label, timezone))
                return 1
            if 12.5 not in set(rate_minutes.values()):
                print("ERROR: expected a 12.5p override under {}, got {}".format(timezone, sorted(set(rate_minutes.values()))))
                return 1
    return 0


def _test_clock_skew_applies_to_both(my_predbat):
    """A configured clock skew shifts both clocks together rather than pulling them apart."""
    print("Test: clock_skew moves the naive and aware clocks together")
    saved_skew = my_predbat.args.get("clock_skew", None)
    try:
        my_predbat.args["clock_skew"] = 90
        my_predbat.update_time()

        error = _check_clock_invariants(my_predbat, "Europe/London with a 90 minute skew")
        if error:
            print("ERROR: {}".format(error))
            return 1

        skewed_now = my_predbat.now
        my_predbat.args["clock_skew"] = 0
        my_predbat.update_time()

        # The skewed clock must be ahead of the un-skewed one by roughly the configured amount
        drift = (skewed_now - my_predbat.now).total_seconds() / 60
        if abs(drift - 90) > 10:
            print("ERROR: expected the skew to move the clock 90 minutes, moved {}".format(round(drift)))
            return 1
    finally:
        if saved_skew is None:
            my_predbat.args.pop("clock_skew", None)
        else:
            my_predbat.args["clock_skew"] = saved_skew
        my_predbat.update_time()
    return 0


def test_update_time(my_predbat=None):
    """Run the update_time test suite, returning non-zero if any sub-test failed."""
    sub_tests = [
        ("clock_frames", _test_clock_frames_agree, "Naive and aware clocks agree"),
        ("manual_slot", _test_manual_slot_round_trip, "Manual slot round trip"),
        ("manual_rate", _test_manual_rate_round_trip, "Manual rate round trip"),
        ("clock_skew", _test_clock_skew_applies_to_both, "Clock skew applies to both clocks"),
    ]

    print("\n" + "=" * 70)
    print("UPDATE TIME TEST SUITE")
    print("=" * 70)

    failed = 0
    passed = 0

    for test_name, test_func, test_desc in sub_tests:
        print("\n[{}] {}".format(test_name, test_desc))
        print("-" * 70)
        try:
            test_result = test_func(my_predbat)
        except Exception as e:
            import traceback

            traceback.print_exc()
            print("✗ FAILED: {} raised {}".format(test_name, e))
            failed += 1
            continue

        if test_result:
            print("✗ FAILED: {}".format(test_name))
            failed += 1
        else:
            print("✓ PASSED: {}".format(test_name))
            passed += 1

    print("\n" + "=" * 70)
    print("UPDATE TIME RESULTS: {} passed, {} failed".format(passed, failed))
    print("=" * 70)
    return failed
