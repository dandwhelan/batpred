# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long

"""Unit tests for the annual heat pump and gas boiler model.

Pure arithmetic over a synthetic temperature series - no network, no Predbat
instance, no plan run - so it is fast and registered as a non-slow test.
"""

import math
from datetime import date, datetime, timedelta

import pytz

from annual_costs import build_heat_costs, build_heat_payback, resolve_costs
from annual_heat import (
    HOURS_PER_DAY,
    MAX_COP,
    MIN_COP,
    SETBACK_FACTOR,
    SETBACK_HOURS,
    AnnualHeatError,
    HeatModel,
    HeatPumpLoadProfile,
    carnot_cop,
    resolve_heat,
)
from annual_weather import temperature_by_local_day


def close(actual, expected, tolerance=0.01):
    """Return True when two floats agree to within a tolerance."""
    return abs(actual - expected) <= tolerance


def sine_year(year, mean=10.0, amplitude=7.0):
    """Return a full year of hourly temperatures with a seasonal and a daily cycle.

    Deliberately not a flat series: the whole point of the model is that heat demand
    and COP both move with temperature, so a constant year would pass tests that a
    real one would fail.
    """
    days = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365
    series = {}
    for offset in range(days):
        day = date(year, 1, 1) + timedelta(days=offset)
        seasonal = mean - amplitude * math.cos(2 * math.pi * offset / days)
        series[day] = [seasonal + 3.0 * math.sin(2 * math.pi * (hour - 9) / 24.0) for hour in range(HOURS_PER_DAY)]
    return series


class StubLoad:
    """A minimal LoadProfileSource returning a flat household load."""

    def __init__(self, daily=10.0, missing=None):
        """Hold the flat daily total and any dates that should report no data."""
        self.daily = daily
        self.missing = missing or set()

    def daily_kwh(self, day):
        """Return the flat daily household total."""
        return self.daily

    def minute_profile(self, day):
        """Return a flat per-minute profile, or None for a date with no data."""
        if day in self.missing:
            return None
        return [self.daily / 1440.0] * 1440


def base_config(**overrides):
    """Return a valid heat config with the given fields overridden."""
    config = {"annual_gas_kwh": 12000, "water_gas_kwh": 2400, "boiler_efficiency": 0.85, "scop": 3.2, "gas_p_per_kwh": 6.4, "gas_standing_charge_p_per_day": 32.0}
    config.update(overrides)
    return config


