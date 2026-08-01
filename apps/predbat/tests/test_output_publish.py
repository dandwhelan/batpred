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

"""Unit tests for the publishing path in output.py.

output.py turns the optimiser's results into the Home Assistant sensors users actually read -
today's cost, the import and export rate windows, the car charging plan and the export limit.
Around 470 statements of it were uncovered, including today_cost() (345 lines on its own) and
every publish_rates_* function: the calculations behind them were tested, but nothing checked
that the numbers reach an entity with the right state, units and attributes.

The tests drive the real functions against the shared PredBat fixture and assert on
dashboard_values, which is where dashboard_item() records everything it publishes.
"""

from datetime import timedelta

from tests.test_infra import reset_inverter

IMPORT_RATE = 20.0
EXPORT_RATE = 5.0
MINUTES_NOW = 600

# Per-minute increments for the cumulative energy series the cost calculation consumes
IMPORT_PER_MINUTE = 0.004
EXPORT_PER_MINUTE = 0.002
LOAD_PER_MINUTE = 0.005
CAR_PER_MINUTE = 0.001


def cumulative_series(per_minute, minutes_now=MINUTES_NOW):
    """Build a backwards cumulative series, as get_from_incrementing() expects.

    Index 0 is now and larger indices go further back, so the value falls with the index - the
    shape a daily energy counter has once it has been read backwards from the present.
    """
    return {minute: round(per_minute * (minutes_now - minute), 6) for minute in range(0, minutes_now + 100)}


class OutputFixture:
    """Puts the shared fixture into a known state for a publishing run and restores it after."""

    def __init__(self, my_predbat, carbon_enable=False, num_cars=0, standing_charge=0.0):
        """Record the options this run needs."""
        self.base = my_predbat
        self.carbon_enable = carbon_enable
        self.num_cars = num_cars
        self.standing_charge = standing_charge
        self.saved = {}

    def __enter__(self):
        """Apply a deterministic cost/rate state and clear the published entity record."""
        base = self.base
        for name in (
            "minutes_now",
            "rate_import",
            "rate_export",
            "carbon_enable",
            "carbon_history",
            "metric_standing_charge",
            "soc_kwh_history",
            "rate_min_forward",
            "num_cars",
            "dashboard_values",
            "car_charging_slots",
            "low_rates",
            "high_export_rates",
            "forecast_minutes",
        ):
            self.saved[name] = getattr(base, name, None)

        reset_inverter(base)
        base.minutes_now = MINUTES_NOW
        base.forecast_minutes = 24 * 60
        base.rate_import = {minute: IMPORT_RATE for minute in range(0, 3 * 24 * 60)}
        base.rate_export = {minute: EXPORT_RATE for minute in range(0, 3 * 24 * 60)}
        base.carbon_enable = self.carbon_enable
        # 100g per kWh keeps the expected carbon figure a round multiple of the energy
        base.carbon_history = {minute: 100.0 for minute in range(0, 3 * 24 * 60)}
        base.metric_standing_charge = self.standing_charge
        base.soc_kwh_history = {minute: 5.0 for minute in range(0, 3 * 24 * 60)}
        base.rate_min_forward = {minute: IMPORT_RATE for minute in range(0, 3 * 24 * 60)}
        base.num_cars = self.num_cars
        base.car_charging_slots = [[] for _ in range(max(self.num_cars, 1))]
        base.low_rates = []
        base.high_export_rates = []
        base.dashboard_values = {}
        return base

    def __exit__(self, exc_type, exc_value, traceback):
        """Restore everything this fixture changed."""
        for name, value in self.saved.items():
            if value is not None:
                setattr(self.base, name, value)
        return False


def published(base, entity):
    """Return a published entity's record, or None if it was never published."""
    return base.dashboard_values.get(entity)


