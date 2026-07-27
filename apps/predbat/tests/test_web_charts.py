# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init

"""Unit tests for the ApexCharts rendering helpers in web.py."""

from web import WebInterface
from web_helper import CHART_PALETTE_LIGHT, CHART_PALETTE_DARK, get_chart_theme_js


def make_web(my_predbat):
    """Create a WebInterface instance bound to the given predbat."""
    return WebInterface(my_predbat, web_port=5052)


def sample_series():
    """Return a small set of chart series covering explicit colours, defaults and empty data."""
    return [
        {"name": "Explicit", "data": {"2025-06-01T10:00:00+00:00": 1.5, "2025-06-01T10:30:00+00:00": 2.0}, "color": "#e34948"},
        {"name": "Defaulted", "data": {"2025-06-01T10:00:00+00:00": 3.0}, "chart_type": "area", "opacity": "0.3"},
        {"name": "Defaulted2", "data": {"2025-06-01T10:00:00+00:00": 4.0}},
        {"name": "Empty", "data": {}},
    ]


def run_web_charts_tests(my_predbat):
    """Unit tests for chart rendering (theme, palette assignment and resize handling)."""
    failed = 0
    print("**** Running web charts tests ****")

    web = make_web(my_predbat)
    now_str = "2025-06-01T11:00:00+0000"

    # -------------------------------------------------------------------------
    print("Test: chart theme helper embeds both palettes and the resize registry")
    theme = get_chart_theme_js()
    for color in CHART_PALETTE_LIGHT + CHART_PALETTE_DARK:
        if color not in theme:
            print(f"  ERROR: palette colour {color} missing from theme JS")
            failed += 1
    if "pbRegisterChart" not in theme:
        print("  ERROR: pbRegisterChart missing from theme JS")
        failed += 1
    if "dark-mode" not in theme:
        print("  ERROR: dark-mode detection missing from theme JS")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: render_chart output uses the shared theme")
    html = web.render_chart(sample_series(), "kWh", "Test Chart", now_str)
    for expected in [
        "fontFamily: pbChartFont",
        "foreColor: pbChartInk",
        "theme: { mode: pbChartDark ? 'dark' : 'light' }",
        "colors: pbSeriesColors",
        "borderColor: pbChartGrid",
        "pbRegisterChart(chart, pbChartSize);",
    ]:
        if expected not in html:
            print(f"  ERROR: expected '{expected}' in rendered chart")
            failed += 1
    if "location.reload" in html:
        print("  ERROR: rendered chart still reloads the page on resize")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: explicit series colours are kept and defaults come from the palette in order")
    if "var pbSeriesColors = pbChartDark ? ['#e34948','{}','{}'] : ['#e34948','{}','{}'];".format(CHART_PALETTE_DARK[0], CHART_PALETTE_DARK[1], CHART_PALETTE_LIGHT[0], CHART_PALETTE_LIGHT[1]) not in html:
        print("  ERROR: series colour arrays not assigned as expected")
        failed += 1
    if "name: 'Empty'" in html:
        print("  ERROR: series with no data should not be rendered")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: chart script is wrapped in an IIFE with balanced delimiters")
    script = html.split("<script>")[-1].split("</script>")[0]
    if "(function() {" not in script or "})();" not in script:
        print("  ERROR: chart script is not wrapped in an IIFE")
        failed += 1
    for open_char, close_char in [("{", "}"), ("(", ")"), ("[", "]")]:
        if script.count(open_char) != script.count(close_char):
            print(f"  ERROR: unbalanced '{open_char}{close_char}' in chart script ({script.count(open_char)} vs {script.count(close_char)})")
            failed += 1

    # -------------------------------------------------------------------------
    print("Test: weekly (non-daily) chart and extra y-axis render with the theme")
    html_weekly = web.render_chart(sample_series(), "kWh", "Weekly", now_str, daily_chart=False, extra_yaxis=[{"title": "%", "series_name": "Defaulted", "opposite": True}])
    if "pbRegisterChart(chart, pbChartSize);" not in html_weekly:
        print("  ERROR: weekly chart is not registered for resize")
        failed += 1
    if "opposite: true" not in html_weekly:
        print("  ERROR: extra y-axis missing from weekly chart")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: heatmap chart uses the shared theme and no page reload")
    heat = web.render_heatmap_chart([{"name": "row", "data": [{"x": "Mon", "y": 1.0}, {"x": "Tue", "y": None}]}], "Heat", 0, 10, chart_id="hm")
    if "fontFamily: pbChartFont" not in heat or "pbRegisterChart(chart_hm, pbChartSize);" not in heat:
        print("  ERROR: heatmap chart is missing theme or resize registration")
        failed += 1
    if "location.reload" in heat:
        print("  ERROR: heatmap chart still reloads the page on resize")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: timeline chart uses the shared theme and no page reload")
    timeline = web.render_timeline_chart([{"name": "entity", "data": {"2025-06-01T10:00:00+00:00": "on", "2025-06-01T11:00:00+00:00": "off"}}], "timeline", 1)
    if "fontFamily: pbChartFont" not in timeline or "pbRegisterChart(chart, pbChartSize);" not in timeline:
        print("  ERROR: timeline chart is missing theme or resize registration")
        failed += 1
    if "location.reload" in timeline:
        print("  ERROR: timeline chart still reloads the page on resize")
        failed += 1

    # -------------------------------------------------------------------------
    failed += run_percent_unit_tests(web, now_str)

    if failed == 0:
        print("**** ✅ Web charts tests PASSED ****")
    else:
        print(f"**** ❌ Web charts tests FAILED ({failed} failure(s)) ****")
    return failed