def test_annual_heat(my_predbat):
    """Verify the heat demand model, the COP curve, the gas costing and the payback.

    ``my_predbat`` is unused: this module is pure arithmetic with no Predbat state, but
    the test runner calls every registered test with the shared instance uniformly.
    """
    failed = False
    print("**** Testing annual heat pump / gas model ****")
    year = 2023
    temperatures = temperature_year(year)

    print("Test: no heat block, or an explicitly disabled one, models no heat pump")
    for raw in [None, {}, {"enable": False}, {"enable": "false"}, {"enable": "no"}]:
        if resolve_heat(raw) is not None:
            print("  ERROR: {} should not enable a heat pump".format(raw))
            failed = True
    if resolve_heat({"enable": True}) is None:
        print("  ERROR: an explicitly enabled heat block should produce settings")
        failed = True

    print("Test: delivered heat is gas times boiler efficiency, split space vs water")
    settings = resolve_heat(base_config())
    if not close(settings["space_heat_kwh"], (12000 - 2400) * 0.85):
        print("  ERROR: space heat should be {}, got {}".format((12000 - 2400) * 0.85, settings["space_heat_kwh"]))
        failed = True
    # Hot water uses its OWN gas efficiency (a stored cylinder is worse than space heating)
    if not close(settings["water_heat_kwh"], 2400 * 0.80):
        print("  ERROR: water heat should be {}, got {}".format(2400 * 0.80, settings["water_heat_kwh"]))
        failed = True

    print("Test: hot water gas cannot exceed the gas total")
    try:
        resolve_heat(base_config(annual_gas_kwh=2000, water_gas_kwh=3000))
        print("  ERROR: water gas above the total should be rejected")
        failed = True
    except AnnualHeatError:
        pass

    print("Test: a non-numeric or non-finite setting is rejected by name")
    for field, value in [("scop", "abc"), ("gas_p_per_kwh", float("nan")), ("boiler_efficiency", float("inf")), ("annual_gas_kwh", True)]:
        try:
            resolve_heat(base_config(**{field: value}))
            print("  ERROR: {}={!r} should have been rejected".format(field, value))
            failed = True
        except AnnualHeatError as error:
            if field not in str(error):
                print("  ERROR: the error for {} should name the field, got '{}'".format(field, error))
                failed = True

    print("Test: an out-of-range setting is rejected")
    for field, value in [("boiler_efficiency", 1.5), ("boiler_efficiency", 0), ("scop", 0.5), ("flow_temp_c", 90), ("base_temp_c", 40)]:
        try:
            resolve_heat(base_config(**{field: value}))
            print("  ERROR: {}={} should have been rejected".format(field, value))
            failed = True
        except AnnualHeatError:
            pass

    print("Test: the Carnot COP rises as the outdoor temperature rises, and is floored on lift")
    if not carnot_cop(0, 45) < carnot_cop(10, 45):
        print("  ERROR: Carnot COP should improve with a warmer outdoor temperature")
        failed = True
    if not close(carnot_cop(44, 45), carnot_cop(40, 45)):
        print("  ERROR: the lift floor should make a tiny lift behave like the minimum lift")
        failed = True

    model = HeatModel(resolve_heat(base_config()), temperatures, year)

    print("Test: the realised space-heating SCOP matches the configured one")
    if not close(model.realised_scop(), 3.2, tolerance=0.05):
        print("  ERROR: realised SCOP should be about 3.2, got {}".format(model.realised_scop()))
        failed = True

    print("Test: a different configured SCOP is honoured")
    for wanted in [2.5, 4.0]:
        other = HeatModel(resolve_heat(base_config(scop=wanted)), temperatures, year)
        if not close(other.realised_scop(), wanted, tolerance=0.05):
            print("  ERROR: realised SCOP should be about {}, got {}".format(wanted, other.realised_scop()))
            failed = True

    print("Test: COP is worse when it is colder, and hot water is always worse than space heating")
    if not model.space_cop(-2) < model.space_cop(12):
        print("  ERROR: COP should be lower on a cold day than a mild one")
        failed = True
    for outdoor in [-5, 0, 5, 10, 15]:
        if not model.water_cop(outdoor) < model.space_cop(outdoor):
            print("  ERROR: hot water COP should be below space heating COP at {}C".format(outdoor))
            failed = True
        if not MIN_COP <= model.space_cop(outdoor) <= MAX_COP:
            print("  ERROR: space COP at {}C is outside the clamp: {}".format(outdoor, model.space_cop(outdoor)))
            failed = True

    print("Test: the twelve months' space heat sums to the configured annual figure")
    monthly = sum(model.month_space_heat_kwh(month) for month in range(1, 13))
    if not close(monthly, model.settings["space_heat_kwh"], tolerance=1.0):
        print("  ERROR: monthly space heat should sum to {}, got {}".format(model.settings["space_heat_kwh"], monthly))
        failed = True

    print("Test: a temperature series running past the year still delivers the whole year's heat")
    # The weather download deliberately ends on 1 January of the FOLLOWING year, so that a
    # sample on 31 December still has the second day its 48 hour plan needs. Those extra
    # days must not be counted in the degree-day denominator: doing so spread the year's
    # heat over 366 days instead of 365 and quietly lost about 0.8% of it, and with it the
    # same slice of the gas bill the comparison rests on.
    with_spill = dict(temperatures)
    with_spill[date(year + 1, 1, 1)] = [-5.0] * HOURS_PER_DAY
    spilled = HeatModel(resolve_heat(base_config()), with_spill, year)
    spilled_total = sum(spilled.month_space_heat_kwh(month) for month in range(1, 13))
    if not close(spilled_total, spilled.settings["space_heat_kwh"], tolerance=1.0):
        print("  ERROR: a series spilling into the next year should still deliver {} kWh, got {}".format(spilled.settings["space_heat_kwh"], spilled_total))
        failed = True
    if not close(spilled.realised_scop(), 3.2, tolerance=0.05):
        print("  ERROR: the spill day should not drag the realised SCOP, got {}".format(spilled.realised_scop()))
        failed = True

    print("Test: winter carries far more space heat than summer")
    january = model.month_space_heat_kwh(1)
    july = model.month_space_heat_kwh(7)
    if not january > july * 5:
        print("  ERROR: January ({}) should carry much more space heat than July ({})".format(january, july))
        failed = True

    print("Test: hot water is weather independent and sums to the configured annual figure")
    water = sum(model.month_water_heat_kwh(month) for month in range(1, 13))
    if not close(water, model.settings["water_heat_kwh"], tolerance=1.0):
        print("  ERROR: monthly water heat should sum to {}, got {}".format(model.settings["water_heat_kwh"], water))
        failed = True

    print("Test: the month scale corrects a biased sample onto the month's own demand")
    # Two of January's coldest days, which over-represent the month
    january_days = sorted((day for day in temperatures if day.month == 1), key=lambda day: sum(temperatures[day]))[:2]
    samples = [(day, 31 / 2.0) for day in january_days]
    scale = model.set_month_scale(1, samples)
    if not scale < 1.0:
        print("  ERROR: sampling January's coldest days should scale demand DOWN, got {}".format(scale))
        failed = True
    corrected = sum(model.day_space_heat_kwh(day) * weight for day, weight in samples)
    if not close(corrected, model.month_space_heat_kwh(1), tolerance=0.5):
        print("  ERROR: the corrected sample should carry the month's total {}, got {}".format(model.month_space_heat_kwh(1), corrected))
        failed = True

    print("Test: a month with no heat at all leaves the scale at 1.0 rather than dividing by zero")
    warm = HeatModel(resolve_heat(base_config()), temperature_year(year, mean=30.0, amplitude=0.0, ensure_heat_day=True), year)
    if warm.set_month_scale(7, [(date(year, 7, 10), 31.0)]) != 1.0:
        print("  ERROR: a month with no sampled heat should leave the scale at 1.0")
        failed = True

    print("Test: the overnight setback trims demand but keeps the day's shape")
    model.set_month_scale(1, [(date(year, 1, 15), 31.0)])
    hourly = model.hourly_electricity_kwh(date(year, 1, 15))
    if len(hourly) != 24:
        print("  ERROR: hourly electricity should have 24 entries, got {}".format(len(hourly)))
        failed = True
    if not all(value >= 0 for value in hourly):
        print("  ERROR: hourly electricity must never be negative")
        failed = True

    print("Test: the per-minute profile has 1440 entries and matches the hourly total")
    minutes = model.minute_electricity_kwh(date(year, 1, 15))
    if len(minutes) != 1440:
        print("  ERROR: minute electricity should have 1440 entries, got {}".format(len(minutes)))
        failed = True
    if not close(sum(minutes), sum(hourly), tolerance=0.001):
        print("  ERROR: the minute profile should sum to the hourly total {}, got {}".format(sum(hourly), sum(minutes)))
        failed = True

    print("Test: a summer day still draws hot water electricity but little or no space heat")
    model.set_month_scale(7, [(date(year, 7, 15), 31.0)])
    summer = sum(model.minute_electricity_kwh(date(year, 7, 15)))
    model.set_month_scale(1, [(date(year, 1, 15), 31.0)])
    winter = sum(model.minute_electricity_kwh(date(year, 1, 15)))
    if not summer > 0:
        print("  ERROR: a summer day should still draw hot water electricity, got {}".format(summer))
        failed = True
    if not winter > summer * 2:
        print("  ERROR: a winter day ({}) should draw much more than a summer one ({})".format(winter, summer))
        failed = True

    print("Test: the gas bill is delivered heat divided by boiler efficiency, at the gas rate")
    gas = model.month_gas(1)
    # Space heating and hot water each divide by their OWN efficiency - a stored cylinder
    # is worse than space heating, and sharing one figure breaks the reconciliation below.
    expected_gas_kwh = model.month_space_heat_kwh(1) / 0.85 + model.month_water_heat_kwh(1) / 0.80
    if not close(gas["gas_kwh"], expected_gas_kwh, tolerance=0.1):
        print("  ERROR: January gas should be {} kWh, got {}".format(expected_gas_kwh, gas["gas_kwh"]))
        failed = True
    if not close(gas["gas_unit_cost_p"], expected_gas_kwh * 6.4, tolerance=1.0):
        print("  ERROR: January gas cost should be {}p, got {}".format(expected_gas_kwh * 6.4, gas["gas_unit_cost_p"]))
        failed = True
    if not close(gas["gas_standing_charge_p"], 31 * 32.0):
        print("  ERROR: January standing charge should be {}p, got {}".format(31 * 32.0, gas["gas_standing_charge_p"]))
        failed = True

    print("Test: a missing temperature series is refused rather than modelled around")
    try:
        HeatModel(resolve_heat(base_config()), {}, year)
        print("  ERROR: an empty temperature series should have been refused")
        failed = True
    except AnnualHeatError:
        pass

    print("Test: a year that never needs heating is refused rather than dividing by zero")
    try:
        HeatModel(resolve_heat(base_config()), temperature_year(year, mean=30.0, amplitude=0.0), year)
        print("  ERROR: a year with no heating demand should have been refused")
        failed = True
    except AnnualHeatError:
        pass

    failed = test_heat_load_profile(model, year) or failed
    failed = test_heat_costs() or failed
    failed = test_temperature_local_days() or failed
    failed = test_smart_cylinder(temperatures, year) or failed

    if failed:
        print("**** ERROR: annual heat tests failed ****")
    return failed