def _test_today_cost_arithmetic(my_predbat):
    """Today's cost is import spend less export earnings, and is returned as well as published."""
    print("Test: today_cost nets export earnings off import spend")
    with OutputFixture(my_predbat) as base:
        cost, carbon = base.today_cost(cumulative_series(IMPORT_PER_MINUTE), cumulative_series(EXPORT_PER_MINUTE), None, cumulative_series(LOAD_PER_MINUTE), save=True)

        imported = IMPORT_PER_MINUTE * MINUTES_NOW
        exported = EXPORT_PER_MINUTE * MINUTES_NOW
        expected = imported * IMPORT_RATE - exported * EXPORT_RATE

        if abs(cost - expected) > 0.5:
            print("ERROR: expected {}p, got {}p".format(round(expected, 2), round(cost, 2)))
            return 1
        if carbon != 0:
            print("ERROR: carbon should be zero when carbon tracking is off, got {}".format(carbon))
            return 1

        entry = published(base, "predbat.cost_today")
        if entry is None:
            print("ERROR: cost_today was not published")
            return 1
        if abs(entry["state"] - expected) > 0.5:
            print("ERROR: the published state {} does not match the returned cost {}".format(entry["state"], round(cost, 2)))
            return 1

        # The import and export legs are published separately and must add back up to the net
        import_entry = published(base, "predbat.cost_today_import")
        export_entry = published(base, "predbat.cost_today_export")
        if import_entry is None or export_entry is None:
            print("ERROR: the import/export cost split was not published")
            return 1
        if abs(import_entry["state"] - imported * IMPORT_RATE) > 0.5:
            print("ERROR: expected {}p of import cost, got {}".format(round(imported * IMPORT_RATE, 2), import_entry["state"]))
            return 1
        if abs(import_entry["state"] + export_entry["state"] - cost) > 0.5:
            print("ERROR: the import and export legs {} and {} do not sum to the net {}".format(import_entry["state"], export_entry["state"], round(cost, 2)))
            return 1
    return 0


def _test_today_cost_energy_attributes(my_predbat):
    """The published entities carry the energy totals, units and a time series."""
    print("Test: today_cost publishes energy, units and a chart series")
    with OutputFixture(my_predbat) as base:
        base.today_cost(cumulative_series(IMPORT_PER_MINUTE), cumulative_series(EXPORT_PER_MINUTE), None, cumulative_series(LOAD_PER_MINUTE), save=True)

        entry = published(base, "predbat.cost_today")
        attributes = entry["attributes"]
        if attributes.get("unit_of_measurement") != base.currency_symbols[1]:
            print("ERROR: cost_today is missing its currency unit, got {}".format(attributes.get("unit_of_measurement")))
            return 1
        if not attributes.get("results"):
            print("ERROR: cost_today has no chart series")
            return 1

        import_attributes = published(base, "predbat.cost_today_import")["attributes"]
        expected_energy = IMPORT_PER_MINUTE * MINUTES_NOW
        if abs(import_attributes.get("energy", 0) - expected_energy) > 0.2:
            print("ERROR: expected {} kWh imported, got {}".format(round(expected_energy, 2), import_attributes.get("energy")))
            return 1
        # A flat tariff means the average price per kWh is exactly the tariff
        if abs(import_attributes.get("p/kWh", 0) - IMPORT_RATE) > 0.5:
            print("ERROR: expected {}p/kWh, got {}".format(IMPORT_RATE, import_attributes.get("p/kWh")))
            return 1

        hourly = published(base, "predbat.cost_hour")
        if hourly is None:
            print("ERROR: the trailing hour cost was not published")
            return 1
        # One hour of importing at a flat rate, less that hour's export earnings
        expected_hour = 60 * IMPORT_PER_MINUTE * IMPORT_RATE - 60 * EXPORT_PER_MINUTE * EXPORT_RATE
        if abs(hourly["state"] - expected_hour) > 0.5:
            print("ERROR: expected {}p in the last hour, got {}".format(round(expected_hour, 2), hourly["state"]))
            return 1
    return 0


def _test_today_cost_standing_charge(my_predbat):
    """The daily standing charge is added on top of the metered cost."""
    print("Test: today_cost adds the standing charge")
    with OutputFixture(my_predbat) as base:
        without, _carbon = base.today_cost(cumulative_series(IMPORT_PER_MINUTE), cumulative_series(EXPORT_PER_MINUTE), None, cumulative_series(LOAD_PER_MINUTE), save=False)

    with OutputFixture(my_predbat, standing_charge=45.0) as base:
        with_charge, _carbon = base.today_cost(cumulative_series(IMPORT_PER_MINUTE), cumulative_series(EXPORT_PER_MINUTE), None, cumulative_series(LOAD_PER_MINUTE), save=False)

    if abs((with_charge - without) - 45.0) > 0.01:
        print("ERROR: expected the 45p standing charge to be added, difference was {}".format(round(with_charge - without, 3)))
        return 1
    return 0


