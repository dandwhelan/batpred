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
    if "fontFamily: pbChartFont" not in heat or "pbRegisterChart(chart, pbChartSize);" not in heat:
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

    if failed == 0:
        print("**** ✅ Web charts tests PASSED ****")
    else:
        print(f"**** ❌ Web charts tests FAILED ({failed} failure(s)) ****")
    return failed
