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
Tests across the whole inverter capability matrix, not just the GivEnergy default.

INVERTER_DEF describes 23 inverter profiles, and the Inverter class branches on the inv_* flags it
reads out of them, but every real Inverter built anywhere else in the suite takes the default type
of GE. These tests build one of every profile, and then drive the control paths that only the
non-GE profiles reach: the SoC-target emulation used by inverters with no target SoC register,
timed pause mode, the Solis energy-control switch, and the GivTCP v3 REST writers.
"""

import copy
import json

from config import INVERTER_DEF, SOLAX_SOLIS_MODES, SOLAX_SOLIS_MODES_NEW
from inverter import Inverter
from tests.test_inverter import DummyRestAPI, dummy_sleep

# Attribute on Inverter -> (key in INVERTER_DEF, default when the key is optional)
# has_timed_pause is deliberately absent: it is downgraded at construction when the inverter has no
# pause_mode entity, and is covered by its own test below.
PROFILE_FLAGS = {
    "inv_has_rest_api": ("has_rest_api", None),
    "inv_has_mqtt_api": ("has_mqtt_api", None),
    "inv_output_charge_control": ("output_charge_control", None),
    "inv_charge_control_immediate": ("charge_control_immediate", None),
    "inv_current_dp": ("current_dp", 1),
    "inv_has_charge_enable_time": ("has_charge_enable_time", None),
    "inv_has_discharge_enable_time": ("has_discharge_enable_time", None),
    "inv_has_target_soc": ("has_target_soc", None),
    "inv_has_reserve_soc": ("has_reserve_soc", None),
    "inv_charge_time_format": ("charge_time_format", None),
    "inv_charge_time_entity_is_option": ("charge_time_entity_is_option", None),
    "inv_clock_time_format": ("clock_time_format", None),
    "inv_soc_units": ("soc_units", None),
    "inv_time_button_press": ("time_button_press", None),
    "inv_support_charge_freeze": ("support_charge_freeze", None),
    "inv_support_discharge_freeze": ("support_discharge_freeze", None),
    "inv_has_ge_inverter_mode": ("has_ge_inverter_mode", None),
    "inv_has_ge_eco_toggle": ("has_ge_eco_toggle", False),
    "inv_num_load_entities": ("num_load_entities", None),
    "inv_write_and_poll_sleep": ("write_and_poll_sleep", None),
    "inv_has_idle_time": ("has_idle_time", None),
    "inv_can_span_midnight": ("can_span_midnight", None),
    "inv_charge_discharge_with_rate": ("charge_discharge_with_rate", False),
    "inv_target_soc_used_for_discharge": ("target_soc_used_for_discharge", True),
    "inv_has_fox_inverter_mode": ("has_fox_inverter_mode", False),
}

# Keys Inverter.__init__ reads with a bare subscript, so a profile missing one raises KeyError on
# any install that selects it - a failure only that owner would ever see.
REQUIRED_PROFILE_KEYS = sorted({key for key, default in PROFILE_FLAGS.values() if default is None} | {"name", "has_timed_pause"})


class Recorder:
    """Records the calls made to a patched-out inverter control method."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        """Record one call, returning True so callers that check a result carry on."""
        self.calls.append((args, kwargs))
        return True

    def args_only(self):
        """Return the positional arguments of each recorded call."""
        return [args for args, _ in self.calls]


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


def _build(my_predbat, inverter_type, **kwargs):
    """Build a real Inverter of the given type against the test fixture."""
    my_predbat.args["inverter_type"] = [inverter_type]
    return Inverter(my_predbat, 0, quiet=True, **kwargs)


def test_every_profile_builds_with_the_flags_it_declares(my_predbat):
    """
    Every INVERTER_DEF profile must construct, and land exactly the capability flags it declares.

    The flags are what the rest of inverter.py branches on, so a profile that declares the wrong
    one silently sends the wrong commands to that brand of hardware.
    """
    print("*** Running test: every inverter profile builds with the flags it declares")
    failed = 0

    for inverter_type in sorted(INVERTER_DEF.keys()):
        profile = INVERTER_DEF[inverter_type]

        missing = [key for key in REQUIRED_PROFILE_KEYS if key not in profile]
        if missing:
            print("ERROR: inverter profile {} is missing required key(s) {}".format(inverter_type, missing))
            failed = 1
            continue

        try:
            inverter = _build(my_predbat, inverter_type)
        except Exception as e:
            print("ERROR: inverter profile {} failed to construct: {}: {}".format(inverter_type, type(e).__name__, e))
            failed = 1
            continue

        failed |= _check("{}: inverter_type recorded".format(inverter_type), inverter.inverter_type, inverter_type)

        for attribute, (key, default) in PROFILE_FLAGS.items():
            expected = profile[key] if default is None else profile.get(key, default)
            failed |= _check("{}: {}".format(inverter_type, attribute), getattr(inverter, attribute), expected)

    return failed


def test_timed_pause_is_dropped_without_a_pause_entity(my_predbat):
    """
    A profile that claims timed pause loses it when the inverter has no pause_mode entity.

    Keeping the claim would have Predbat issue pause commands into nothing, so the downgrade at
    construction is what stops a half-configured install from silently doing nothing.
    """
    print("*** Running test: timed pause is dropped without a pause entity")
    failed = 0

    claims_pause = [name for name in sorted(INVERTER_DEF) if INVERTER_DEF[name].get("has_timed_pause")]
    failed |= _check_true("at least one profile claims timed pause", claims_pause)

    saved_arg = my_predbat.args.get("pause_mode", None)
    for inverter_type in claims_pause:
        # No entity configured at all
        my_predbat.args.pop("pause_mode", None)
        inverter = _build(my_predbat, inverter_type)
        failed |= _check("{}: pause dropped with no entity".format(inverter_type), inverter.inv_has_timed_pause, False)

        # Entity configured but the inverter never published a state for it
        my_predbat.args["pause_mode"] = "select.pause_mode_missing"
        my_predbat.ha_interface.dummy_items.pop("select.pause_mode_missing", None)
        inverter = _build(my_predbat, inverter_type)
        failed |= _check("{}: pause dropped with a dead entity".format(inverter_type), inverter.inv_has_timed_pause, False)

        # Entity configured and reporting a state: the capability is kept
        my_predbat.args["pause_mode"] = "select.pause_mode_live"
        my_predbat.ha_interface.dummy_items["select.pause_mode_live"] = "Disabled"
        inverter = _build(my_predbat, inverter_type)
        failed |= _check("{}: pause kept with a live entity".format(inverter_type), inverter.inv_has_timed_pause, True)

    if saved_arg is None:
        my_predbat.args.pop("pause_mode", None)
    else:
        my_predbat.args["pause_mode"] = saved_arg
    return failed


def _mimic(my_predbat, inverter, limit, discharge, charge_control):
    """Drive mimic_target_soc with the enable and current writers recorded rather than issued."""
    enables = Recorder()
    currents = Recorder()
    inverter.inv_output_charge_control = charge_control
    inverter.alt_charge_discharge_enable = enables
    inverter.set_current_from_power = currents
    inverter.mimic_target_soc(limit, discharge=discharge)
    return enables.args_only(), currents.args_only()


def test_mimic_target_soc_charging(my_predbat):
    """
    SoC-target emulation for charging: inverters with no target SoC register are steered by
    turning grid charge on and off around the target, so each side of the target must be right.
    """
    print("*** Running test: mimic_target_soc charging decisions")
    failed = 0
    inverter = _build(my_predbat, "GS")
    charge_power = my_predbat.get_arg("charge_rate", index=0, default=2600.0, required_unit="W")

    # A target of zero means no grid charging at all: back to ECO
    inverter.soc_percent = 50
    enables, currents = _mimic(my_predbat, inverter, 0, False, "power")
    failed |= _check("charge/zero target: eco enabled", enables, [("eco", True)])
    failed |= _check("charge/zero target: no current writes on power control", currents, [])

    inverter.soc_percent = 50
    enables, currents = _mimic(my_predbat, inverter, 0, False, "current")
    failed |= _check("charge/zero target: currents reset on current control", currents, [("charge", charge_power), ("discharge", my_predbat.get_arg("discharge_rate", index=0, default=2600.0, required_unit="W"))])

    # Above the target: stop charging, and hold at zero amps on current control
    inverter.soc_percent = 80
    enables, currents = _mimic(my_predbat, inverter, 50, False, "current")
    failed |= _check("charge/above target: charging disabled", enables, [("charge", False)])
    failed |= _check("charge/above target: current held at zero", currents, [("charge", 0)])

    # Exactly at the target: stay enabled but hold at zero amps
    inverter.soc_percent = 50
    enables, currents = _mimic(my_predbat, inverter, 50, False, "current")
    failed |= _check("charge/at target: charging stays enabled", enables, [("charge", True)])
    failed |= _check("charge/at target: current held at zero", currents, [("charge", 0)])

    # Below the target: charge at the configured rate
    inverter.soc_percent = 20
    enables, currents = _mimic(my_predbat, inverter, 50, False, "current")
    failed |= _check("charge/below target: charging enabled", enables, [("charge", True)])
    failed |= _check("charge/below target: current set to the charge rate", currents, [("charge", charge_power)])

    # On power control the same decisions are made without touching the current registers
    inverter.soc_percent = 20
    enables, currents = _mimic(my_predbat, inverter, 50, False, "power")
    failed |= _check("charge/below target on power control: charging enabled", enables, [("charge", True)])
    failed |= _check("charge/below target on power control: no current writes", currents, [])

    return failed


def test_mimic_target_soc_discharging(my_predbat):
    """SoC-target emulation for export: grid discharge is gated the other way around the target."""
    print("*** Running test: mimic_target_soc discharge decisions")
    failed = 0
    inverter = _build(my_predbat, "GS")
    discharge_power = my_predbat.get_arg("discharge_rate", index=0, default=2600.0, required_unit="W")

    # A target of 100% means export everything: ECO mode, at the full rate on current control
    inverter.soc_percent = 50
    enables, currents = _mimic(my_predbat, inverter, 100, True, "current")
    failed |= _check("discharge/100 target: eco enabled", enables, [("eco", True)])
    failed |= _check("discharge/100 target: discharge current written", currents, [("discharge", discharge_power)])

    # Below the target there is nothing to export
    inverter.soc_percent = 30
    enables, currents = _mimic(my_predbat, inverter, 50, True, "power")
    failed |= _check("discharge/below target: discharge disabled", enables, [("discharge", False)])

    # Exactly at the target: hold, which on current control means enabled at zero amps
    inverter.soc_percent = 50
    enables, currents = _mimic(my_predbat, inverter, 50, True, "current")
    failed |= _check("discharge/at target on current control: enabled at zero", enables, [("discharge", True)])
    failed |= _check("discharge/at target on current control: current zeroed", currents, [("discharge", 0)])

    inverter.soc_percent = 50
    enables, currents = _mimic(my_predbat, inverter, 50, True, "power")
    failed |= _check("discharge/at target on power control: disabled", enables, [("discharge", False)])

    # Above the target: export
    inverter.soc_percent = 80
    enables, currents = _mimic(my_predbat, inverter, 50, True, "current")
    failed |= _check("discharge/above target: discharge enabled", enables, [("discharge", True)])
    failed |= _check("discharge/above target: current set to the discharge rate", currents, [("discharge", discharge_power)])

    return failed


def test_solis_energy_control_switch(my_predbat):
    """
    Solis has one switch for both directions, so charge and export share a single mode write.

    The switch is only written when the mode actually changes; writing it every cycle would burn
    inverter register writes for nothing.
    """
    print("*** Running test: Solis energy control switch")
    failed = 0
    inverter = _build(my_predbat, "GS")

    saved_switch_arg = my_predbat.args.get("energy_control_switch", None)
    saved_modbus_new = my_predbat.args.get("solax_modbus_new", None)
    my_predbat.args["energy_control_switch"] = "select.solis_energy_control"

    for modbus_new, modes in ((True, SOLAX_SOLIS_MODES_NEW), (False, SOLAX_SOLIS_MODES)):
        my_predbat.args["solax_modbus_new"] = modbus_new
        by_value = {value: name for name, value in modes.items()}
        writes = Recorder()
        inverter.write_and_poll_option = writes
        inverter.mqtt_message = Recorder()

        # Sitting in the "no timed charge/discharge" mode (33), enabling charge moves it to 35
        my_predbat.ha_interface.dummy_items["select.solis_energy_control"] = by_value[33]
        inverter.alt_charge_discharge_enable("charge", True)
        failed |= _check("solis(modbus_new={}): enable charge writes mode 35".format(modbus_new), [kwargs.get("new_value") for _, kwargs in writes.calls], [by_value[35]])

        # Already in 35, disabling charge moves it back to 33
        writes = Recorder()
        inverter.write_and_poll_option = writes
        my_predbat.ha_interface.dummy_items["select.solis_energy_control"] = by_value[35]
        inverter.alt_charge_discharge_enable("charge", False)
        failed |= _check("solis(modbus_new={}): disable charge writes mode 33".format(modbus_new), [kwargs.get("new_value") for _, kwargs in writes.calls], [by_value[33]])

        # Already in the mode being asked for: no write at all
        writes = Recorder()
        inverter.write_and_poll_option = writes
        my_predbat.ha_interface.dummy_items["select.solis_energy_control"] = by_value[35]
        inverter.alt_charge_discharge_enable("discharge", True)
        failed |= _check("solis(modbus_new={}): unchanged mode is not rewritten".format(modbus_new), writes.calls, [])

        # ECO always means timed charge/discharge on
        writes = Recorder()
        inverter.write_and_poll_option = writes
        my_predbat.ha_interface.dummy_items["select.solis_energy_control"] = by_value[33]
        inverter.alt_charge_discharge_enable("eco", True)
        failed |= _check("solis(modbus_new={}): eco writes mode 35".format(modbus_new), [kwargs.get("new_value") for _, kwargs in writes.calls], [by_value[35]])

    if saved_switch_arg is None:
        my_predbat.args.pop("energy_control_switch", None)
    else:
        my_predbat.args["energy_control_switch"] = saved_switch_arg
    if saved_modbus_new is None:
        my_predbat.args.pop("solax_modbus_new", None)
    else:
        my_predbat.args["solax_modbus_new"] = saved_modbus_new
    return failed


def test_mqtt_charge_discharge_enable(my_predbat):
    """An MQTT inverter is steered by published messages rather than by entity writes."""
    print("*** Running test: MQTT charge/discharge enable")
    failed = 0
    inverter = _build(my_predbat, "SF")
    failed |= _check_true("SF speaks MQTT", inverter.inv_has_mqtt_api)

    messages = Recorder()
    inverter.mqtt_message = messages
    inverter.get_current_charge_rate = lambda: 2600
    inverter.get_current_discharge_rate = lambda: 2600

    inverter.alt_charge_discharge_enable("charge", True)
    failed |= _check("mqtt: charge publishes set/charge", [args[0] for args in messages.args_only()], ["set/charge"])

    messages = Recorder()
    inverter.mqtt_message = messages
    inverter.alt_charge_discharge_enable("discharge", True)
    failed |= _check("mqtt: discharge publishes set/discharge", [args[0] for args in messages.args_only()], ["set/discharge"])

    messages = Recorder()
    inverter.mqtt_message = messages
    inverter.alt_charge_discharge_enable("eco", True)
    failed |= _check("mqtt: eco publishes set/auto", [args[0] for args in messages.args_only()], ["set/auto"])

    # With the discharge rate already at zero, eco is modelled as a forced discharge at zero rate
    messages = Recorder()
    inverter.mqtt_message = messages
    inverter.get_current_discharge_rate = lambda: 0
    inverter.alt_charge_discharge_enable("eco", True)
    failed |= _check("mqtt: eco at zero rate publishes set/discharge", [args[0] for args in messages.args_only()], ["set/discharge"])

    return failed


def test_adjust_pause_mode_without_support_is_a_no_op(my_predbat):
    """An inverter without timed pause must not write anything when asked to pause."""
    print("*** Running test: pause mode is a no-op without support")
    failed = 0
    inverter = _build(my_predbat, "GS")
    inverter.inv_has_timed_pause = False
    writes = Recorder()
    inverter.write_and_poll_option = writes

    inverter.adjust_pause_mode(pause_charge=True, pause_discharge=True)
    failed |= _check("no pause support: nothing written", writes.calls, [])
    return failed


def test_adjust_pause_mode_over_entities(my_predbat):
    """
    Pause mode over plain entities writes the slot bounds and then the mode itself.

    The mode names differ between a GE Cloud inverter and a local one, chosen from whatever the
    inverter is currently reporting, so both vocabularies are checked.
    """
    print("*** Running test: pause mode over entities")
    failed = 0

    saved = {name: my_predbat.args.get(name, None) for name in ("pause_mode", "pause_start_time", "pause_end_time")}
    my_predbat.args["pause_mode"] = "select.pause_mode_live"
    my_predbat.args["pause_start_time"] = "select.pause_start"
    my_predbat.args["pause_end_time"] = "select.pause_end"
    my_predbat.ha_interface.dummy_items["select.pause_mode_live"] = "Disabled"
    my_predbat.ha_interface.dummy_items["select.pause_start"] = "01:00:00"
    my_predbat.ha_interface.dummy_items["select.pause_end"] = "02:00:00"

    inverter = _build(my_predbat, "GE")
    inverter.rest_data = None
    failed |= _check_true("pause: capability kept with a live entity", inverter.inv_has_timed_pause)

    writes = Recorder()
    inverter.write_and_poll_option = writes
    inverter.adjust_pause_mode(pause_charge=True, pause_discharge=True)
    written = {args[0]: args[2] for args in writes.args_only()}
    failed |= _check("pause: slot opened to the whole day", (written.get("pause_start_time"), written.get("pause_end_time")), ("00:00:00", "23:59:00"))
    failed |= _check("pause: local mode vocabulary", written.get("pause_mode"), "PauseBoth")

    # Charge only, and discharge only
    for pause_charge, pause_discharge, expected in ((True, False, "PauseCharge"), (False, True, "PauseDischarge"), (False, False, "Disabled")):
        my_predbat.ha_interface.dummy_items["select.pause_mode_live"] = "PauseBoth"
        writes = Recorder()
        inverter.write_and_poll_option = writes
        inverter.adjust_pause_mode(pause_charge=pause_charge, pause_discharge=pause_discharge)
        written = {args[0]: args[2] for args in writes.args_only()}
        failed |= _check("pause: charge={} discharge={}".format(pause_charge, pause_discharge), written.get("pause_mode"), expected)

    # A GE Cloud inverter reports the long-form names, and must be written back in the same words
    my_predbat.ha_interface.dummy_items["select.pause_mode_live"] = "Not Paused"
    writes = Recorder()
    inverter.write_and_poll_option = writes
    inverter.adjust_pause_mode(pause_charge=True, pause_discharge=True)
    written = {args[0]: args[2] for args in writes.args_only()}
    failed |= _check("pause: cloud mode vocabulary", written.get("pause_mode"), "Pause Charge & Discharge")

    # An unchanged mode is not rewritten
    my_predbat.ha_interface.dummy_items["select.pause_mode_live"] = "Disabled"
    my_predbat.ha_interface.dummy_items["select.pause_start"] = "00:00:00"
    my_predbat.ha_interface.dummy_items["select.pause_end"] = "23:59:00"
    writes = Recorder()
    inverter.write_and_poll_option = writes
    inverter.adjust_pause_mode(pause_charge=False, pause_discharge=False)
    failed |= _check("pause: nothing rewritten when already correct", writes.calls, [])

    for name, value in saved.items():
        if value is None:
            my_predbat.args.pop(name, None)
        else:
            my_predbat.args[name] = value
    return failed


def _rest_inverter(my_predbat, rest_data):
    """Build a GE inverter wired to a dummy GivTCP v3 REST endpoint carrying rest_data."""
    dummy_rest = DummyRestAPI()
    dummy_rest.rest_data = copy.deepcopy(rest_data)
    saved_rest_arg = my_predbat.args.get("givtcp_rest", None)
    my_predbat.args["givtcp_rest"] = "dummy"
    inverter = _build(my_predbat, "GE", rest_postCommand=dummy_rest.dummy_rest_postCommand, rest_getData=dummy_rest.dummy_rest_getData)
    inverter.sleep = dummy_sleep
    inverter.rest_v3 = True
    return inverter, dummy_rest, saved_rest_arg


def _restore_rest_arg(my_predbat, saved_rest_arg):
    """Put the givtcp_rest argument back the way the fixture had it."""
    if saved_rest_arg is None:
        my_predbat.args.pop("givtcp_rest", None)
    else:
        my_predbat.args["givtcp_rest"] = saved_rest_arg


def test_rest_pause_and_discharge_schedule_writers(my_predbat):
    """
    The GivTCP v3 REST writers post their command, re-read, and report whether the write stuck.

    A write that never lands has to come back False and be recorded as an error - reporting success
    would leave Predbat believing the inverter is paused when it is still cycling the battery.
    """
    print("*** Running test: REST pause and discharge-schedule writers")
    failed = 0

    with open("cases/rest_v3.json", "r") as handle:
        rest_v3 = json.load(handle)

    # --- setBatteryPauseMode ---
    inverter, dummy_rest, saved_rest_arg = _rest_inverter(my_predbat, rest_v3)
    accepted = copy.deepcopy(rest_v3)
    accepted["Control"]["Battery_pause_mode"] = "PauseBoth"
    dummy_rest.queue_rest_data(accepted)
    dummy_rest.get_commands()

    result = inverter.rest_setBatteryPauseMode("PauseBoth")
    commands = dummy_rest.get_commands()
    failed |= _check("rest pause mode: reported success", result, True)
    failed |= _check("rest pause mode: one command posted", len(commands), 1)
    if commands:
        failed |= _check_true("rest pause mode: correct endpoint", commands[0][0].endswith("/setBatteryPauseMode"))
        failed |= _check("rest pause mode: payload", commands[0][1], {"state": "PauseBoth"})

    # The inverter never takes the setting: the writer must give up and report failure
    dummy_rest.clear_queue()
    dummy_rest.rest_data = copy.deepcopy(rest_v3)
    dummy_rest.get_commands()
    result = inverter.rest_setBatteryPauseMode("PauseBoth")
    failed |= _check("rest pause mode: reported failure when it never sticks", result, False)
    failed |= _check_true("rest pause mode: retried more than once", len(dummy_rest.get_commands()) > 1)

    # --- setPauseSlot ---
    accepted = copy.deepcopy(rest_v3)
    accepted["Timeslots"]["Battery_pause_start_time_slot"] = "00:00:00"
    accepted["Timeslots"]["Battery_pause_end_time_slot"] = "23:59:00"
    dummy_rest.clear_queue()
    dummy_rest.queue_rest_data(accepted)
    dummy_rest.get_commands()

    result = inverter.rest_setPauseSlot("00:00:00", "23:59:00")
    commands = dummy_rest.get_commands()
    failed |= _check("rest pause slot: reported success", result, True)
    if commands:
        failed |= _check_true("rest pause slot: correct endpoint", commands[0][0].endswith("/setPauseSlot"))
        # GivTCP wants HH:MM, so the seconds are trimmed off before posting
        failed |= _check("rest pause slot: payload trimmed to HH:MM", commands[0][1], {"start": "00:00", "finish": "23:59"})

    # --- enableDischargeSchedule ---
    for state, published in ((True, "enable"), (False, "disable")):
        accepted = copy.deepcopy(rest_v3)
        accepted["Control"]["Enable_Discharge_Schedule"] = published
        dummy_rest.clear_queue()
        dummy_rest.queue_rest_data(accepted)
        dummy_rest.get_commands()

        result = inverter.rest_enableDischargeSchedule(state)
        commands = dummy_rest.get_commands()
        failed |= _check("rest discharge schedule {}: reported success".format(state), result, True)
        if commands:
            failed |= _check_true("rest discharge schedule {}: correct endpoint".format(state), commands[0][0].endswith("/enableDischargeSchedule"))
            failed |= _check("rest discharge schedule {}: payload".format(state), commands[0][1], {"state": published})

    # A boolean reply is accepted just as a worded one is
    accepted = copy.deepcopy(rest_v3)
    accepted["Control"]["Enable_Discharge_Schedule"] = True
    dummy_rest.clear_queue()
    dummy_rest.queue_rest_data(accepted)
    failed |= _check("rest discharge schedule: boolean reply accepted", inverter.rest_enableDischargeSchedule(True), True)

    # Neither of the remaining writers may claim success when the setting never lands
    refused = copy.deepcopy(rest_v3)
    refused["Control"]["Enable_Discharge_Schedule"] = "disable"
    dummy_rest.clear_queue()
    dummy_rest.rest_data = refused
    dummy_rest.get_commands()
    failed |= _check("rest discharge schedule: failure reported", inverter.rest_enableDischargeSchedule(True), False)
    failed |= _check_true("rest discharge schedule: retried more than once", len(dummy_rest.get_commands()) > 1)

    refused = copy.deepcopy(rest_v3)
    refused["Timeslots"]["Battery_pause_start_time_slot"] = "05:00:00"
    refused["Timeslots"]["Battery_pause_end_time_slot"] = "06:00:00"
    dummy_rest.clear_queue()
    dummy_rest.rest_data = refused
    dummy_rest.get_commands()
    failed |= _check("rest pause slot: failure reported", inverter.rest_setPauseSlot("00:00:00", "23:59:00"), False)
    failed |= _check_true("rest pause slot: retried more than once", len(dummy_rest.get_commands()) > 1)

    _restore_rest_arg(my_predbat, saved_rest_arg)
    return failed


def test_adjust_pause_mode_over_rest(my_predbat):
    """On GivTCP v3 the pause slot and mode go over REST rather than through entities."""
    print("*** Running test: pause mode over REST")
    failed = 0

    with open("cases/rest_v3.json", "r") as handle:
        rest_v3 = json.load(handle)

    saved = {name: my_predbat.args.get(name, None) for name in ("pause_mode", "pause_start_time", "pause_end_time")}
    my_predbat.args["pause_mode"] = "select.pause_mode_live"
    my_predbat.args["pause_start_time"] = "select.pause_start"
    my_predbat.args["pause_end_time"] = "select.pause_end"
    my_predbat.ha_interface.dummy_items["select.pause_mode_live"] = "Disabled"

    inverter, dummy_rest, saved_rest_arg = _rest_inverter(my_predbat, rest_v3)
    inverter.rest_data = copy.deepcopy(rest_v3)
    inverter.rest_data["Control"]["Battery_pause_mode"] = "Disabled"
    inverter.rest_data["Timeslots"]["Battery_pause_start_time_slot"] = "01:00:00"
    inverter.rest_data["Timeslots"]["Battery_pause_end_time_slot"] = "02:00:00"

    slot_writes = Recorder()
    mode_writes = Recorder()
    entity_writes = Recorder()
    inverter.rest_setPauseSlot = slot_writes
    inverter.rest_setBatteryPauseMode = mode_writes
    inverter.write_and_poll_option = entity_writes

    inverter.adjust_pause_mode(pause_charge=True, pause_discharge=False)

    failed |= _check("rest pause: slot written over REST", slot_writes.args_only(), [("00:00:00", "23:59:00")])
    failed |= _check("rest pause: mode written over REST", mode_writes.args_only(), [("PauseCharge",)])
    failed |= _check("rest pause: no entity writes", entity_writes.calls, [])

    _restore_rest_arg(my_predbat, saved_rest_arg)
    for name, value in saved.items():
        if value is None:
            my_predbat.args.pop(name, None)
        else:
            my_predbat.args[name] = value
    return failed


def run_inverter_matrix_tests(my_predbat):
    """Run the inverter capability matrix tests, returning non-zero if any failed."""
    print("**** Running inverter capability matrix tests ****")
    failed = 0

    saved_args = copy.deepcopy(my_predbat.args)
    saved_inverter_def = copy.deepcopy(INVERTER_DEF)

    try:
        for test in (
            test_every_profile_builds_with_the_flags_it_declares,
            test_timed_pause_is_dropped_without_a_pause_entity,
            test_mimic_target_soc_charging,
            test_mimic_target_soc_discharging,
            test_solis_energy_control_switch,
            test_mqtt_charge_discharge_enable,
            test_adjust_pause_mode_without_support_is_a_no_op,
            test_adjust_pause_mode_over_entities,
            test_rest_pause_and_discharge_schedule_writers,
            test_adjust_pause_mode_over_rest,
        ):
            failed |= test(my_predbat)
    finally:
        # Inverter.__init__ writes user overrides straight into INVERTER_DEF, so restore it as well
        # as the args - leaving either edited would follow the shared fixture into later tests
        my_predbat.args.clear()
        my_predbat.args.update(saved_args)
        INVERTER_DEF.clear()
        INVERTER_DEF.update(saved_inverter_def)

    return failed