def _test_today_cost_no_save(my_predbat):
    """With save off the cost is still calculated but nothing is published."""
    print("Test: today_cost with save off publishes nothing")
    with OutputFixture(my_predbat) as base:
        cost, _carbon = base.today_cost(cumulative_series(IMPORT_PER_MINUTE), cumulative_series(EXPORT_PER_MINUTE), None, cumulative_series(LOAD_PER_MINUTE), save=False)

        if cost <= 0:
            print("ERROR: expected a cost to be calculated even without saving, got {}".format(cost))
            return 1
        if base.dashboard_values:
            print("ERROR: expected nothing to be published, got {}".format(sorted(base.dashboard_values)))
            return 1
    return 0


def _test_today_cost_carbon(my_predbat):
    """With carbon tracking on the footprint is accumulated and published."""
    print("Test: today_cost tracks carbon when enabled")
    with OutputFixture(my_predbat, carbon_enable=True) as base:
        _cost, carbon = base.today_cost(cumulative_series(IMPORT_PER_MINUTE), cumulative_series(EXPORT_PER_MINUTE), None, cumulative_series(LOAD_PER_MINUTE), save=True)

        # Net energy at a flat 100g/kWh
        net_energy = (IMPORT_PER_MINUTE - EXPORT_PER_MINUTE) * MINUTES_NOW
        expected = net_energy * 100.0
        if abs(carbon - expected) > max(expected * 0.1, 5.0):
            print("ERROR: expected about {}g of carbon, got {}".format(round(expected), round(carbon)))
            return 1

        entry = published(base, "predbat.carbon_today")
        if entry is None:
            print("ERROR: carbon_today was not published")
            return 1
        if entry["attributes"].get("unit_of_measurement") != "g":
            print("ERROR: carbon_today should be published in grams, got {}".format(entry["attributes"].get("unit_of_measurement")))
            return 1

    # And nothing carbon related is published when the feature is off
    with OutputFixture(my_predbat, carbon_enable=False) as base:
        base.today_cost(cumulative_series(IMPORT_PER_MINUTE), cumulative_series(EXPORT_PER_MINUTE), None, cumulative_series(LOAD_PER_MINUTE), save=True)
        if published(base, "predbat.carbon_today") is not None:
            print("ERROR: carbon_today should not be published while carbon tracking is off")
            return 1
    return 0


def _test_today_cost_car(my_predbat):
    """Car charging energy is costed separately and published as its own entity."""
    print("Test: today_cost splits out the car charging cost")
    with OutputFixture(my_predbat, num_cars=1) as base:
        base.today_cost(cumulative_series(IMPORT_PER_MINUTE), cumulative_series(EXPORT_PER_MINUTE), cumulative_series(CAR_PER_MINUTE), cumulative_series(LOAD_PER_MINUTE), save=True)

        entry = published(base, "predbat.cost_today_car")
        if entry is None:
            print("ERROR: cost_today_car was not published")
            return 1

        expected = CAR_PER_MINUTE * MINUTES_NOW * IMPORT_RATE
        if abs(entry["state"] - expected) > 0.5:
            print("ERROR: expected {}p of car charging cost, got {}".format(round(expected, 2), entry["state"]))
            return 1
        if abs(entry["attributes"].get("energy", 0) - CAR_PER_MINUTE * MINUTES_NOW) > 0.2:
            print("ERROR: expected {} kWh of car energy, got {}".format(round(CAR_PER_MINUTE * MINUTES_NOW, 2), entry["attributes"].get("energy")))
            return 1

    # With no cars configured the entity is not published at all
    with OutputFixture(my_predbat, num_cars=0) as base:
        base.today_cost(cumulative_series(IMPORT_PER_MINUTE), cumulative_series(EXPORT_PER_MINUTE), None, cumulative_series(LOAD_PER_MINUTE), save=True)
        if published(base, "predbat.cost_today_car") is not None:
            print("ERROR: cost_today_car should not be published with no cars configured")
            return 1
    return 0


def _test_publish_rates_import(my_predbat):
    """The cheapest import window is published as start/end/cost entities."""
    print("Test: publish_rates_import publishes the low rate windows")
    with OutputFixture(my_predbat) as base:
        base.low_rates = [
            {"start": MINUTES_NOW + 120, "end": MINUTES_NOW + 240, "average": 7.5},
            {"start": MINUTES_NOW + 600, "end": MINUTES_NOW + 660, "average": 12.0},
        ]
        base.publish_rates_import()

        for entity in ("predbat.low_rate_start", "predbat.low_rate_end", "predbat.low_rate_cost"):
            if published(base, entity) is None:
                print("ERROR: {} was not published".format(entity))
                return 1

        if published(base, "predbat.low_rate_cost")["state"] != 7.5:
            print("ERROR: expected the first window's 7.5p rate, got {}".format(published(base, "predbat.low_rate_cost")["state"]))
            return 1

        # The second window is published under its own suffix
        if published(base, "predbat.low_rate_cost_2") is None:
            print("ERROR: the second low rate window was not published")
            return 1
        if published(base, "predbat.low_rate_cost_2")["state"] != 12.0:
            print("ERROR: expected the second window at 12p, got {}".format(published(base, "predbat.low_rate_cost_2")["state"]))
            return 1

        start_attributes = published(base, "predbat.low_rate_start")["attributes"]
        if "date" not in start_attributes:
            print("ERROR: the low rate start is missing its absolute date, got {}".format(sorted(start_attributes)))
            return 1
    return 0


