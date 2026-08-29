# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long

from web_helper import get_header_html, get_plan_renderer_js


def get_test_header():
    """Render the shared page header the way every web page does."""
    return get_header_html("Test", False, "./dash", {}, "v0.0.0", "<span></span>")


def run_web_mobile_tests(my_predbat):
    """
    Guard the mobile layout of the shared header and plan table.

    Upstream renders a 370x184 bat logo <img> inside .menu-bar with no size
    constraint of its own. Merged into this fork without its CSS it broke the
    compact single-row phone app bar, so the markup is removed here and these
    tests stop it coming back unnoticed on the next upstream merge.
    """
    failed = 0
    print("**** Running web mobile layout tests ****")

    header = get_test_header()

    # -------------------------------------------------------------------------
    print("Test: no unconstrained logo image in the menu bar")
    if "logo-image" in header or "flyBat" in header:
        print("  ERROR: header still renders the upstream menu-bar logo image or flyBat() handler")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: defensive CSS hides a reintroduced logo")
    if ".menu-bar .logo" not in header or ".flying-bat" not in header:
        print("  ERROR: header is missing the .menu-bar .logo / .flying-bat guard rule")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: viewport meta is served so phones do not render at desktop width")
    if 'name="viewport"' not in header:
        print("  ERROR: header is missing the viewport meta tag")
        failed += 1

    # -------------------------------------------------------------------------
    print("Test: wide plan columns are dropped on narrow screens")
    for selector in (".plan-car-rate", ".plan-debug-col"):
        if selector not in header:
            print("  ERROR: mobile CSS does not hide {}".format(selector))
            failed += 1

    # -------------------------------------------------------------------------
    print("Test: plan renderer tags the columns the mobile CSS hides")
    renderer = get_plan_renderer_js()
    if 'class="plan-car-rate"' not in renderer:
        print("  ERROR: plan renderer does not tag the car-rate cell with plan-car-rate")
        failed += 1
    if renderer.count('class="plan-debug-col"') < 3:
        print("  ERROR: plan renderer does not tag every debug-history cell with plan-debug-col")
        failed += 1

    return failed
