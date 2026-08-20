# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init

"""
Tests for the car_charging_soc staleness guard (fetch.py).

A plugged-in car whose SoC sensor freezes reports the value it last reached, which is
typically just under the charge limit. Predbat then plans ~0 kWh of car charging, the real
load drops out of the plan, and the car-charging battery hold never fires because it needs a
slot with kwh > 0.

The guard only fires for a car that car_charging_planned reports as plugged in, and only when
car_charging_soc_max_age is set - an unplugged car that has gone to sleep stops reporting for
days and that is not a fault.
"""

SOC_SENSOR = "sensor.test_car_soc"
FRESH_PERCENT = 89.9


# Everything _setup() writes to, so a test cannot leak car state into the rest of the suite
_MUTATED = ("num_cars", "car_charging_planned", "car_charging_soc_stale", "car_charging_manual_soc")


def _save(my_predbat):
    """Snapshot the attributes and dummy state these tests overwrite."""
    saved = {name: getattr(my_predbat, name, None) for name in _MUTATED}
    saved["_args"] = my_predbat.args.copy()
    saved["_items"] = my_predbat.ha_interface.dummy_items.copy()
    return saved


def _restore(my_predbat, saved):
    """Put everything back, including the lists sized to the real num_cars."""
    for name in _MUTATED:
        setattr(my_predbat, name, saved[name])
    my_predbat.args = saved["_args"]
    my_predbat.ha_interface.dummy_items = saved["_items"]


def _setup(my_predbat, age_minutes, planned, last_updated="stamp"):
    """Point car 0's SoC at a dummy sensor whose last_updated is age_minutes old."""
    my_predbat.num_cars = 1
    my_predbat.car_charging_planned = [planned]
    my_predbat.car_charging_soc_stale = [False]
    my_predbat.car_charging_manual_soc = [False]
    my_predbat.args["car_charging_soc"] = [SOC_SENSOR]

    record = {"state": FRESH_PERCENT}
    if last_updated == "stamp":
        from datetime import timedelta

        record["last_updated"] = (my_predbat.now_utc - timedelta(minutes=age_minutes)).isoformat()
    elif last_updated is not None:
        record["last_updated"] = last_updated
    my_predbat.ha_interface.dummy_items[SOC_SENSOR] = record


def _check(name, got_percent, got_stale, expect_percent, expect_stale):
    failed = False
    if abs(got_percent - expect_percent) > 0.001:
        print("ERROR: {}: expected SoC {}% got {}%".format(name, expect_percent, got_percent))
        failed = True
    if got_stale != expect_stale:
        print("ERROR: {}: expected stale={} got {}".format(name, expect_stale, got_stale))
        failed = True
    return failed


def test_car_soc_stale(my_predbat):
    """The guard fires only for a plugged-in car with a genuinely frozen sensor."""
    print("**** test_car_soc_stale ****")
    failed = False

    saved = _save(my_predbat)
    try:
        # A recent reading is used as-is
        _setup(my_predbat, age_minutes=10, planned=True)
        failed |= _check("fresh sensor", my_predbat.car_charging_soc_read(0, 240), my_predbat.car_charging_soc_stale[0], FRESH_PERCENT, False)

        # Frozen for 20 hours while plugged in - do not trust it, assume a charge is needed
        _setup(my_predbat, age_minutes=1200, planned=True)
        failed |= _check("stale sensor, plugged in", my_predbat.car_charging_soc_read(0, 240), my_predbat.car_charging_soc_stale[0], 0.0, True)

        # Exactly at the limit is not yet stale (the check is strictly greater than)
        _setup(my_predbat, age_minutes=240, planned=True)
        failed |= _check("age equal to limit", my_predbat.car_charging_soc_read(0, 240), my_predbat.car_charging_soc_stale[0], FRESH_PERCENT, False)

        # max_age 0 is the default and disables the feature entirely - upstream behaviour
        _setup(my_predbat, age_minutes=1200, planned=True)
        failed |= _check("feature disabled (max_age 0)", my_predbat.car_charging_soc_read(0, 0), my_predbat.car_charging_soc_stale[0], FRESH_PERCENT, False)

        # An unplugged car legitimately sleeps for days
        _setup(my_predbat, age_minutes=4320, planned=False)
        failed |= _check("stale sensor, unplugged", my_predbat.car_charging_soc_read(0, 240), my_predbat.car_charging_soc_stale[0], FRESH_PERCENT, False)

        # No last_updated at all - age is unknowable, so do not second-guess the reading
        _setup(my_predbat, age_minutes=0, planned=True, last_updated=None)
        failed |= _check("no last_updated", my_predbat.car_charging_soc_read(0, 240), my_predbat.car_charging_soc_stale[0], FRESH_PERCENT, False)

        # Unparseable timestamp is treated the same way
        _setup(my_predbat, age_minutes=0, planned=True, last_updated="not-a-timestamp")
        failed |= _check("unparseable last_updated", my_predbat.car_charging_soc_read(0, 240), my_predbat.car_charging_soc_stale[0], FRESH_PERCENT, False)

        # A naive timestamp has no UTC offset, which str2time rejects - must not raise
        _setup(my_predbat, age_minutes=0, planned=True, last_updated="2026-08-18 03:15:00")
        failed |= _check("naive last_updated", my_predbat.car_charging_soc_read(0, 240), my_predbat.car_charging_soc_stale[0], FRESH_PERCENT, False)
    finally:
        _restore(my_predbat, saved)

    return failed


def test_car_soc_age(my_predbat):
    """car_charging_soc_age_minutes reports a real age and None when it cannot tell."""
    print("**** test_car_soc_age ****")
    failed = False

    saved = _save(my_predbat)
    try:
        _setup(my_predbat, age_minutes=90, planned=True)
        age = my_predbat.car_charging_soc_age_minutes(0)
        if age is None or abs(age - 90) > 1:
            print("ERROR: expected an age of about 90 minutes, got {}".format(age))
            failed = True

        # An attribute-qualified entity ages as its parent entity
        my_predbat.args["car_charging_soc"] = [SOC_SENSOR + "$battery_level"]
        age = my_predbat.car_charging_soc_age_minutes(0)
        if age is None or abs(age - 90) > 1:
            print("ERROR: expected attribute-qualified entity to age as its parent, got {}".format(age))
            failed = True

        # An entity that does not exist has no age
        my_predbat.args["car_charging_soc"] = ["sensor.does_not_exist"]
        age = my_predbat.car_charging_soc_age_minutes(0)
        if age is not None:
            print("ERROR: expected None for a missing entity, got {}".format(age))
            failed = True

        # A plain numeric config value is not a sensor and has no age
        my_predbat.args["car_charging_soc"] = [50.0]
        age = my_predbat.car_charging_soc_age_minutes(0)
        if age is not None:
            print("ERROR: expected None for a non-sensor value, got {}".format(age))
            failed = True
    finally:
        _restore(my_predbat, saved)

    return failed
