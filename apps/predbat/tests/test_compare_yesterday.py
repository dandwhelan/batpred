# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init

"""Unit tests for the Compare yesterday replay (compare_yesterday): every tariff planned on yesterday's actual data."""

from datetime import datetime

from fetch import Fetch
from tests.test_compare import _make_compare

MINUTES_NOW = 60
HISTORY_AGO = 24 * 60 + MINUTES_NOW


def _incrementing(per_minute_ago):
    """
    Turn {minutes_ago: kWh used in that minute} into a cumulative series keyed by minutes ago, as load_minutes is
    """
    series = {}
    total = 0.0
    for minutes_ago in range(3 * 24 * 60, -1, -1):
        total += per_minute_ago.get(minutes_ago, 0.0)
        series[minutes_ago] = total
    return series


def _replay_predbat():
    """A Compare on a stub with one hour of 1 kWh load and 2 kWh PV yesterday from 12:00, and yesterday's midnight SoC"""
    cmp, pb = _make_compare()
    pb.minutes_now = MINUTES_NOW
    pb.minutes_since_day_start = lambda: MINUTES_NOW
    pb.plan_interval_minutes = 30
    pb.midnight_utc = datetime(2026, 11, 12)
    pb.soc_kwh_history = {HISTORY_AGO: 4.25, 0: 9.0}
    # Yesterday 12:00 is HISTORY_AGO - 720 minutes ago
    noon_ago = HISTORY_AGO - 12 * 60
    pb.load_minutes = _incrementing({noon_ago - minute: 1.0 / 60 for minute in range(60)})
    pb.pv_today = _incrementing({noon_ago - minute: 2.0 / 60 for minute in range(60)})
    pb.get_from_incrementing = lambda data, index, backwards=True: Fetch.get_from_incrementing(pb, data, index, backwards)
    return cmp, pb


def _check(failures, label, condition, detail=""):
    """Record a failed check and print the outcome"""
    if condition:
        print("PASS {}".format(label))
    else:
        print("ERROR {} {}".format(label, detail))
        failures.append(label)


