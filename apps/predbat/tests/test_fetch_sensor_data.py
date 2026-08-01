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

"""Unit tests for Fetch.fetch_sensor_data(), the main data ingest entry point.

fetch_sensor_data() is ~400 lines that turn apps.yaml configuration and Home Assistant history
into every input the optimiser consumes - load, import/export/PV history, energy rates, car
charging slots, alerts and today's cost so far. It had no test calling it: several existing
tests instead re-implement pieces of its logic ("Simulate the octopus intelligent slot
processing from fetch_sensor_data"), which means a bug introduced here leaves those copies
green.

These tests drive the real function against the shared PredBat fixture with a history provider
that returns well-formed cumulative series, and assert on the state it leaves behind. Every
test restores the fixture's configuration afterwards so later tests in the run are unaffected.
"""

from datetime import datetime, timedelta

from tests.test_infra import reset_inverter

# A daily-cumulative sensor's increment per minute, chosen so the totals are easy to reason about
DEFAULT_SERIES_RATES = {
    "sensor.load_today": 0.005,
    "sensor.import_today": 0.004,
    "sensor.export_today": 0.002,
    "sensor.pv_today": 0.003,
}

# Value returned for any entity without an explicit cumulative rate (power sensors, temperatures)
DEFAULT_FLAT_STATE = 250.0