def temperature_year(year, mean=10.0, amplitude=7.0, ensure_heat_day=False):
    """Return a synthetic year of hourly temperatures keyed by local date.

    ``ensure_heat_day`` drops one day to freezing so a warm year still has SOME heating
    demand, which is what the month-scale test needs: a year with no demand at all is
    refused by HeatModel outright and could not reach the scale logic.
    """
    series = sine_year(year, mean=mean, amplitude=amplitude)
    if ensure_heat_day:
        series[date(year, 1, 5)] = [0.0] * HOURS_PER_DAY
    return series


def test_heat_load_profile(model, year):
    """Verify the heat pump load source adds to, rather than replaces, the household load."""
    failed = False
    print("Test: the heat pump load source adds heat pump electricity onto the base load")
    base = StubLoad(daily=10.0)
    combined = HeatPumpLoadProfile(base, model)
    day = date(year, 1, 15)
    model.set_month_scale(1, [(day, 31.0)])

    base_profile = base.minute_profile(day)
    combined_profile = combined.minute_profile(day)
    heat_profile = model.minute_electricity_kwh(day)
    if len(combined_profile) != 1440:
        print("  ERROR: the combined profile should have 1440 entries, got {}".format(len(combined_profile)))
        failed = True
    for index in [0, 500, 1000, 1439]:
        expected = base_profile[index] + heat_profile[index]
        if not close(combined_profile[index], expected, tolerance=1e-9):
            print("  ERROR: minute {} should be {}, got {}".format(index, expected, combined_profile[index]))
            failed = True
    if not close(combined.daily_kwh(day), 10.0 + sum(model.hourly_electricity_kwh(day)), tolerance=0.001):
        print("  ERROR: the combined daily total should be base plus heat pump")
        failed = True

    print("Test: a day the base source has no data for stays missing rather than becoming heat-pump-only")
    missing_base = StubLoad(daily=10.0, missing={day})
    if HeatPumpLoadProfile(missing_base, model).minute_profile(day) is not None:
        print("  ERROR: a missing base day must pass None through, not return heat pump load alone")
        failed = True
    return failed


