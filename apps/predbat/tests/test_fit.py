# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init


from tests.test_infra import reset_rates, reset_inverter, simple_scenario


def _set_fit(my_predbat, generation_rate=0.0, deemed_rate=0.0, deemed_pct=50.0):
    """Set FIT configuration on the predbat instance."""
    my_predbat.metric_fit_generation_rate = generation_rate
    my_predbat.metric_fit_deemed_export_rate = deemed_rate
    my_predbat.metric_fit_deemed_export_percentage = deemed_pct


def _check(name, value, expected, tolerance=0.5):
    """Compare a numeric value to its expected value within a tolerance."""
    if abs(value - expected) > tolerance:
        print("ERROR: {}: got {}, expected {} (tol {})".format(name, value, expected, tolerance))
        return True
    return False


def run_fit_tests(my_predbat):
    """Run FIT (Feed-in Tariff) calculator tests."""
    print("**** Running FIT tests ****")
    reset_inverter(my_predbat)
    import_rate = 10.0
    export_rate = 5.0
    reset_rates(my_predbat, import_rate, export_rate)

    failed = False

    # --- Test 1: FIT disabled -> no income tracked, export rate unaffected ---
    _set_fit(my_predbat, generation_rate=0.0, deemed_rate=0.0, deemed_pct=50.0)
    fail, pred = simple_scenario(
        "fit_disabled",
        my_predbat,
        load_amount=0,
        pv_amount=1,
        assert_final_metric=-export_rate * 24,  # exports 24kWh @ 5p
        assert_final_soc=0,
        with_battery=False,
        return_prediction_handle=True,
    )
    failed |= fail
    failed |= _check("fit_disabled.gen_income", pred.final_fit_generation_income, 0)
    failed |= _check("fit_disabled.deemed_income", pred.final_fit_deemed_export_income, 0)

    # --- Test 2: FIT generation only, no deemed export ---
    # Export rate must NOT be zeroed (user has metered export, e.g. SEG).
    # PV=1kW for 24h with no battery and no clipping: 24kWh forecast = 24kWh delivered.
    _set_fit(my_predbat, generation_rate=10.0, deemed_rate=0.0, deemed_pct=50.0)
    fail, pred = simple_scenario(
        "fit_generation_only",
        my_predbat,
        load_amount=0,
        pv_amount=1,
        # metric = -export(120) - generation(240) = -360p
        assert_final_metric=-export_rate * 24 - 10.0 * 24,
        assert_final_soc=0,
        with_battery=False,
        return_prediction_handle=True,
    )
    failed |= fail
    failed |= _check("fit_generation_only.gen_income", pred.final_fit_generation_income, 24 * 10.0)
    failed |= _check("fit_generation_only.deemed_income", pred.final_fit_deemed_export_income, 0)

    # --- Test 3: FIT deemed-only (legacy contracts where generation tariff isn't separate) ---
    # Export rate must be zeroed because deemed export pays regardless of actual exports.
    _set_fit(my_predbat, generation_rate=0.0, deemed_rate=4.0, deemed_pct=50.0)
    fail, pred = simple_scenario(
        "fit_deemed_only",
        my_predbat,
        load_amount=0,
        pv_amount=1,
        # exports earn 0 (zeroed) - deemed(24*0.5*4=48) = -48p
        assert_final_metric=-(24 * 0.5 * 4.0),
        assert_final_soc=0,
        with_battery=False,
        return_prediction_handle=True,
    )
    failed |= fail
    failed |= _check("fit_deemed_only.gen_income", pred.final_fit_generation_income, 0)
    failed |= _check("fit_deemed_only.deemed_income", pred.final_fit_deemed_export_income, 24 * 0.5 * 4.0)

    # --- Test 4: FIT generation + deemed export (typical UK FIT) ---
    # Export rate IS zeroed because deemed export is active.
    _set_fit(my_predbat, generation_rate=10.0, deemed_rate=5.0, deemed_pct=50.0)
    fail, pred = simple_scenario(
        "fit_full",
        my_predbat,
        load_amount=0,
        pv_amount=1,
        # exports 0, generation 240, deemed 60 -> metric = -300p
        assert_final_metric=-(24 * 10.0) - (24 * 0.5 * 5.0),
        assert_final_soc=0,
        with_battery=False,
        return_prediction_handle=True,
    )
    failed |= fail
    failed |= _check("fit_full.gen_income", pred.final_fit_generation_income, 24 * 10.0)
    failed |= _check("fit_full.deemed_income", pred.final_fit_deemed_export_income, 24 * 0.5 * 5.0)

    # --- Test 5: Clipping reduces FIT generation income ---
    # PV=3 kW, battery (default 100kWh), export_limit=0.5 kW.
    # Each hour: 1 kWh charges the battery, 0.5 kWh exports, 1.5 kWh is clipped.
    # 24h: 24 kWh battery + 12 kWh export = 36 kWh delivered. 36 kWh clipped.
    # Use deemed_pct=0 so export rate stays in play and we can keep the metric assertion simple.
    _set_fit(my_predbat, generation_rate=10.0, deemed_rate=0.0, deemed_pct=0.0)
    fail, pred = simple_scenario(
        "fit_clipping_generation",
        my_predbat,
        load_amount=0,
        pv_amount=3,
        # Standard metric for this scenario: -export_rate * 24 * 0.5 = -60p.
        # FIT generation on 36 kWh @ 10p = 360p. Total: -420p.
        assert_final_metric=-export_rate * 24 * 0.5 - 10.0 * 36,
        assert_final_soc=24,
        with_battery=True,
        export_limit=0.5,
        return_prediction_handle=True,
    )
    failed |= fail
    failed |= _check("fit_clipping_generation.gen_income", pred.final_fit_generation_income, 36 * 10.0, tolerance=2.0)
    failed |= _check("fit_clipping_generation.deemed_income", pred.final_fit_deemed_export_income, 0)

    # --- Test 6: Clipping reduces FIT deemed-export income proportionally ---
    # Same clip scenario as test 5: 36 kWh delivered out of 72 kWh forecast.
    # Export rate is zeroed by deemed-export logic. Deemed income = 36 * 0.5 * 4 = 72p.
    _set_fit(my_predbat, generation_rate=0.0, deemed_rate=4.0, deemed_pct=50.0)
    fail, pred = simple_scenario(
        "fit_clipping_deemed",
        my_predbat,
        load_amount=0,
        pv_amount=3,
        assert_final_metric=-(36 * 0.5 * 4.0),
        assert_final_soc=24,
        with_battery=True,
        export_limit=0.5,
        return_prediction_handle=True,
    )
    failed |= fail
    failed |= _check("fit_clipping_deemed.gen_income", pred.final_fit_generation_income, 0)
    failed |= _check("fit_clipping_deemed.deemed_income", pred.final_fit_deemed_export_income, 36 * 0.5 * 4.0, tolerance=2.0)

    # Restore default FIT config so subsequent tests are unaffected.
    _set_fit(my_predbat, generation_rate=0.0, deemed_rate=0.0, deemed_pct=50.0)

    if failed:
        print("**** FIT tests FAILED ****")
    else:
        print("**** FIT tests PASSED ****")
    return failed
