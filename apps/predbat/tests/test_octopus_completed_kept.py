# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init

from datetime import timedelta
from const import TIME_FORMAT


def run_octopus_completed_kept_tests(my_predbat):
    """
    Completed Intelligent dispatches go on pricing the import they covered once no dispatch sensor is wired.

    Suspending the only Intelligent device clears its dispatch sensor from the car slots, and today's cost is rebuilt every cycle from the
    import rates, so the dispatches it had completed dropped out and a whole daytime dispatch was re-billed at the peak
    rate (23.7 kWh at 39p rather than 7.9p).

    Tests:
    - Test 1: While a car is wired nothing is kept, and dispatches older than the start of yesterday are forgotten
    - Test 2: With no car wired, the remembered dispatches since yesterday are kept for their car
    - Test 3: The kept dispatches price their minutes at the low rate, and leave other minutes alone
    - Test 4: Nothing remembered keeps nothing, and the kept list has an entry per car
    """
    print("**** Running Octopus completed-dispatch keep tests ****")
    failed = False
    saved = {name: getattr(my_predbat, name) for name in ("octopus_completed_seen", "octopus_completed_kept", "octopus_completed_kept_logged", "octopus_slots", "io_adjusted", "rate_min_base", "forecast_minutes")}
    saved_args = {key: my_predbat.args[key] for key in ("octopus_slot_low_rate", "octopus_slot_max") if key in my_predbat.args}
    midnight = my_predbat.midnight_utc

    def dispatch(start_minutes, end_minutes, kwh):
        return {"start": (midnight + timedelta(minutes=start_minutes)).strftime(TIME_FORMAT), "end": (midnight + timedelta(minutes=end_minutes)).strftime(TIME_FORMAT), "charge_in_kwh": kwh, "source": "smart-charge", "location": "AT_HOME"}

    today = dispatch(12 * 60 + 30, 15 * 60, 23.7)
    yesterday = dispatch(-10 * 60, -9 * 60, 7.0)
    old = dispatch(-3 * 24 * 60, -3 * 24 * 60 + 30, 3.5)

    try:
        my_predbat.octopus_completed_seen = {}
        my_predbat.octopus_completed_kept_logged = 0
        my_predbat.octopus_completed_remember(0, [today, yesterday, old, "not a dispatch", {"start": today["start"]}])

        print("*** Test 1: nothing kept while a car is wired, and old dispatches forgotten ***")
        kept = my_predbat.octopus_completed_kept_slots([0])
        if any(kept):
            print("ERROR: Expected nothing kept while a dispatch sensor is wired, got {}".format(kept))
            failed = True
        if sorted(my_predbat.octopus_completed_seen.get(0, {})) != sorted([today["start"], yesterday["start"]]):
            print("ERROR: Expected only today's and yesterday's dispatches remembered, got {}".format(sorted(my_predbat.octopus_completed_seen.get(0, {}))))
            failed = True

        print("*** Test 2: with no car wired the remembered dispatches are kept for their car ***")
        kept = my_predbat.octopus_completed_kept_slots([])
        if len(kept) != my_predbat.num_cars or kept[0] != [yesterday, today]:
            print("ERROR: Expected car 0 to keep yesterday's and today's dispatches, got {}".format(kept))
            failed = True

        print("*** Test 3: kept dispatches price their minutes at the low rate ***")
        my_predbat.args["octopus_slot_low_rate"] = True
        my_predbat.args.pop("octopus_slot_max", None)
        my_predbat.octopus_slots = [[] for _ in range(my_predbat.num_cars)]
        my_predbat.io_adjusted = {}
        my_predbat.rate_min_base = 7.9
        rates = {minute: 39.05 for minute in range(-48 * 60, max(my_predbat.forecast_minutes, 2 * 24 * 60))}
        rates = my_predbat.rate_add_io_slots(0, rates, my_predbat.octopus_slots[0] + kept[0])
        if rates[13 * 60] != 7.9 or rates[14 * 60 + 45] != 7.9:
            print("ERROR: Expected the 12:30-15:00 dispatch priced at 7.9p, got {} and {}".format(rates[13 * 60], rates[14 * 60 + 45]))
            failed = True
        if rates[11 * 60] != 39.05 or rates[16 * 60] != 39.05:
            print("ERROR: Expected minutes outside the dispatch left at 39.05p, got {} and {}".format(rates[11 * 60], rates[16 * 60]))
            failed = True

        print("*** Test 4: nothing remembered keeps nothing ***")
        my_predbat.octopus_completed_seen = {}
        kept = my_predbat.octopus_completed_kept_slots([])
        if kept != [[] for _ in range(my_predbat.num_cars)]:
            print("ERROR: Expected an empty kept list per car, got {}".format(kept))
            failed = True
    finally:
        for name, value in saved.items():
            setattr(my_predbat, name, value)
        for key in ("octopus_slot_low_rate", "octopus_slot_max"):
            if key in saved_args:
                my_predbat.args[key] = saved_args[key]
            else:
                my_predbat.args.pop(key, None)

    if failed:
        print("**** Octopus completed-dispatch keep tests: FAILED ****")
    else:
        print("**** Octopus completed-dispatch keep tests: PASSED ****")
    return failed