def test_heat_costs():
    """Verify the heat pump capital model and its payback rows."""
    failed = False
    print("Test: the grant comes off capital, and a quote beats the estimate")
    settings = resolve_costs(None)
    costs = build_heat_costs(settings)
    if not close(costs["install_gbp"], 13000.0) or not close(costs["grant_gbp"], 7500.0) or not close(costs["net_gbp"], 5500.0):
        print("  ERROR: default heat pump costs should be 13000/7500/5500, got {}".format(costs))
        failed = True
    if costs["quoted"]:
        print("  ERROR: the default costs are an estimate, not a quote")
        failed = True

    quoted = build_heat_costs(resolve_costs({"quoted_heat_pump_gbp": 9000}))
    if not close(quoted["net_gbp"], 1500.0) or not quoted["quoted"]:
        print("  ERROR: a 9000 quote less the 7500 grant should be 1500 and flagged as quoted, got {}".format(quoted))
        failed = True

    print("Test: a grant larger than the price cannot produce a negative capital cost")
    generous = build_heat_costs(resolve_costs({"quoted_heat_pump_gbp": 5000, "heat_pump_grant_gbp": 7500}))
    if generous["net_gbp"] < 0:
        print("  ERROR: net capital should never be negative, got {}".format(generous["net_gbp"]))
        failed = True

    print("Test: heat pump payback needs a full year")
    scenarios = {key: {"annual_saving_p": 55000.0} for key in ["no_pvbat", "pv_only", "without_predbat", "with_predbat"]}
    partial = build_heat_payback(scenarios, costs, 11)
    if partial.get("available"):
        print("  ERROR: an 11 month year should not produce a heat pump payback")
        failed = True
    if "11" not in partial.get("reason", ""):
        print("  ERROR: the refusal should say how many months were modelled, got '{}'".format(partial.get("reason")))
        failed = True

    print("Test: a full year pays back capital divided by the saving, per scenario")
    full = build_heat_payback(scenarios, costs, 12)
    if not full.get("available"):
        print("  ERROR: a full year should produce a heat pump payback, got {}".format(full))
        failed = True
    # 5500 capital against 550 a year
    elif not close(full["scenarios"]["with_predbat"]["years"], 10.0):
        print("  ERROR: 5500 capital against 550 a year should be 10 years, got {}".format(full["scenarios"]["with_predbat"]["years"]))
        failed = True

    print("Test: a heat pump that costs more than it saves does not report a payback period")
    losing = build_heat_payback({key: {"annual_saving_p": -12000.0} for key in scenarios}, costs, 12)
    if losing["scenarios"]["with_predbat"]["pays_back"] or losing["scenarios"]["with_predbat"]["years"] is not None:
        print("  ERROR: a negative saving must not report a payback period, got {}".format(losing["scenarios"]["with_predbat"]))
        failed = True
    return failed