def _test_publish_rates_import_none(my_predbat):
    """With no cheap window the entities are published as unavailable rather than omitted."""
    print("Test: publish_rates_import with no low rate windows")
    with OutputFixture(my_predbat) as base:
        base.low_rates = []
        base.publish_rates_import()

        entry = published(base, "predbat.low_rate_start")
        if entry is None:
            print("ERROR: low_rate_start should still be published when there is no cheap window")
            return 1
        if entry["state"] not in ("unavailable", "undefined", None, ""):
            print("ERROR: expected an unavailable state with no window, got {}".format(entry["state"]))
            return 1
    return 0


def _test_publish_rates_export(my_predbat):
    """The best export window is published as start/end/cost entities."""
    print("Test: publish_rates_export publishes the high export windows")
    with OutputFixture(my_predbat) as base:
        base.high_export_rates = [
            {"start": MINUTES_NOW + 60, "end": MINUTES_NOW + 120, "average": 30.0},
            {"start": MINUTES_NOW + 300, "end": MINUTES_NOW + 360, "average": 22.0},
        ]
        base.publish_rates_export()

        for entity in ("predbat.high_rate_export_start", "predbat.high_rate_export_end", "predbat.high_rate_export_cost"):
            if published(base, entity) is None:
                print("ERROR: {} was not published".format(entity))
                return 1
        if published(base, "predbat.high_rate_export_cost")["state"] != 30.0:
            print("ERROR: expected the best export window at 30p, got {}".format(published(base, "predbat.high_rate_export_cost")["state"]))
            return 1
        if published(base, "predbat.high_rate_export_cost_2")["state"] != 22.0:
            print("ERROR: expected the second export window at 22p, got {}".format(published(base, "predbat.high_rate_export_cost_2")["state"]))
            return 1
    return 0


def _test_publish_rates_series(my_predbat):
    """publish_rates publishes the rate series with a chart-ready results attribute."""
    print("Test: publish_rates publishes the rate series")
    with OutputFixture(my_predbat) as base:
        base.publish_rates(base.rate_import, False)
        entry = published(base, "predbat.rates")
        if entry is None:
            print("ERROR: the import rates entity was not published")
            return 1
        if not entry["attributes"].get("results"):
            print("ERROR: the import rates entity has no chart series")
            return 1
        if entry["state"] != IMPORT_RATE:
            print("ERROR: expected the current rate {} as the state, got {}".format(IMPORT_RATE, entry["state"]))
            return 1

        base.publish_rates(base.rate_export, True)
        export_entry = published(base, "predbat.rates_export")
        if export_entry is None:
            print("ERROR: the export rates entity was not published")
            return 1
        if export_entry["state"] != EXPORT_RATE:
            print("ERROR: expected the current export rate {}, got {}".format(EXPORT_RATE, export_entry["state"]))
            return 1
    return 0


def _test_publish_export_limit(my_predbat):
    """The export limit is charted, with the first active window driving the headline state."""
    print("Test: publish_export_limit charts the export plan")
    with OutputFixture(my_predbat) as base:
        base.soc_max = 10.0
        export_window = [{"start": MINUTES_NOW + 60, "end": MINUTES_NOW + 120, "average": 30.0}]
        export_limits = [50.0]

        base.publish_export_limit(export_window, export_limits, best=True)

        entry = published(base, "predbat.best_export_limit_kw")
        if entry is None:
            print("ERROR: the best export limit was not published, got {}".format(sorted(base.dashboard_values)))
            return 1
        if not entry["attributes"].get("results"):
            print("ERROR: the export limit has no chart series")
            return 1

        percent_entry = published(base, "predbat.best_export_limit")
        if percent_entry is None:
            print("ERROR: the export limit percentage was not published")
            return 1
        if percent_entry["state"] != 50.0:
            print("ERROR: expected a 50% export limit, got {}".format(percent_entry["state"]))
            return 1

    # The base plan publishes under its own entity names
    with OutputFixture(my_predbat) as base:
        base.soc_max = 10.0
        base.publish_export_limit([{"start": MINUTES_NOW + 60, "end": MINUTES_NOW + 120, "average": 30.0}], [50.0], best=False)
        if published(base, "predbat.export_limit") is None:
            print("ERROR: the base export limit was not published, got {}".format(sorted(base.dashboard_values)))
            return 1
    return 0