class FetchFixture:
    """Configures the shared PredBat fixture for a fetch_sensor_data run and restores it after.

    The fixture object is shared across the whole test run, so anything mutated here - apps.yaml
    arguments, the history provider, the manual override state - has to be put back. Using a
    context manager keeps that guaranteed even when an assertion fails part way through.
    """

    def __init__(self, my_predbat, args=None, series_rates=None, flat_state=DEFAULT_FLAT_STATE, history_days=2, history_step=5):
        """Record the configuration this run needs on top of the fixture defaults."""
        self.base = my_predbat
        self.extra_args = dict(args or {})
        self.series_rates = dict(DEFAULT_SERIES_RATES)
        if series_rates is not None:
            self.series_rates.update(series_rates)
        self.flat_state = flat_state
        self.history_days = history_days
        # 5-minute samples match PREDICT_STEP, so per-step deltas are real rather than interpolated
        self.history_step = history_step
        self.history_calls = []

    def build_history(self, entity_id):
        """Return a Home Assistant style history series for one entity.

        Sensors named in ``series_rates`` get a daily-cumulative kWh series that resets at
        midnight, as a real energy sensor reports. Everything else gets a flat reading, which is
        what the power and temperature sensors in the default apps.yaml need.
        """
        per_minute = self.series_rates.get(entity_id)
        now = datetime.now(self.base.now_utc.tzinfo)
        step = self.history_step
        records = []
        for back in range(self.history_days * 24 * 60 // step, -1, -1):
            stamp = now - timedelta(minutes=back * step)
            if per_minute is None:
                state = self.flat_state
            else:
                state = round(per_minute * (stamp.hour * 60 + stamp.minute), 4)
            records.append({"state": str(state), "last_updated": stamp.strftime("%Y-%m-%dT%H:%M:%S%z")})
        return [records]

    def __enter__(self):
        """Apply the test configuration and install the history provider."""
        base = self.base
        self.saved_args = base.args.copy()
        self.saved_get_history = base.ha_interface.get_history
        self.saved_manual_soc_keep = getattr(base, "manual_soc_keep", {})
        self.saved_alert_active_keep = getattr(base, "alert_active_keep", {})

        for key in ("load_today", "import_today", "export_today", "pv_today"):
            base.args[key] = "sensor." + key
        base.args.update(self.extra_args)

        def get_history(entity_id, days=30, now=None):
            """Serve history for any entity, recording which ones were asked for."""
            self.history_calls.append(entity_id)
            if entity_id == "predbat.status":
                return [[{"state": "idle", "last_changed": datetime.now()}]]
            return self.build_history(entity_id)

        base.ha_interface.get_history = get_history
        reset_inverter(base)
        return base

    def __exit__(self, exc_type, exc_value, traceback):
        """Put the fixture back exactly as it was found."""
        self.base.args = self.saved_args
        self.base.ha_interface.get_history = self.saved_get_history
        self.base.manual_soc_keep = self.saved_manual_soc_keep
        self.base.alert_active_keep = self.saved_alert_active_keep
        return False


FLAT_IMPORT_RATE = 20.0
FLAT_EXPORT_RATE = 5.0

# A two-band import tariff with a cheap overnight window, plus a flat export rate
BANDED_RATES_ARGS = {
    "rates_import": [
        {"start": "00:00:00", "end": "05:00:00", "rate": 7.5},
        {"start": "05:00:00", "end": "00:00:00", "rate": 30.0},
    ],
    "rates_export": [{"rate": FLAT_EXPORT_RATE}],
}

FLAT_RATES_ARGS = {
    "rates_import": [{"rate": FLAT_IMPORT_RATE}],
    "rates_export": [{"rate": FLAT_EXPORT_RATE}],
}


def _test_reads_energy_history(my_predbat):
    """The load, import, export and PV sensors are turned into per-minute series."""
    print("Test: fetch_sensor_data reads the energy history sensors")
    with FetchFixture(my_predbat, args=FLAT_RATES_ARGS) as base:
        base.fetch_sensor_data()

        for name, data in (("load_minutes", base.load_minutes), ("import_today", base.import_today), ("export_today", base.export_today), ("pv_today", base.pv_today)):
            if not data:
                print("ERROR: {} was not populated".format(name))
                return 1
            if 0 not in data:
                print("ERROR: {} has no reading for minute 0".format(name))
                return 1

        if base.load_minutes_age < 1:
            print("ERROR: expected at least a day of load history, got {}".format(base.load_minutes_age))
            return 1

        # The series increments 0.005 kWh per minute, so consumption so far today tracks the clock
        expected_now = 0.005 * base.minutes_now
        if abs(base.load_minutes_now - expected_now) > 0.5:
            print("ERROR: expected about {} kWh used today, got {}".format(round(expected_now, 2), base.load_minutes_now))
            return 1

        # load_last_period is an hourly rate derived from the last prediction step
        if base.load_last_period <= 0:
            print("ERROR: expected a positive recent load rate, got {}".format(base.load_last_period))
            return 1
    return 0


def _test_builds_rates(my_predbat):
    """Configured import and export rates are expanded across the whole forecast window."""
    print("Test: fetch_sensor_data expands the configured rates")
    with FetchFixture(my_predbat, args=BANDED_RATES_ARGS) as base:
        base.fetch_sensor_data()

        if not base.rate_import or not base.rate_export:
            print("ERROR: rates were not built")
            return 1
        # Rates must cover the whole planning horizon, not just today
        if len(base.rate_import) < base.forecast_minutes:
            print("ERROR: import rates cover {} minutes, need {}".format(len(base.rate_import), base.forecast_minutes))
            return 1

        # The overnight band is cheap, the daytime band is not
        overnight = base.rate_import.get(2 * 60)
        daytime = base.rate_import.get(12 * 60)
        if overnight != 7.5:
            print("ERROR: expected the 7.5p overnight rate at 02:00, got {}".format(overnight))
            return 1
        if daytime != 30.0:
            print("ERROR: expected the 30p day rate at 12:00, got {}".format(daytime))
            return 1
        if base.rate_export.get(12 * 60) != FLAT_EXPORT_RATE:
            print("ERROR: expected a flat {}p export rate, got {}".format(FLAT_EXPORT_RATE, base.rate_export.get(12 * 60)))
            return 1

        # The cheap band must be picked up as a charging opportunity
        if not base.low_rates:
            print("ERROR: no low rate windows were found for a two-band tariff")
            return 1
        cheapest = min(window["average"] for window in base.low_rates)
        if cheapest > 7.5:
            print("ERROR: the cheap overnight band was not found, cheapest window is {}".format(cheapest))
            return 1
    return 0


def _test_flat_rate_windows_are_uniform(my_predbat):
    """A flat tariff still yields windows, but none of them is cheaper than any other.

    The window search always slices the horizon up; what distinguishes a flat tariff is that
    every slice prices the same, so there is no cheap slot for the optimiser to prefer.
    """
    print("Test: a flat tariff yields uniformly priced windows")
    with FetchFixture(my_predbat, args=FLAT_RATES_ARGS) as base:
        base.fetch_sensor_data()

        if not base.rate_import:
            print("ERROR: rates were not built")
            return 1
        if base.rate_import.get(2 * 60) != FLAT_IMPORT_RATE or base.rate_import.get(14 * 60) != FLAT_IMPORT_RATE:
            print("ERROR: expected the same rate all day, got {} and {}".format(base.rate_import.get(2 * 60), base.rate_import.get(14 * 60)))
            return 1
        if not base.low_rates:
            print("ERROR: expected the horizon to be sliced into windows")
            return 1

        averages = {round(window["average"], 4) for window in base.low_rates}
        if averages != {FLAT_IMPORT_RATE}:
            print("ERROR: expected every window at {}p, got {}".format(FLAT_IMPORT_RATE, sorted(averages)))
            return 1
    return 0


def _test_cost_today_so_far(my_predbat):
    """Today's cost so far is derived from the import/export history and the rates."""
    print("Test: fetch_sensor_data computes today's cost so far")
    with FetchFixture(my_predbat, args=FLAT_RATES_ARGS) as base:
        base.fetch_sensor_data()

        # Imported 0.004 kWh/min at 20p, exported 0.002 kWh/min at 5p, over the elapsed day
        imported = 0.004 * base.minutes_now
        exported = 0.002 * base.minutes_now
        expected = imported * FLAT_IMPORT_RATE - exported * FLAT_EXPORT_RATE

        if base.cost_today_sofar <= 0:
            print("ERROR: expected a positive cost so far, got {}".format(base.cost_today_sofar))
            return 1
        # A wide tolerance: the sampled series is interpolated onto per-minute data
        if abs(base.cost_today_sofar - expected) > max(abs(expected) * 0.25, 10.0):
            print("ERROR: expected roughly {}p, got {}p".format(round(expected, 1), round(base.cost_today_sofar, 1)))
            return 1
    return 0


def _test_import_only_cost_is_higher(my_predbat):
    """Export earnings reduce today's cost, so removing them raises it."""
    print("Test: export earnings offset today's cost")
    with FetchFixture(my_predbat, args=FLAT_RATES_ARGS) as base:
        base.fetch_sensor_data()
        cost_with_export = base.cost_today_sofar

    # The same run with nothing exported must cost more
    with FetchFixture(my_predbat, args=FLAT_RATES_ARGS, series_rates={"sensor.export_today": 0.0}) as base:
        base.fetch_sensor_data()
        cost_without_export = base.cost_today_sofar

    if cost_without_export <= cost_with_export:
        print("ERROR: expected a higher cost without export earnings: {} vs {}".format(cost_without_export, cost_with_export))
        return 1
    return 0


def _test_missing_rates_records_error(my_predbat):
    """With no rate source configured the run reports an error rather than silently continuing."""
    print("Test: fetch_sensor_data reports missing import rates")
    with FetchFixture(my_predbat, args={"rates_export": [{"rate": FLAT_EXPORT_RATE}]}) as base:
        base.had_errors = False
        base.fetch_sensor_data()

        if base.rate_import:
            print("ERROR: import rates were built from nothing, got {} entries".format(len(base.rate_import)))
            return 1
        if not base.had_errors:
            print("ERROR: a missing import rate source should be recorded as an error")
            return 1
    return 0


def _test_manual_soc_keep_merged(my_predbat):
    """A manual SOC keep floor is merged into the active keep used by the planner."""
    print("Test: fetch_sensor_data merges the manual SOC keep floor")
    with FetchFixture(my_predbat, args=FLAT_RATES_ARGS) as base:
        base.alert_active_keep = {}
        base.manual_soc_keep = {}
        base.fetch_sensor_data()
        if base.all_active_keep:
            print("ERROR: expected no active keep without alerts or a manual floor")
            return 1

        base.manual_soc_keep = {60: 4.0, 120: 6.0}
        base.fetch_sensor_data()
        if base.all_active_keep.get(60) != 4.0 or base.all_active_keep.get(120) != 6.0:
            print("ERROR: the manual keep floor was not merged, got {}".format(base.all_active_keep))
            return 1
    return 0


def _test_alert_keep_takes_the_higher_floor(my_predbat):
    """Where an alert and a manual floor overlap, the higher of the two wins."""
    print("Test: the active keep floor takes the larger of alert and manual values")
    with FetchFixture(my_predbat, args=FLAT_RATES_ARGS) as base:
        base.alert_active_keep = {}
        base.manual_soc_keep = {}
        base.fetch_sensor_data()

        # Inject an alert-derived floor that the next run must combine with the manual one.
        # process_alerts only overwrites alert_active_keep when an alert feed component is
        # configured, so with none present this stands in for a live weather alert.
        base.alert_active_keep = {60: 8.0, 180: 2.0}
        base.manual_soc_keep = {60: 4.0, 180: 5.0}
        base.fetch_sensor_data()

        if base.all_active_keep.get(60) != 8.0:
            print("ERROR: expected the larger alert floor of 8.0 at minute 60, got {}".format(base.all_active_keep.get(60)))
            return 1
        if base.all_active_keep.get(180) != 5.0:
            print("ERROR: expected the larger manual floor of 5.0 at minute 180, got {}".format(base.all_active_keep.get(180)))
            return 1
        # The merge must not mutate the alert data it copied from
        if base.alert_active_keep.get(180) != 2.0:
            print("ERROR: the alert keep data was mutated by the merge, got {}".format(base.alert_active_keep))
            return 1
    return 0


def _test_state_is_reset_between_runs(my_predbat):
    """Each run rebuilds its collections rather than accumulating across cycles.

    fetch_sensor_data runs every 5 minutes for the life of the process, so anything it appends to
    without clearing first would grow without bound.
    """
    print("Test: fetch_sensor_data resets its state on every run")
    with FetchFixture(my_predbat, args=BANDED_RATES_ARGS) as base:
        base.fetch_sensor_data()
        first = {
            "rate_slots": len(base.rate_slots),
            "low_rates": len(base.low_rates),
            "high_export_rates": len(base.high_export_rates),
            "load_forecast_array": len(base.load_forecast_array),
            "octopus_slots": len(base.octopus_slots),
        }

        base.fetch_sensor_data()
        second = {
            "rate_slots": len(base.rate_slots),
            "low_rates": len(base.low_rates),
            "high_export_rates": len(base.high_export_rates),
            "load_forecast_array": len(base.load_forecast_array),
            "octopus_slots": len(base.octopus_slots),
        }

        if first != second:
            print("ERROR: collections grew between identical runs: {} then {}".format(first, second))
            return 1

        # One slot list per car, rebuilt each run
        if len(base.octopus_slots) != base.num_cars:
            print("ERROR: expected {} car slot lists, got {}".format(base.num_cars, len(base.octopus_slots)))
            return 1
    return 0


def _test_iboost_energy_today(my_predbat):
    """An iBoost energy sensor is read into today's iBoost total."""
    print("Test: fetch_sensor_data reads the iBoost energy sensor")
    args = dict(FLAT_RATES_ARGS)
    args["iboost_energy_today"] = "sensor.iboost_energy_today"
    with FetchFixture(my_predbat, args=args, series_rates={"sensor.iboost_energy_today": 0.001}) as base:
        base.iboost_today = 0
        base.fetch_sensor_data()

        if not base.iboost_energy_today:
            print("ERROR: the iBoost energy series was not read")
            return 1
        expected = 0.001 * base.minutes_now
        if abs(base.iboost_today - expected) > max(expected * 0.25, 0.5):
            print("ERROR: expected about {} kWh of iBoost energy today, got {}".format(round(expected, 2), base.iboost_today))
            return 1
    return 0


def _test_battery_temperature_history(my_predbat):
    """A battery temperature sensor produces both history and a forward prediction."""
    print("Test: fetch_sensor_data reads and forecasts battery temperature")
    args = dict(FLAT_RATES_ARGS)
    args["battery_temperature"] = "sensor.battery_temperature"
    with FetchFixture(my_predbat, args=args, flat_state=12.0) as base:
        base.fetch_sensor_data()

        if not base.battery_temperature_history:
            print("ERROR: no battery temperature history was read")
            return 1
        if abs(base.battery_temperature_history.get(0, 0) - 12.0) > 0.5:
            print("ERROR: expected 12C now, got {}".format(base.battery_temperature_history.get(0)))
            return 1
        if not base.battery_temperature_prediction:
            print("ERROR: no forward battery temperature prediction was produced")
            return 1
    return 0


def _test_load_forecast_array(my_predbat):
    """The extra load forecast is assembled into the array the planner consumes."""
    print("Test: fetch_sensor_data assembles the load forecast")
    with FetchFixture(my_predbat, args=FLAT_RATES_ARGS) as base:
        base.fetch_sensor_data()

        # load_forecast and its array are always rebuilt, even when no forecast source is set
        if not isinstance(base.load_forecast, dict):
            print("ERROR: expected a load forecast dict, got {}".format(type(base.load_forecast)))
            return 1
        if not isinstance(base.load_forecast_array, list):
            print("ERROR: expected a load forecast array, got {}".format(type(base.load_forecast_array)))
            return 1
    return 0


def _test_history_is_actually_queried(my_predbat):
    """The configured sensors really are read, rather than defaults being used."""
    print("Test: fetch_sensor_data queries the configured sensors")
    fixture = FetchFixture(my_predbat, args=FLAT_RATES_ARGS)
    with fixture as base:
        base.fetch_sensor_data()

    for entity_id in ("sensor.load_today", "sensor.import_today", "sensor.export_today", "sensor.pv_today"):
        if entity_id not in fixture.history_calls:
            print("ERROR: {} was never queried, calls were {}".format(entity_id, sorted(set(fixture.history_calls))))
            return 1
    return 0


def test_fetch_sensor_data(my_predbat=None):
    """Run the fetch_sensor_data test suite, returning non-zero if any sub-test failed."""
    sub_tests = [
        ("energy_history", _test_reads_energy_history, "Energy history sensors"),
        ("rates", _test_builds_rates, "Rate expansion and low rate windows"),
        ("flat_rates", _test_flat_rate_windows_are_uniform, "Flat tariff windows are uniform"),
        ("cost_today", _test_cost_today_so_far, "Today's cost so far"),
        ("export_offsets_cost", _test_import_only_cost_is_higher, "Export earnings offset cost"),
        ("missing_rates", _test_missing_rates_records_error, "Missing import rates reported"),
        ("manual_keep", _test_manual_soc_keep_merged, "Manual SOC keep merged"),
        ("alert_keep", _test_alert_keep_takes_the_higher_floor, "Alert and manual keep merge"),
        ("state_reset", _test_state_is_reset_between_runs, "State reset between runs"),
        ("iboost", _test_iboost_energy_today, "iBoost energy today"),
        ("battery_temperature", _test_battery_temperature_history, "Battery temperature history"),
        ("load_forecast", _test_load_forecast_array, "Load forecast assembly"),
        ("history_queried", _test_history_is_actually_queried, "Configured sensors queried"),
    ]

    print("\n" + "=" * 70)
    print("FETCH SENSOR DATA TEST SUITE")
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
    print("FETCH SENSOR DATA RESULTS: {} passed, {} failed".format(passed, failed))
    print("=" * 70)
    return failed