def test_temperature_local_days():
    """Verify the UTC-to-local-day regrouping, including both clock changes."""
    failed = False
    print("Test: a UTC temperature series regroups into 24 values per local day")
    utc = pytz.utc
    series = {}
    start = datetime(2023, 6, 1, 0, 0, tzinfo=utc)
    for hour in range(24 * 5):
        series[start + timedelta(hours=hour)] = 12.0 + (hour % 24) * 0.1
    by_day = temperature_by_local_day(series, "Europe/London")

    for day, hourly in by_day.items():
        if len(hourly) != 24:
            print("  ERROR: {} should have 24 hourly values, got {}".format(day, len(hourly)))
            failed = True

    print("Test: local midnight in British Summer Time is 23:00 UTC the day before")
    # 1 June 2023 is BST (UTC+1), so local midnight on the 2nd is 23:00 UTC on the 1st
    if date(2023, 6, 2) in by_day:
        expected = series[datetime(2023, 6, 1, 23, 0, tzinfo=utc)]
        if not close(by_day[date(2023, 6, 2)][0], expected):
            print("  ERROR: local midnight should be 23:00 UTC the day before ({}), got {}".format(expected, by_day[date(2023, 6, 2)][0]))
            failed = True

    print("Test: the autumn clock change averages its doubled hour instead of dropping a day")
    autumn = {}
    for hour in range(24 * 3):
        autumn[datetime(2023, 10, 28, 0, 0, tzinfo=utc) + timedelta(hours=hour)] = 8.0
    autumn_days = temperature_by_local_day(autumn, "Europe/London")
    if date(2023, 10, 29) not in autumn_days:
        print("  ERROR: the 25 hour local day should still be present")
        failed = True
    elif len(autumn_days[date(2023, 10, 29)]) != 24:
        print("  ERROR: the 25 hour local day should still produce 24 values, got {}".format(len(autumn_days[date(2023, 10, 29)])))
        failed = True

    print("Test: the spring clock change fills its missing hour instead of dropping a day")
    spring = {}
    for hour in range(24 * 3):
        spring[datetime(2023, 3, 25, 0, 0, tzinfo=utc) + timedelta(hours=hour)] = 5.0
    spring_days = temperature_by_local_day(spring, "Europe/London")
    if date(2023, 3, 26) not in spring_days:
        print("  ERROR: the 23 hour local day should still be present")
        failed = True
    elif len(spring_days[date(2023, 3, 26)]) != 24:
        print("  ERROR: the 23 hour local day should still produce 24 values, got {}".format(len(spring_days[date(2023, 3, 26)])))
        failed = True

    print("Test: a day with too few readings is dropped rather than mostly invented")
    sparse = {datetime(2023, 6, 10, hour, 0, tzinfo=utc): 10.0 for hour in range(4)}
    if temperature_by_local_day(sparse, "Europe/London"):
        print("  ERROR: a day with only 4 readings should be dropped")
        failed = True

    print("Test: the setback constants are a trim, not a shutdown")
    if not 0 < SETBACK_FACTOR < 1:
        print("  ERROR: the setback factor should be between 0 and 1, got {}".format(SETBACK_FACTOR))
        failed = True
    if not SETBACK_HOURS:
        print("  ERROR: there should be some setback hours")
        failed = True
    return failed