def run_percent_unit_tests(web, now_str):
    """Unit tests for charting entities whose unit makes an invalid CSS/JS identifier (e.g. '%')."""
    failed = 0
    series_data = [{"name": "SoC", "data": {"2026-07-23T10:00:00+00:00": 45.0}, "chart_type": "line"}]

    # -------------------------------------------------------------------------
    print("Test: render_chart() targets a percent-unit tagname via getElementById, not a CSS id selector")
    html = web.render_chart(series_data, "%", "SoC Chart", now_str, tagname="chart_%")
    if "querySelector('#" in html or 'querySelector("#' in html:
        print("  ERROR: render_chart() still targets the chart element via a '#id' CSS selector, which throws for a tagname like 'chart_%'")
        failed += 1
    if "getElementById('chart_%')" not in html:
        print(f"  ERROR: expected render_chart() to call getElementById('chart_%'), got: {html}")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: render_timeline_chart() targets a percent-unit tagname via getElementById, not a CSS id selector")
    timeline_data = [{"name": "Status", "entity_id": "sensor.x", "data": {"2026-07-23T10:00:00+00:00": "on"}}]
    html = web.render_timeline_chart(timeline_data, "chart_%", 7)
    if "querySelector('#" in html or 'querySelector("#' in html:
        print("  ERROR: render_timeline_chart() still targets the chart element via a '#id' CSS selector, which throws for a tagname like 'chart_%'")
        failed += 1
    if "getElementById('chart_%')" not in html:
        print(f"  ERROR: expected render_timeline_chart() to call getElementById('chart_%'), got: {html}")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: render_heatmap_chart() targets a percent-unit chart_id via getElementById, not a CSS id selector")
    html = web.render_heatmap_chart([{"name": "SoC", "data": [{"x": "Mon", "y": 45.0}]}], "SoC Heatmap", 0, 100, chart_id="chart_%")
    if "querySelector('#" in html or 'querySelector("#' in html:
        print("  ERROR: render_heatmap_chart() still targets the chart element via a '#id' CSS selector, which throws for a chart_id like 'chart_%'")
        failed += 1
    if "getElementById('chart_%')" not in html:
        print(f"  ERROR: expected render_heatmap_chart() to call getElementById('chart_%'), got: {html}")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: render_heatmap_chart() sanitises chart_id before using it as a JS variable name")
    for bad_variable_name in ("height_chart_%", "chart_chart_%"):
        if bad_variable_name in html:
            print(f"  ERROR: render_heatmap_chart() interpolated an unsanitised chart_id into a JS identifier ('{bad_variable_name}'), which is a syntax error")
            failed += 1
    if "height_chart__" not in html:
        print(f"  ERROR: expected render_heatmap_chart() to declare a sanitised 'height_chart__' variable, got: {html}")
        failed += 1
    if "chart_chart__.render()" not in html:
        print(f"  ERROR: expected render_heatmap_chart() to render via a sanitised 'chart_chart__' variable, got: {html}")
        failed += 1

    return failed
