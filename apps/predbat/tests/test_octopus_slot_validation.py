# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
"""Validation of the Octopus car slot lists that automatic_config pads with blank entries."""

import copy


def run_octopus_slot_validation_tests(my_predbat):
    """
    The Octopus component wires the intelligent device to the car on the charger and pads the
    lists for the cars before it with "" - apps.yaml validation must accept those placeholders.
    """
    failed = False
    names = ("octopus_intelligent_slot", "octopus_ready_time", "octopus_charge_limit")

    def errors_for(values):
        """Validate the three slot lists set from values with 2 cars, returning their errors."""
        saved_args = copy.deepcopy(my_predbat.args)
        saved_states = my_predbat.ha_interface.dummy_items.copy()
        try:
            my_predbat.ha_interface.dummy_items.update({"binary_sensor.predbat_octopus_intelligent_dispatch": "off", "select.predbat_octopus_intelligent_target_time": "07:00", "number.predbat_octopus_intelligent_target_soc": 80})
            my_predbat.args["num_cars"] = 2
            for name in names:
                my_predbat.args[name] = values[name]
            my_predbat.validate_config()
            return {name: my_predbat.arg_errors.get(name) for name in names if my_predbat.arg_errors.get(name)}
        finally:
            my_predbat.args.clear()
            my_predbat.args.update(saved_args)
            my_predbat.ha_interface.dummy_items = saved_states

    print("Test 1: blank placeholder entries for the cars before the dispatch car validate")
    errors = errors_for({"octopus_intelligent_slot": ["", "binary_sensor.predbat_octopus_intelligent_dispatch"], "octopus_ready_time": ["", "select.predbat_octopus_intelligent_target_time"], "octopus_charge_limit": ["", "number.predbat_octopus_intelligent_target_soc"]})
    if errors:
        print("ERROR: blank placeholder entries were rejected: {}".format(errors))
        failed = True

    print("Test 2: a non-blank entry without a dot is still rejected")
    errors = errors_for({"octopus_intelligent_slot": ["bogus", "binary_sensor.predbat_octopus_intelligent_dispatch"], "octopus_ready_time": ["", ""], "octopus_charge_limit": ["", ""]})
    if "octopus_intelligent_slot" not in errors:
        print("ERROR: an invalid entity_id was accepted")
        failed = True

    return failed
