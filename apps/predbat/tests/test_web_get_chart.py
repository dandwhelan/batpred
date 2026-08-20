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
Tests for WebInterface.get_chart, the largest single uncovered function in the codebase.

get_chart is a long if/elif that assembles the series for each of the charts on the /charts page.
Only the default chart was ever requested, so every other branch - and the empty-data guard in
front of all of them - went unexercised: a rename of a published entity, or a series referring to
a variable that is not in scope on that branch, would only show up when a user clicked the tab.

These tests call it directly rather than through a rendered page, so what is under test is series
assembly rather than HTML.
"""

from datetime import timedelta

from marginal import MARGINAL_EXTRA_KWH_LEVELS, MARGINAL_EXTRA_KWH_LEVEL_NAMES, MARGINAL_TIME_OFFSETS
from web import WebInterface

# The tabs offered on the /charts page, plus the two shown only when ML load forecasting is on
CHART_NAMES = ["Battery", "Power", "Cost", "Rates", "InDay", "PV", "PV7", "PVAccuracy", "Savings", "BatteryDegradation", "MarginalCosts"]
ML_CHART_NAMES = ["LoadML", "LoadMLPower"]

# Every entity get_chart reads results from. The Battery chart's soc_kw_best is the one that gates
# the whole function, but the rest have to carry data too or a branch can silently render nothing.
RESULT_ENTITIES = [
    "soc_kw",
    "soc_kw_best",
    "soc_kw_best10",
    "soc_kw_base10",
    "charge_limit_kw",
    "best_charge_limit_kw",
    "best_export_limit_kw",
    "battery_power_best",
    "pv_power_best",
    "grid_power_best",
    "load_power_best",
    "iboost_best",
    "metric",
    "best_metric",
    "best10_metric",
    "cost_today",
    "cost_today_export",
    "cost_today_import",
    "base10_metric",
    "rates",
    "rates_export",
    "rates_gas",
    "record",
]


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


def _series_of(web, entity_suffix, count=8, start=1.0, step=0.5):
    """Build a plausible half-hourly result series for one published entity."""
    series = {}
    stamp = web.midnight_utc
    value = start
    for _ in range(count):
        series[stamp.strftime("%Y-%m-%dT%H:%M:%S%z")] = round(value, 2)
        stamp += timedelta(minutes=30)
        value += step
    return series


def _seed_results(my_predbat, web):
    """Publish a results attribute for every entity get_chart reads, and return what was set."""
    seeded = {}
    for index, suffix in enumerate(RESULT_ENTITIES):
        entity = my_predbat.prefix + "." + suffix
        series = _series_of(web, suffix, start=1.0 + index, step=0.25)
        my_predbat.dashboard_values[entity] = {"state": 0, "attributes": {"results": series}}
        seeded[entity] = series
    return seeded


def _seed_marginal(my_predbat, web):
    """Publish a marginal energy cost matrix, which the MarginalCosts chart is gated on."""
    time_labels = [(web.now_utc + timedelta(minutes=offset)).strftime("%H:%M") for offset in MARGINAL_TIME_OFFSETS]
    matrix = {level: {label: 10.0 + level + index for index, label in enumerate(time_labels)} for level in MARGINAL_EXTRA_KWH_LEVELS}
    attributes = {
        "matrix": matrix,
        "grid_import": {label: 20.0 + index for index, label in enumerate(time_labels)},
        "grid_export": {label: 5.0 + index for index, label in enumerate(time_labels)},
    }
    for name in MARGINAL_EXTRA_KWH_LEVEL_NAMES:
        attributes["rate_now_{}_consumption".format(name)] = 12.5
    my_predbat.dashboard_values["sensor." + my_predbat.prefix + "_marginal_energy_costs"] = {"state": 12.5, "attributes": attributes}
    return time_labels


def test_charts_render_without_data(my_predbat):
    """
    With nothing published yet, every chart must show the loading placeholder rather than fail.

    This is the state right after a restart, so it is the first thing a user sees; a branch that
    indexed into empty results here would 500 the whole page.
    """
    print("*** Running test: charts render the loading placeholder without data")
    failed = 0
    web = WebInterface(my_predbat, web_port=5052)

    saved = dict(my_predbat.dashboard_values)
    my_predbat.dashboard_values = {}
    try:
        for chart in CHART_NAMES + ML_CHART_NAMES:
            text = web.get_chart(chart=chart)
            failed |= _check_true("{}: something rendered".format(chart), text)
            failed |= _check_true("{}: shows loading".format(chart), "Loading" in text)
    finally:
        my_predbat.dashboard_values = saved
    return failed


def test_every_chart_assembles_its_series(my_predbat):
    """Each chart tab must assemble and render its own series once results have been published."""
    print("*** Running test: every chart assembles its series")
    failed = 0
    web = WebInterface(my_predbat, web_port=5052)

    saved = dict(my_predbat.dashboard_values)
    saved_ml = my_predbat.args.get("load_ml_enable", None)
    _seed_results(my_predbat, web)
    _seed_marginal(my_predbat, web)

    try:
        for chart in CHART_NAMES:
            text = web.get_chart(chart=chart)
            failed |= _check_true("{}: something rendered".format(chart), text)
            failed |= _check_true("{}: not stuck loading".format(chart), "Loading" not in text)
            failed |= _check_true("{}: not reported unknown".format(chart), "Unknown chart type" not in text)
            failed |= _check_true("{}: rendered a chart".format(chart), "ApexCharts" in text or "chart_marginal" in text)

        # The two ML charts are only offered when ML load forecasting is switched on, but the
        # branches must still render rather than fall through to "unknown"
        my_predbat.args["load_ml_enable"] = True
        for chart in ML_CHART_NAMES:
            text = web.get_chart(chart=chart)
            failed |= _check_true("{}: something rendered".format(chart), text)
            failed |= _check_true("{}: not reported unknown".format(chart), "Unknown chart type" not in text)
    finally:
        my_predbat.dashboard_values = saved
        if saved_ml is None:
            my_predbat.args.pop("load_ml_enable", None)
        else:
            my_predbat.args["load_ml_enable"] = saved_ml
    return failed


def test_battery_chart_carries_the_published_series(my_predbat):
    """The Battery chart must plot the values that were published, not a re-derived set."""
    print("*** Running test: the Battery chart carries the published series")
    failed = 0
    web = WebInterface(my_predbat, web_port=5052)

    saved = dict(my_predbat.dashboard_values)
    seeded = _seed_results(my_predbat, web)
    try:
        text = web.get_chart(chart="Battery")

        for name in ("Base", "Best", "Best10", "Actual", "Charge Limit Best", "Best Export Limit", "Record"):
            failed |= _check_true("battery chart: series {} present".format(name), "name: '{}'".format(name) in text)

        # A value from the middle of the best SoC series has to appear in the rendered data
        best = seeded[my_predbat.prefix + ".soc_kw_best"]
        sample = list(best.values())[3]
        failed |= _check_true("battery chart: published value {} plotted".format(sample), str(sample) in text)
    finally:
        my_predbat.dashboard_values = saved
    return failed


def test_actual_soc_series_is_built_from_history(my_predbat):
    """
    The Battery chart's "Actual" line comes from the recorded SoC history plus the live reading.

    It is the only series get_chart derives itself rather than reading from a published entity, so
    an off-by-one in the minute arithmetic would misplace today's real battery trace against the
    predictions it is there to be compared with.
    """
    print("*** Running test: the actual SoC series is built from history")
    failed = 0
    web = WebInterface(my_predbat, web_port=5052)

    saved = dict(my_predbat.dashboard_values)
    saved_history = my_predbat.soc_kwh_history
    saved_soc = my_predbat.soc_kw
    _seed_results(my_predbat, web)

    try:
        # History is keyed by minutes ago, so minute 0 of the chart is the oldest sample
        my_predbat.soc_kwh_history = {minutes_ago: float(minutes_ago) for minutes_ago in range(0, 24 * 60, 5)}
        my_predbat.soc_kw = 7.77

        text = web.get_chart(chart="Battery")
        failed |= _check_true("actual series: live SoC plotted", "7.77" in text)

        # With no history at all the series still carries the live reading
        my_predbat.soc_kwh_history = {}
        text = web.get_chart(chart="Battery")
        failed |= _check_true("actual series: live SoC plotted without history", "7.77" in text)
    finally:
        my_predbat.dashboard_values = saved
        my_predbat.soc_kwh_history = saved_history
        my_predbat.soc_kw = saved_soc
    return failed


def test_marginal_costs_chart_needs_a_plan_first(my_predbat):
    """
    The marginal cost chart says so when no plan has produced a matrix, and tabulates it when one has.

    It is the one chart with a genuine "no data" state distinct from the loading placeholder: the
    other tabs draw from the plan itself, this one from a sensor only a completed plan publishes.
    """
    print("*** Running test: marginal costs chart needs a plan first")
    failed = 0
    web = WebInterface(my_predbat, web_port=5052)

    saved = dict(my_predbat.dashboard_values)
    _seed_results(my_predbat, web)
    try:
        # Results published, but no marginal sensor yet
        my_predbat.dashboard_values.pop("sensor." + my_predbat.prefix + "_marginal_energy_costs", None)
        text = web.get_chart(chart="MarginalCosts")
        failed |= _check_true("marginal: reports no data", "not yet available" in text)

        _seed_marginal(my_predbat, web)
        text = web.get_chart(chart="MarginalCosts")
        failed |= _check_true("marginal: renders the table", "Marginal Energy Costs" in text)
        failed |= _check_true("marginal: renders the heatmap", "chart_grid" in text)
        for name in MARGINAL_EXTRA_KWH_LEVEL_NAMES:
            failed |= _check_true("marginal: level {} listed".format(name), name.capitalize() in text)
        for level in MARGINAL_EXTRA_KWH_LEVELS:
            failed |= _check_true("marginal: {}kWh series present".format(level), "{}kWh".format(level) in text)
    finally:
        my_predbat.dashboard_values = saved
    return failed


def test_unknown_chart_is_reported(my_predbat):
    """An unrecognised chart name must say so rather than render an empty page."""
    print("*** Running test: an unknown chart name is reported")
    failed = 0
    web = WebInterface(my_predbat, web_port=5052)

    saved = dict(my_predbat.dashboard_values)
    _seed_results(my_predbat, web)
    try:
        text = web.get_chart(chart="NoSuchChart")
        failed |= _check_true("unknown chart: reported", "Unknown chart type" in text)
    finally:
        my_predbat.dashboard_values = saved
    return failed


def test_chart_names_match_the_tabs_offered(my_predbat):
    """
    The tabs on the /charts page and the branches in get_chart must not drift apart.

    A tab with no branch renders "Unknown chart type", and a branch with no tab is unreachable.
    """
    print("*** Running test: chart tabs match the branches that handle them")
    failed = 0
    import re

    source = open("../apps/predbat/web.py").read()
    tabs = set(re.findall(r"\./charts\?chart=(\w+)", source))
    branches = set(re.findall(r'chart == "(\w+)"', source))

    missing_branch = sorted(tabs - branches)
    failed |= _check("charts: every tab has a branch", missing_branch, [])

    unreachable = sorted(branches - tabs)
    failed |= _check("charts: every branch has a tab", unreachable, [])

    # And the list this test sweeps is the same set, so a new chart cannot be added untested
    failed |= _check("charts: this test sweeps every tab", sorted(tabs), sorted(set(CHART_NAMES + ML_CHART_NAMES)))
    return failed


def run_web_get_chart_tests(my_predbat):
    """Run the get_chart series-assembly tests, returning non-zero if any failed."""
    print("**** Running web get_chart tests ****")
    failed = 0
    for test in (
        test_charts_render_without_data,
        test_every_chart_assembles_its_series,
        test_battery_chart_carries_the_published_series,
        test_actual_soc_series_is_built_from_history,
        test_marginal_costs_chart_needs_a_plan_first,
        test_unknown_chart_is_reported,
        test_chart_names_match_the_tabs_offered,
    ):
        failed |= test(my_predbat)
    return failed