def _test_publish_car_plan_empty(my_predbat):
    """With no car slots planned the binary sensor is published as off."""
    print("Test: publish_car_plan with no planned slots")
    with OutputFixture(my_predbat, num_cars=1) as base:
        base.car_charging_slots = [[]]
        base.publish_car_plan()

        entry = published(base, "binary_sensor.predbat_car_charging_slot")
        if entry is None:
            print("ERROR: the car charging slot sensor was not published")
            return 1
        if entry["state"] != "off":
            print("ERROR: expected the slot sensor off with no plan, got {}".format(entry["state"]))
            return 1
        if entry["attributes"].get("planned"):
            print("ERROR: expected an empty plan, got {}".format(entry["attributes"].get("planned")))
            return 1
        # The energy key must be spelled the same way whether or not there is a plan, or a
        # template sensor reading it loses its value every time the plan empties
        if "kwh" not in entry["attributes"]:
            print("ERROR: expected a 'kwh' attribute even with no plan, got {}".format(sorted(entry["attributes"])))
            return 1
    return 0


def _test_publish_car_plan_with_slots(my_predbat):
    """A planned car slot is published with its window and energy."""
    print("Test: publish_car_plan with a planned slot")
    with OutputFixture(my_predbat, num_cars=1) as base:
        start = MINUTES_NOW + 120
        base.car_charging_slots = [[{"start": start, "end": start + 60, "kwh": 7.0, "average": 7.5, "cost": 52.5}]]
        base.publish_car_plan()

        entry = published(base, "binary_sensor.predbat_car_charging_slot")
        if entry is None:
            print("ERROR: the car charging slot sensor was not published")
            return 1
        if not entry["attributes"].get("planned"):
            print("ERROR: expected the planned slot to be listed, got {}".format(entry["attributes"]))
            return 1
        if entry["attributes"].get("kwh") != 7.0:
            print("ERROR: expected 7.0 kWh planned, got {}".format(entry["attributes"].get("kwh")))
            return 1
        if entry["attributes"].get("cost") != 52.5:
            print("ERROR: expected a 52.5p slot cost, got {}".format(entry["attributes"].get("cost")))
            return 1

        start_entry = published(base, "predbat.car_charging_start")
        if start_entry is None:
            print("ERROR: the car charging start time was not published")
            return 1
        expected_start = (base.midnight_utc + timedelta(minutes=start)).strftime("%H:%M:%S")
        if start_entry["state"] != expected_start:
            print("ERROR: expected a start of {}, got {}".format(expected_start, start_entry["state"]))
            return 1
    return 0


def test_output_publish(my_predbat=None):
    """Run the output publishing test suite, returning non-zero if any sub-test failed."""
    sub_tests = [
        ("cost_arithmetic", _test_today_cost_arithmetic, "Today's cost arithmetic"),
        ("cost_attributes", _test_today_cost_energy_attributes, "Cost entity attributes"),
        ("standing_charge", _test_today_cost_standing_charge, "Standing charge"),
        ("cost_no_save", _test_today_cost_no_save, "Cost without publishing"),
        ("carbon", _test_today_cost_carbon, "Carbon tracking"),
        ("car_cost", _test_today_cost_car, "Car charging cost split"),
        ("rates_import", _test_publish_rates_import, "Low rate window publishing"),
        ("rates_import_none", _test_publish_rates_import_none, "No low rate windows"),
        ("rates_export", _test_publish_rates_export, "High export window publishing"),
        ("rates_series", _test_publish_rates_series, "Rate series publishing"),
        ("export_limit", _test_publish_export_limit, "Export limit charting"),
        ("car_plan_empty", _test_publish_car_plan_empty, "Car plan with no slots"),
        ("car_plan_slots", _test_publish_car_plan_with_slots, "Car plan with a slot"),
    ]

    print("\n" + "=" * 70)
    print("OUTPUT PUBLISHING TEST SUITE")
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
    print("OUTPUT PUBLISHING RESULTS: {} passed, {} failed".format(passed, failed))
    print("=" * 70)
    return failed