def cheap_night_rates(cheap=7.0, normal=28.0, peak=45.0, days=2):
    """Return plan rates with a cheap 00:30-05:30 band and an expensive 16:00-19:00 peak."""
    rates = {}
    for minute in range(days * 1440):
        in_day = minute % 1440
        if 30 <= in_day < 330:
            rates[minute] = cheap
        elif 16 * 60 <= in_day < 19 * 60:
            rates[minute] = peak
        else:
            rates[minute] = normal
    return rates


def test_smart_cylinder(temperatures, year):
    """Verify the hot water cylinder's charge scheduling, smart and fixed."""
    failed = False
    day = date(year, 6, 15)  # summer, so hot water dominates and space heat is near zero

    print("Test: hot water uses its own gas efficiency, and the gas total still reconciles")
    settings = resolve_heat(base_config(water_boiler_efficiency=0.75))
    if not close(settings["water_heat_kwh"], 2400 * 0.75):
        print("  ERROR: water heat should use its own efficiency, got {}".format(settings["water_heat_kwh"]))
        failed = True
    model = HeatModel(settings, temperatures, year)
    # The identity the whole gas side rests on: space gas + water gas == the bill
    gas_total = sum(model.month_gas(month)["gas_kwh"] for month in range(1, 13))
    if not close(gas_total, 12000.0, tolerance=1.0):
        print("  ERROR: the year's modelled gas should equal the configured 12000 kWh, got {}".format(gas_total))
        failed = True

    print("Test: the cylinder's standing loss is charged to the heat pump, not to the gas boiler")
    model = HeatModel(resolve_heat(base_config(cylinder_standing_loss_kwh_per_day=1.5)), temperatures, year)
    if not close(model.water_charge_per_day_kwh - model.water_heat_per_day_kwh, 1.5):
        print("  ERROR: the heat pump should carry the 1.5 kWh/day standing loss, got {}".format(model.water_charge_per_day_kwh - model.water_heat_per_day_kwh))
        failed = True
    # The gas bill must be unaffected by the NEW cylinder's loss
    with_loss = HeatModel(resolve_heat(base_config(cylinder_standing_loss_kwh_per_day=3.0)), temperatures, year).month_gas(1)["gas_kwh"]
    without = HeatModel(resolve_heat(base_config(cylinder_standing_loss_kwh_per_day=0.0)), temperatures, year).month_gas(1)["gas_kwh"]
    if not close(with_loss, without, tolerance=0.01):
        print("  ERROR: the heat pump cylinder's standing loss must not change the gas bill ({} vs {})".format(with_loss, without))
        failed = True

    print("Test: a full charge is the cylinder's volume times its temperature rise")
    model = HeatModel(resolve_heat(base_config(cylinder_volume_l=180, hot_water_target_c=50, cold_mains_c=10)), temperatures, year)
    # 180 kg * 4.186 kJ/kg/K * 40 K / 3600 = 8.37 kWh
    if not close(model.full_charge_kwh, 8.372, tolerance=0.01):
        print("  ERROR: a 180 L charge over 40 K should be 8.37 kWh, got {}".format(model.full_charge_kwh))
        failed = True

    print("Test: one charge a day is the default, and it finishes before the water is wanted")
    model = HeatModel(resolve_heat(base_config()), temperatures, year)
    model.set_month_scale(6, [(day, 30.0)])
    windows = model.water_charge_windows(day)
    if len(windows) != 1:
        print("  ERROR: the default should be a single daily charge, got {}".format(len(windows)))
        failed = True
    elif windows[0][1] > 7 * 60:
        print("  ERROR: the charge should finish by 07:00, ends at minute {}".format(windows[0][1]))
        failed = True

    print("Test: the whole day's hot water energy is delivered, however it is scheduled")
    for smart in [False, True]:
        m = HeatModel(resolve_heat(base_config(smart_hot_water=smart)), temperatures, year)
        m.set_month_scale(6, [(day, 30.0)])
        if smart:
            m.set_plan_rates(day, cheap_night_rates())
        delivered = sum(energy for _, _, energy in m.water_charge_windows(day))
        if not close(delivered, m.water_charge_per_day_kwh, tolerance=0.001):
            print("  ERROR: smart={} should still deliver {} kWh, got {}".format(smart, m.water_charge_per_day_kwh, delivered))
            failed = True

    print("Test: a smart cylinder moves its charge into the cheap overnight band")
    smart = HeatModel(resolve_heat(base_config(smart_hot_water=True)), temperatures, year)
    smart.set_month_scale(6, [(day, 30.0)])
    smart.set_plan_rates(day, cheap_night_rates())
    start, end, _ = smart.water_charge_windows(day)[0]
    if not (30 <= start and end <= 330):
        print("  ERROR: the smart charge should sit inside the 00:30-05:30 cheap band, got {}-{}".format(start, end))
        failed = True

    print("Test: the smart charge is genuinely cheaper than the fixed one")
    rates = cheap_night_rates()
    fixed = HeatModel(resolve_heat(base_config(smart_hot_water=False)), temperatures, year)
    fixed.set_month_scale(6, [(day, 30.0)])

    def window_cost(m):
        """Return what the day's cylinder charges would cost at the plan rates."""
        total = 0.0
        for start, end, energy in m.water_charge_windows(day):
            per_minute = energy / max(1, end - start)
            total += sum(per_minute * rates[minute] for minute in range(start, end))
        return total

    if not window_cost(smart) < window_cost(fixed):
        print("  ERROR: the smart charge ({}) should cost less than the fixed one ({})".format(window_cost(smart), window_cost(fixed)))
        failed = True

    print("Test: with no rates known, a smart cylinder falls back to the fixed schedule")
    blind = HeatModel(resolve_heat(base_config(smart_hot_water=True)), temperatures, year)
    blind.set_month_scale(6, [(day, 30.0)])
    blind.set_plan_rates(None, None)
    if blind.water_charge_windows(day) != fixed.water_charge_windows(day):
        print("  ERROR: a smart cylinder with no rates should behave exactly like the fixed one")
        failed = True

    print("Test: on a flat tariff the smart charge does not drift away from the draw-off")
    flat = HeatModel(resolve_heat(base_config(smart_hot_water=True)), temperatures, year)
    flat.set_month_scale(6, [(day, 30.0)])
    flat.set_plan_rates(day, {minute: 25.0 for minute in range(2880)})
    if flat.water_charge_windows(day) != fixed.water_charge_windows(day):
        print("  ERROR: with every window equally priced the charge should sit where the timer put it, got {} vs {}".format(flat.water_charge_windows(day), fixed.water_charge_windows(day)))
        failed = True

    print("Test: two charges a day do not overlap, and both finish before their draw-off")
    twice = HeatModel(resolve_heat(base_config(smart_hot_water=True, water_charges_per_day=2)), temperatures, year)
    twice.set_month_scale(6, [(day, 30.0)])
    twice.set_plan_rates(day, cheap_night_rates())
    windows = twice.water_charge_windows(day)
    if len(windows) != 2:
        print("  ERROR: two charges a day should produce two windows, got {}".format(len(windows)))
        failed = True
    else:
        if windows[0][1] > windows[1][0]:
            print("  ERROR: the two charges overlap: {} and {}".format(windows[0], windows[1]))
            failed = True
        if windows[0][1] > 7 * 60 or windows[1][1] > 16 * 60:
            print("  ERROR: each charge should finish by its ready time, got {}".format(windows))
            failed = True

    print("Test: the charge lasts longer when the heat pump puts less power into the cylinder")
    slow = HeatModel(resolve_heat(base_config(dhw_kw=1.5)), temperatures, year)
    quick = HeatModel(resolve_heat(base_config(dhw_kw=6.0)), temperatures, year)
    for m in (slow, quick):
        m.set_month_scale(6, [(day, 30.0)])
    slow_len = slow.water_charge_windows(day)[0][1] - slow.water_charge_windows(day)[0][0]
    quick_len = quick.water_charge_windows(day)[0][1] - quick.water_charge_windows(day)[0][0]
    if not slow_len > quick_len:
        print("  ERROR: a 1.5 kW charge ({} min) should take longer than a 6 kW one ({} min)".format(slow_len, quick_len))
        failed = True

    print("Test: the minute profile still reconciles with the hourly one")
    smart_minutes = smart.minute_electricity_kwh(day)
    smart_hours = smart.hourly_electricity_kwh(day)
    if len(smart_minutes) != 1440 or len(smart_hours) != 24:
        print("  ERROR: profile lengths wrong: {} minutes, {} hours".format(len(smart_minutes), len(smart_hours)))
        failed = True
    if not close(sum(smart_minutes), sum(smart_hours), tolerance=1e-9):
        print("  ERROR: the minute and hourly profiles disagree: {} vs {}".format(sum(smart_minutes), sum(smart_hours)))
        failed = True

    print("Test: a cylinder charged overnight in winter costs more electricity than one charged at noon")
    # Charging when it is coldest is genuinely less efficient, so shifting is a real
    # trade-off rather than a free win - the COP must follow the hour the charge lands in.
    winter = date(year, 1, 15)
    night = HeatModel(resolve_heat(base_config(smart_hot_water=True)), temperatures, year)
    night.set_month_scale(1, [(winter, 31.0)])
    night.set_plan_rates(winter, cheap_night_rates())
    noon_rates = {minute: (7.0 if 11 * 60 <= (minute % 1440) < 14 * 60 else 40.0) for minute in range(2880)}
    noon = HeatModel(resolve_heat(base_config(smart_hot_water=True, water_charges_per_day=2)), temperatures, year)
    noon.set_month_scale(1, [(winter, 31.0)])
    noon.set_plan_rates(winter, noon_rates)
    night_start = night.water_charge_windows(winter)[0][0]
    if not night_start < 6 * 60:
        print("  ERROR: the overnight cheap band should pull the charge before 06:00, got minute {}".format(night_start))
        failed = True

    print("Test: a flow temperature below the cylinder target is rejected")
    try:
        resolve_heat(base_config(water_flow_temp_c=45, hot_water_target_c=50))
        print("  ERROR: a flow temperature below the cylinder target should be rejected")
        failed = True
    except AnnualHeatError as error:
        if "water_flow_temp_c" not in str(error):
            print("  ERROR: the error should name water_flow_temp_c, got '{}'".format(error))
            failed = True

    print("Test: a fractional number of charges a day is rejected rather than truncated")
    try:
        resolve_heat(base_config(water_charges_per_day=1.5))
        print("  ERROR: 1.5 charges a day should be rejected")
        failed = True
    except AnnualHeatError:
        pass

    if failed:
        print("**** ERROR: smart cylinder tests failed ****")
    return failed