def test_compare_yesterday(my_predbat=None):
    """
    Compare's yesterday replay builds yesterday's actual load and PV, shifts the rates onto it, and plans each tariff on it
    """
    failures = []

    # Replay inputs: yesterday's load and PV land at the same time of day in the plan, on both days of it
    cmp, pb = _replay_predbat()
    replay = cmp.build_replay()
    _check(failures, "Y1 replay built", replay is not None)
    if replay:
        _check(failures, "Y1 shift is minutes since yesterday's midnight", replay["shift"] == HISTORY_AGO, replay["shift"])
        _check(failures, "Y1 start SoC is yesterday's midnight", replay["start_soc"] == 4.25, replay["start_soc"])
        noon_load = sum(replay["load"][minute] for minute in range(12 * 60, 13 * 60, 5))
        _check(failures, "Y1 load is placed at noon", abs(noon_load - 1.0) < 0.001, noon_load)
        _check(failures, "Y1 no load outside noon", sum(replay["load"][minute] for minute in range(0, 12 * 60, 5)) < 0.001)
        second_day = sum(replay["load"][minute] for minute in range(36 * 60, 37 * 60, 5))
        _check(failures, "Y1 the second plan day repeats yesterday", abs(second_day - 1.0) < 0.001, second_day)
        _check(failures, "Y1 day totals", replay["load_kwh"] == 1.0 and replay["pv_kwh"] == 2.0, (replay["load_kwh"], replay["pv_kwh"]))

    # No midnight SoC recorded: no replay
    cmp, pb = _replay_predbat()
    pb.soc_kwh_history = {0: 9.0}
    _check(failures, "Y2 replay skipped without yesterday's midnight SoC", cmp.build_replay() is None)

    # Load history too short: no replay
    cmp, pb = _replay_predbat()
    pb.load_minutes = {minute: 0.0 for minute in range(600)}
    _check(failures, "Y3 replay skipped without a day of load history", cmp.build_replay() is None)

    # Rates move a day plus minutes_now later; a table that starts today falls back to the same time a day later
    cmp, pb = _replay_predbat()
    rates = {minute: (10.0 if minute < 0 else 20.0) for minute in range(-24 * 60, 48 * 60)}
    shifted = cmp.shift_rates(rates, HISTORY_AGO)
    _check(failures, "Y4 plan start gets yesterday's midnight rate", shifted.get(MINUTES_NOW) == 10.0, shifted.get(MINUTES_NOW))
    _check(failures, "Y4 a day into the plan is today's rate", shifted.get(MINUTES_NOW + 24 * 60) == 20.0, shifted.get(MINUTES_NOW + 24 * 60))
    fixed = {minute: (5.0 if (minute % (24 * 60)) < 7 * 60 else 25.0) for minute in range(0, 48 * 60)}
    shifted = cmp.shift_rates(fixed, HISTORY_AGO)
    _check(failures, "Y4 fixed table off-peak at yesterday 01:00", shifted.get(MINUTES_NOW + 60) == 5.0, shifted.get(MINUTES_NOW + 60))
    _check(failures, "Y4 fixed table peak at yesterday 12:00", shifted.get(MINUTES_NOW + 12 * 60) == 25.0, shifted.get(MINUTES_NOW + 12 * 60))

    # run_single with a replay: actual data in place for the plan, car and iBoost off for it and put back after
    cmp, pb = _replay_predbat()
    pb.iboost_enable = True
    pb.num_cars = 1
    pb.car_charging_slots = [[{"kwh": 7}]]
    pb.car_charging_now_slots = [[{"kwh": 1}]]
    pb.iboost_plan = ["live"]
    pb.compare_replay_steps = None
    seen = {}
    cmp.fetch_config = lambda tariff: None
    cmp.apply_hardware_overrides = lambda tariff, pb: None

    def fetch_rates(*args):
        """Record the shift fetch_rates was given"""
        seen["shift"] = args[6] if len(args) > 6 else 0
        return False

    def run_scenario(end_record):
        """Record the state the plan ran with"""
        seen["steps"] = pb.compare_replay_steps
        seen["iboost"] = pb.iboost_enable
        seen["soc"] = pb.soc_kw
        seen["car"] = pb.car_charging_slots
        return {"cost": 100, "metric": 90}

    cmp.fetch_rates = fetch_rates
    cmp.run_scenario = run_scenario
    cmp.recompute_car_charging = lambda slots: seen.setdefault("recompute", True)
    cmp.recompute_iboost = lambda: seen.setdefault("recompute", True)
    replay = cmp.build_replay()
    cmp.run_single({"name": "Test", "id": "test"}, {}, {}, 24 * 60, fetch_sensor=False, replay=replay)
    _check(failures, "Y5 rates shifted", seen.get("shift") == HISTORY_AGO, seen.get("shift"))
    _check(failures, "Y5 plan used the replay steps", seen.get("steps") is replay)
    _check(failures, "Y5 iBoost off during the replay", seen.get("iboost") is False)
    _check(failures, "Y5 started from yesterday's midnight SoC", seen.get("soc") == 4.25, seen.get("soc"))
    _check(failures, "Y5 no car planned on top of the actual load", seen.get("car") == [[]], seen.get("car"))
    _check(failures, "Y5 the live car and iBoost plans are not rebuilt", "recompute" not in seen)
    _check(failures, "Y5 iBoost setting put back", pb.iboost_enable is True)
    _check(failures, "Y5 replay steps cleared", pb.compare_replay_steps is None)

    # run_yesterday stores each tariff's replay under "yesterday" and marks the lowest metric best
    cmp, pb = _replay_predbat()
    metrics = {"a": 300, "b": 150}
    cmp.run_single = lambda tariff, *args, **kwargs: {"cost": metrics[tariff["id"]] + 10, "metric": metrics[tariff["id"]], "html": "<p>{}</p>".format(tariff["id"]), "raw": {}, "existing_tariff": tariff["id"] == "a"}
    cmp.restore_tariff_overrides = lambda saved, snapshot: None
    results = {"a": {"cost": 1}, "b": {"cost": 2}}
    cmp.run_yesterday([{"id": "a", "name": "A"}, {"id": "b", "name": "B"}], results, {}, {}, 24 * 60, {}, {})
    _check(failures, "Y6 results stored", "yesterday" in results["a"] and "yesterday" in results["b"])
    if "yesterday" in results["a"] and "yesterday" in results["b"]:
        _check(failures, "Y6 lowest metric is best", results["b"]["yesterday"]["best"] and not results["a"]["yesterday"]["best"])
        _check(failures, "Y6 day is yesterday", results["a"]["yesterday"]["day"] == "2026-11-11", results["a"]["yesterday"]["day"])
        _check(failures, "Y6 raw plan not kept", "raw" not in results["a"]["yesterday"])
        _check(failures, "Y6 forecast result untouched", results["a"]["cost"] == 1)

    # publish_data gives the replay its own entity, without the plan html, and keeps it off the forecast entity
    published = {}
    cmp.dashboard_item = lambda entity_id, state=None, attributes=None: published.__setitem__(entity_id, (state, attributes))
    cmp.comparisons = {"a": {"cost": 1, "name": "A", "yesterday": {"cost": 310, "metric": 300, "html": "<p>a</p>"}}}
    cmp.publish_data()
    forecast = published.get("predbat.compare_tariff_a")
    yesterday = published.get("predbat.compare_yesterday_a")
    _check(failures, "Y7 forecast entity has no yesterday attribute", forecast and "yesterday" not in forecast[1])
    _check(failures, "Y7 yesterday entity published", yesterday and yesterday[0] == 310 and "html" not in yesterday[1] and yesterday[1]["metric"] == 300)

    if failures:
        print("ERROR: compare_yesterday failures: {}".format(failures))
    return len(failures) > 0
