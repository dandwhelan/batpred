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
Tests for update_pred(), the five-minute cycle that drives everything else.

Every stage inside update_pred is covered by its own test elsewhere; what was never covered is the
wiring between them - the order the stages run in, the early returns that abandon a cycle, and the
decisions about whether to recompute the plan or re-use the saved one. These tests replace each
heavy stage with a recorder so that control flow, and only control flow, is under test.
"""

import os
import tempfile
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from predbat import COMPONENT_ERROR_RESTART_MINUTES, WATCHDOG_MAX_RESTARTS


class RecordingPluginSystem:
    """Plugin system stand-in that records the hooks update_pred fires."""

    def __init__(self):
        self.hooks = []

    def call_hooks(self, hook_name, **kwargs):
        """Record one hook call and the payload it carried."""
        self.hooks.append((hook_name, kwargs))

    def hook_names(self):
        """Return the names of the hooks fired, in order."""
        return [name for name, _ in self.hooks]


class RecordingComparison:
    """Compare stand-in that records which of its two entry points update_pred chose."""

    def __init__(self):
        self.calls = []

    def run_all(self):
        """Record a full comparison run."""
        self.calls.append("run_all")

    def publish_only(self):
        """Record a publish-only refresh."""
        self.calls.append("publish_only")


class Stages:
    """
    Recorder for the stages update_pred calls, with per-stage results the caller can steer.

    Holds the call log the assertions read, so a test can check both that a stage ran and where it
    ran relative to the others.
    """

    def __init__(self, my_predbat=None, sensor_force_replan=False, inverter_ok=True, dynamic_load=False, execute_status=("Ok", "detail")):
        self.calls = []
        self.my_predbat = my_predbat
        self.sensor_force_replan = sensor_force_replan
        self.inverter_ok = inverter_ok
        self.dynamic_load_result = dynamic_load
        self.execute_status = execute_status
        self.calculate_plan_args = []
        self.inverter_fetches = 0

    def _note(self, name):
        """Append a stage name to the call log."""
        self.calls.append(name)

    def update_time(self):
        """Stand in for the clock update so a test can pin minutes_now."""
        self._note("update_time")

    def download_predbat_releases(self):
        """Record the version check."""
        self._note("download_predbat_releases")

    def fetch_config_options(self):
        """Record the config reload."""
        self._note("fetch_config_options")

    def fetch_sensor_data(self):
        """Record the sensor fetch and report whether it forces a replan."""
        self._note("fetch_sensor_data")
        return self.sensor_force_replan

    def fetch_inverter_data(self):
        """Record an inverter read and report whether it succeeded."""
        self._note("fetch_inverter_data")
        self.inverter_fetches += 1
        if isinstance(self.inverter_ok, list):
            return self.inverter_ok[min(self.inverter_fetches - 1, len(self.inverter_ok) - 1)]
        return self.inverter_ok

    def dynamic_load(self):
        """Record the dynamic load check and report whether it changed anything."""
        self._note("dynamic_load")
        return self.dynamic_load_result

    def calculate_plan(self, recompute=True):
        """Record a plan calculation, echoing back whether it recomputed and marking the plan valid."""
        self._note("calculate_plan(recompute={})".format(recompute))
        self.calculate_plan_args.append(recompute)
        if recompute and self.my_predbat is not None:
            # A real recompute leaves a valid plan behind, which is what gates save_plan
            self.my_predbat.plan_valid = True
        return recompute

    def save_plan(self):
        """Record the plan being persisted."""
        self._note("save_plan")

    def publish_rate_and_threshold(self):
        """Record rate publication."""
        self._note("publish_rate_and_threshold")

    def execute_plan(self):
        """Record plan execution and return the configured status pair."""
        self._note("execute_plan")
        return self.execute_status

    def record_final_run_status(self, status, status_extra):
        """Record the final status write."""
        self._note("record_final_run_status")
        self.final_status = (status, status_extra)

    def save_current_config(self):
        """Record a settings save."""
        self._note("save_current_config")

    def emit_snapshot_metrics(self):
        """Record the metrics snapshot."""
        self._note("_emit_snapshot_metrics")

    def create_debug_yaml(self):
        """Record the debug dump."""
        self._note("create_debug_yaml")

    def index(self, name):
        """Return the position of a stage in the call log, or -1 when it never ran."""
        return self.calls.index(name) if name in self.calls else -1

    def count(self, name):
        """Return how many times a stage ran."""
        return self.calls.count(name)


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


def _drive(my_predbat, stages, scheduled=True, previous_values=None):
    """
    Run update_pred with every heavy stage replaced by the recorder, and restore afterwards.

    load_previous_value_from_ha is stubbed from previous_values so the register-write and savings
    counters are arithmetic on known inputs rather than on whatever the shared fixture is carrying.
    Keys are (entity, attribute) pairs; anything not listed reads back as 0.
    """
    previous_values = previous_values or {}

    def _previous(entity, attribute=None):
        """Read a stubbed previous value for an entity, defaulting to zero."""
        return previous_values.get((entity, attribute), 0)

    with ExitStack() as stack:
        for attribute, replacement in (
            ("update_time", stages.update_time),
            ("download_predbat_releases", stages.download_predbat_releases),
            ("fetch_config_options", stages.fetch_config_options),
            ("fetch_sensor_data", stages.fetch_sensor_data),
            ("fetch_inverter_data", stages.fetch_inverter_data),
            ("dynamic_load", stages.dynamic_load),
            ("calculate_plan", stages.calculate_plan),
            ("save_plan", stages.save_plan),
            ("publish_rate_and_threshold", stages.publish_rate_and_threshold),
            ("execute_plan", stages.execute_plan),
            ("record_final_run_status", stages.record_final_run_status),
            ("save_current_config", stages.save_current_config),
            ("_emit_snapshot_metrics", stages.emit_snapshot_metrics),
            ("create_debug_yaml", stages.create_debug_yaml),
        ):
            stack.enter_context(patch.object(my_predbat, attribute, replacement))
        stack.enter_context(patch.object(my_predbat, "load_previous_value_from_ha", _previous))
        my_predbat.update_pred(scheduled=scheduled)


def _reset_for_update_pred(my_predbat):
    """Put the shared instance into a known state for a cycle: valid rates, a fresh plan, no extras."""
    my_predbat.args.pop("template", None)
    my_predbat.args["user_id"] = None
    my_predbat.rate_min = 5.0
    my_predbat.rate_max = 30.0
    my_predbat.plan_valid = True
    my_predbat.plan_last_updated = my_predbat.now_utc
    my_predbat.calculate_plan_every = 30
    my_predbat.iboost_enable = False
    my_predbat.calculate_savings = False
    my_predbat.debug_enable = False
    my_predbat.holiday_days_left = 0
    my_predbat.set_read_only = False
    my_predbat.count_inverter_writes = {}
    my_predbat.comparison = None
    my_predbat.plugin_system = None
    my_predbat.expose_config("active", False)


def test_template_config_abandons_the_cycle(my_predbat):
    """An unedited apps.yaml template must stop the cycle before any data is fetched."""
    print("*** Running test: template configuration abandons the cycle")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.args["template"] = True
    stages = Stages(my_predbat)

    _drive(my_predbat, stages)

    failed |= _check("template: fetch_sensor_data calls", stages.count("fetch_sensor_data"), 0)
    failed |= _check("template: calculate_plan calls", stages.count("calculate_plan(recompute=True)"), 0)
    failed |= _check_true("template: status mentions the template", "Template" in my_predbat.current_status)
    failed |= _check("template: active flag left on", my_predbat.get_arg("active", False), False)

    my_predbat.args.pop("template", None)
    return failed


def test_failed_inverter_read_abandons_the_cycle(my_predbat):
    """A failed inverter read must abandon the cycle rather than plan against missing state."""
    print("*** Running test: failed inverter read abandons the cycle")
    failed = 0
    _reset_for_update_pred(my_predbat)
    stages = Stages(my_predbat, inverter_ok=False)

    _drive(my_predbat, stages)

    failed |= _check("inverter fail: fetch_sensor_data ran", stages.count("fetch_sensor_data"), 1)
    failed |= _check("inverter fail: no plan calculated", stages.count("calculate_plan(recompute=True)"), 0)
    failed |= _check("inverter fail: no plan executed", stages.count("execute_plan"), 0)
    failed |= _check_true("inverter fail: status reports it", "inverter data" in my_predbat.current_status)
    return failed


def test_all_zero_rates_abandon_the_cycle(my_predbat):
    """Import rates that are all zero mean no usable tariff, so the cycle must stop."""
    print("*** Running test: all-zero import rates abandon the cycle")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.rate_min = 0
    my_predbat.rate_max = 0
    stages = Stages(my_predbat)

    _drive(my_predbat, stages)

    failed |= _check("zero rates: inverter read ran", stages.count("fetch_inverter_data"), 1)
    failed |= _check("zero rates: no plan executed", stages.count("execute_plan"), 0)
    failed |= _check_true("zero rates: status reports it", "Import rates are all zero" in my_predbat.current_status)

    my_predbat.rate_min = 5.0
    my_predbat.rate_max = 30.0
    return failed


def test_stage_order_on_a_recompute(my_predbat):
    """A recomputing cycle runs its stages in order and re-reads the inverter before executing."""
    print("*** Running test: stage order on a recomputing cycle")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = False
    stages = Stages(my_predbat)

    _drive(my_predbat, stages)

    order = ["fetch_config_options", "fetch_sensor_data", "fetch_inverter_data", "calculate_plan(recompute=True)", "publish_rate_and_threshold", "execute_plan", "record_final_run_status"]
    positions = [stages.index(name) for name in order]
    failed |= _check_true("recompute: every stage ran ({})".format(stages.calls), all(position >= 0 for position in positions))
    failed |= _check_true("recompute: stages in order ({})".format(stages.calls), positions == sorted(positions))

    # Time passes while planning, so the inverter is read again before the plan is executed
    failed |= _check("recompute: inverter read twice", stages.count("fetch_inverter_data"), 2)
    failed |= _check_true("recompute: second read precedes execute", stages.calls.index("execute_plan") > len(stages.calls) - 1 - stages.calls[::-1].index("fetch_inverter_data"))
    failed |= _check("recompute: plan saved once", stages.count("save_plan"), 1)
    failed |= _check("recompute: active flag cleared", my_predbat.get_arg("active", False), False)
    return failed


def test_a_valid_recent_plan_is_reused(my_predbat):
    """A scheduled run with a valid, recent plan executes it without recomputing."""
    print("*** Running test: a valid recent plan is re-used")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = True
    my_predbat.plan_last_updated = my_predbat.now_utc
    my_predbat.calculate_plan_every = 60
    stages = Stages(my_predbat)

    _drive(my_predbat, stages)

    failed |= _check("reuse: calculate_plan called once", len(stages.calculate_plan_args), 1)
    failed |= _check("reuse: called with recompute False", stages.calculate_plan_args, [False])
    failed |= _check("reuse: plan not re-saved", stages.count("save_plan"), 0)
    failed |= _check("reuse: inverter read once", stages.count("fetch_inverter_data"), 1)
    failed |= _check("reuse: plan executed once", stages.count("execute_plan"), 1)
    return failed


def test_an_expiring_plan_is_recalculated_before_the_next_run(my_predbat):
    """A plan that would age past calculate_plan_every before the next cycle is redone now."""
    print("*** Running test: an expiring plan is recalculated in the same cycle")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = True
    my_predbat.calculate_plan_every = 10
    my_predbat.plan_last_updated = my_predbat.now_utc - timedelta(minutes=9)
    my_predbat.args["plan_random_delay"] = 0
    stages = Stages(my_predbat)

    _drive(my_predbat, stages)

    failed |= _check("expiring: calculate_plan called twice", stages.calculate_plan_args, [False, True])
    failed |= _check("expiring: plan executed twice", stages.count("execute_plan"), 2)
    failed |= _check("expiring: inverter read twice", stages.count("fetch_inverter_data"), 2)
    return failed


def test_sensor_changes_force_a_recompute(my_predbat):
    """fetch_sensor_data reporting a change must force a recompute even when the plan is valid."""
    print("*** Running test: sensor changes force a recompute")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = True
    my_predbat.plan_last_updated = my_predbat.now_utc
    my_predbat.calculate_plan_every = 60
    stages = Stages(my_predbat, sensor_force_replan=True)

    _drive(my_predbat, stages)

    failed |= _check("sensor replan: recomputed", stages.calculate_plan_args, [True])
    return failed


def test_dynamic_load_changes_force_a_recompute(my_predbat):
    """A dynamic load adjustment must force a recompute even when the plan is valid."""
    print("*** Running test: dynamic load changes force a recompute")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = True
    my_predbat.plan_last_updated = my_predbat.now_utc
    my_predbat.calculate_plan_every = 60
    stages = Stages(my_predbat, dynamic_load=True)

    _drive(my_predbat, stages)

    failed |= _check("dynamic load: recomputed", stages.calculate_plan_args, [True])
    return failed


def test_an_unscheduled_run_always_recomputes(my_predbat):
    """A manual (unscheduled) run recomputes regardless of how fresh the saved plan is."""
    print("*** Running test: an unscheduled run always recomputes")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = True
    my_predbat.plan_last_updated = my_predbat.now_utc
    my_predbat.calculate_plan_every = 60
    stages = Stages(my_predbat)

    _drive(my_predbat, stages, scheduled=False)

    failed |= _check("unscheduled: recomputed", stages.calculate_plan_args, [True])
    return failed


def test_a_failed_second_inverter_read_stops_before_executing(my_predbat):
    """When the post-plan inverter re-read fails, the plan must not be executed."""
    print("*** Running test: a failed second inverter read stops before executing")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = False
    stages = Stages(my_predbat, inverter_ok=[True, False])

    _drive(my_predbat, stages)

    failed |= _check("second read fail: plan calculated", stages.calculate_plan_args, [True])
    failed |= _check("second read fail: nothing executed", stages.count("execute_plan"), 0)
    failed |= _check_true("second read fail: status reports it", "not able to execute the plan" in my_predbat.current_status)
    return failed


def test_plugin_hooks_fire_around_the_plan(my_predbat):
    """Plugins are told when the plan has been executed and when the cycle has finished."""
    print("*** Running test: plugin hooks fire around the plan")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = False
    plugins = RecordingPluginSystem()
    my_predbat.plugin_system = plugins
    stages = Stages(my_predbat)

    _drive(my_predbat, stages)

    failed |= _check("plugins: hooks fired", plugins.hook_names(), ["on_plan_executed", "on_update"])
    if plugins.hooks:
        payload = plugins.hooks[0][1]
        for key in ("charge_windows", "charge_limits", "export_windows", "export_limits", "charge_rate_w", "discharge_rate_w", "soc_max", "reserve", "timezone"):
            failed |= _check_true("plugins: on_plan_executed carries {}".format(key), key in payload)

    my_predbat.plugin_system = None
    return failed


def test_register_write_counter_accumulates_and_resets(my_predbat):
    """Per-inverter register writes are summed into the running total and then zeroed."""
    print("*** Running test: register write counter accumulates and resets")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = False
    my_predbat.count_inverter_writes = {0: 3, 1: 4}
    stages = Stages(my_predbat)

    _drive(my_predbat, stages)

    failed |= _check("writes: per-inverter counters zeroed", my_predbat.count_inverter_writes, {0: 0, 1: 0})
    total = my_predbat.get_state_wrapper(my_predbat.prefix + ".inverter_register_writes")
    failed |= _check("writes: total published", float(total), 7.0)
    return failed


def test_comparison_runs_at_midnight_and_publishes_otherwise(my_predbat):
    """Tariff comparison runs in full at midnight, and only refreshes its sensors the rest of the day."""
    print("*** Running test: comparison runs at midnight and publishes otherwise")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = False
    saved_minutes_now = my_predbat.minutes_now
    comparison = RecordingComparison()
    my_predbat.comparison = comparison
    my_predbat.args["compare_active"] = False

    my_predbat.minutes_now = 0
    _drive(my_predbat, Stages(my_predbat))
    failed |= _check("compare: full run at midnight", comparison.calls, ["run_all"])

    comparison.calls = []
    my_predbat.minutes_now = 12 * 60
    _drive(my_predbat, Stages(my_predbat))
    failed |= _check("compare: publish only during the day", comparison.calls, ["publish_only"])

    my_predbat.minutes_now = saved_minutes_now
    my_predbat.comparison = None
    return failed


def test_working_data_is_freed_unless_debugging(my_predbat):
    """The per-minute working arrays are dropped at the end of a cycle unless debug is on."""
    print("*** Running test: working data is freed unless debugging")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = False
    my_predbat.load_minutes_step = {0: 1.0}
    my_predbat.pv_forecast_minute_step = {0: 1.0}
    stages = Stages(my_predbat)

    _drive(my_predbat, stages)

    failed |= _check("free: load steps dropped", my_predbat.load_minutes_step, {})
    failed |= _check("free: pv steps dropped", my_predbat.pv_forecast_minute_step, {})
    failed |= _check("free: debug yaml not written", stages.count("create_debug_yaml"), 0)

    my_predbat.debug_enable = True
    my_predbat.load_minutes_step = {0: 1.0}
    stages = Stages(my_predbat)
    _drive(my_predbat, stages)
    failed |= _check("debug: load steps kept", my_predbat.load_minutes_step, {0: 1.0})
    failed |= _check("debug: debug yaml written", stages.count("create_debug_yaml"), 1)

    my_predbat.debug_enable = False
    my_predbat.load_minutes_step = {}
    return failed


def test_version_check_is_skipped_for_cloud_installs(my_predbat):
    """A cloud install cannot self-update, so update_pred must not check GitHub for releases."""
    print("*** Running test: version check is skipped for cloud installs")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = False

    stages = Stages(my_predbat)
    _drive(my_predbat, stages)
    failed |= _check("local install: version checked", stages.count("download_predbat_releases"), 1)

    my_predbat.args["user_id"] = "cloud-user"
    stages = Stages(my_predbat)
    _drive(my_predbat, stages)
    failed |= _check("cloud install: version not checked", stages.count("download_predbat_releases"), 0)

    my_predbat.args["user_id"] = None
    return failed


def test_component_watchdog_restarts_after_sustained_failure(my_predbat):
    """
    The watchdog restarts Predbat only once a component has been unhealthy for the full window,
    and gives up after the maximum number of restarts rather than cycling for ever.
    """
    print("*** Running test: component watchdog restart behaviour")
    failed = 0
    saved_since = my_predbat.component_error_since
    saved_fatal = my_predbat.fatal_error

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "watchdog_state")
        with patch.object(my_predbat, "watchdog_state_path", lambda: state_path), patch.object(my_predbat, "call_notify", lambda message: None):
            # A healthy run clears any remembered failure
            my_predbat.component_error_since = datetime.now(timezone.utc)
            my_predbat.check_component_watchdog([])
            failed |= _check("watchdog: healthy run clears the timer", my_predbat.component_error_since, None)

            # First failure only starts the clock
            my_predbat.fatal_error = False
            my_predbat.check_component_watchdog(["DB"])
            failed |= _check_true("watchdog: first failure starts the clock", my_predbat.component_error_since is not None)
            failed |= _check("watchdog: first failure does not restart", my_predbat.fatal_error, False)

            # Still inside the window, so still no restart
            my_predbat.component_error_since = datetime.now(timezone.utc) - timedelta(minutes=COMPONENT_ERROR_RESTART_MINUTES - 1)
            my_predbat.check_component_watchdog(["DB"])
            failed |= _check("watchdog: no restart inside the window", my_predbat.fatal_error, False)

            # Past the window, restart
            my_predbat.component_error_since = datetime.now(timezone.utc) - timedelta(minutes=COMPONENT_ERROR_RESTART_MINUTES + 1)
            my_predbat.check_component_watchdog(["DB"])
            failed |= _check("watchdog: restarts after the window", my_predbat.fatal_error, True)
            failed |= _check("watchdog: timer cleared on restart", my_predbat.component_error_since, None)
            failed |= _check("watchdog: restart recorded", len(my_predbat.watchdog_restart_history()), 1)

            # Once the restart budget is spent it warns instead of restarting again
            now = datetime.now(timezone.utc)
            my_predbat.watchdog_record_restart([now] * (WATCHDOG_MAX_RESTARTS - 1), now)
            failed |= _check("watchdog: history holds the budget", len(my_predbat.watchdog_restart_history()), WATCHDOG_MAX_RESTARTS)
            my_predbat.fatal_error = False
            my_predbat.component_error_since = datetime.now(timezone.utc) - timedelta(minutes=COMPONENT_ERROR_RESTART_MINUTES + 1)
            my_predbat.check_component_watchdog(["DB"])
            failed |= _check("watchdog: budget spent, no restart", my_predbat.fatal_error, False)
            failed |= _check_true("watchdog: re-armed to warn again", my_predbat.component_error_since is not None)

    my_predbat.component_error_since = saved_since
    my_predbat.fatal_error = saved_fatal
    return failed


def test_watchdog_history_ignores_old_and_corrupt_entries(my_predbat):
    """Restart history keeps only timestamps inside the backoff window and survives a corrupt file."""
    print("*** Running test: watchdog history ignores old and corrupt entries")
    failed = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "watchdog_state")
        with patch.object(my_predbat, "watchdog_state_path", lambda: state_path):
            # No file at all is not an error
            failed |= _check("history: missing file reads empty", my_predbat.watchdog_restart_history(), [])

            recent = datetime.now(timezone.utc) - timedelta(minutes=5)
            stale = datetime.now(timezone.utc) - timedelta(days=1)
            with open(state_path, "w") as handle:
                handle.write(recent.isoformat() + "\n")
                handle.write(stale.isoformat() + "\n")
                handle.write("\n")
                handle.write("not-a-timestamp\n")

            history = my_predbat.watchdog_restart_history()
            failed |= _check("history: only the recent entry survives", history, [recent])
    return failed


def test_savings_totals_roll_over_once_a_day(my_predbat):
    """
    Running totals absorb yesterday's savings once per day, after 1am and only on a scheduled run.

    The rollover has to happen exactly once: cloud data lags, so it waits until an hour into the
    day, and it stamps the date it ran so a later cycle on the same day does not double-count.
    """
    print("*** Running test: savings totals roll over once a day")
    failed = 0
    _reset_for_update_pred(my_predbat)
    my_predbat.plan_valid = False
    my_predbat.calculate_savings = True
    saved_minutes_now = my_predbat.minutes_now
    saved_num_cars = my_predbat.num_cars
    my_predbat.minutes_now = 90
    my_predbat.num_cars = 0
    my_predbat.savings_today_predbat = 40.0
    my_predbat.savings_today_pvbat = 20.0
    my_predbat.savings_today_predbat_soc = 5.0
    my_predbat.savings_today_actual = 10.0

    yesterday = (my_predbat.now_utc_real - timedelta(days=1)).strftime("%Y-%m-%d")
    today = my_predbat.now_utc_real.strftime("%Y-%m-%d")
    prefix = my_predbat.prefix
    previous = {
        (prefix + ".savings_total_predbat", None): 100.0,
        (prefix + ".savings_total_predbat", "last_updated"): yesterday,
        (prefix + ".savings_total_predbat", "start_date"): yesterday,
        (prefix + ".savings_total_pvbat", None): 200.0,
        (prefix + ".savings_total_actual", None): 300.0,
        (prefix + ".savings_total_soc", None): 1.0,
    }

    _drive(my_predbat, Stages(my_predbat), previous_values=previous)

    failed |= _check("savings: predbat total incremented", float(my_predbat.get_state_wrapper(prefix + ".savings_total_predbat")), 140.0)
    failed |= _check("savings: pvbat total incremented", float(my_predbat.get_state_wrapper(prefix + ".savings_total_pvbat")), 220.0)
    failed |= _check("savings: actual total incremented", float(my_predbat.get_state_wrapper(prefix + ".savings_total_actual")), 310.0)
    failed |= _check("savings: soc total replaced not added", float(my_predbat.get_state_wrapper(prefix + ".savings_total_soc")), 5.0)
    failed |= _check("savings: stamped with today", my_predbat.get_state_wrapper(prefix + ".savings_total_predbat", attribute="last_updated"), today)

    # Same day again: already stamped, so nothing is added a second time
    previous_same_day = dict(previous)
    previous_same_day[(prefix + ".savings_total_predbat", "last_updated")] = today
    _drive(my_predbat, Stages(my_predbat), previous_values=previous_same_day)
    failed |= _check("savings: no double count on a second cycle", float(my_predbat.get_state_wrapper(prefix + ".savings_total_predbat")), 100.0)

    # Read-only mode never moves the totals
    my_predbat.set_read_only = True
    _drive(my_predbat, Stages(my_predbat), previous_values=previous)
    failed |= _check("savings: read-only leaves totals alone", float(my_predbat.get_state_wrapper(prefix + ".savings_total_predbat")), 100.0)
    my_predbat.set_read_only = False

    # An unscheduled (manual) run must not roll over either
    _drive(my_predbat, Stages(my_predbat), scheduled=False, previous_values=previous)
    failed |= _check("savings: manual run leaves totals alone", float(my_predbat.get_state_wrapper(prefix + ".savings_total_predbat")), 100.0)

    # With cars configured the car cost total is published too
    my_predbat.num_cars = 1
    my_predbat.cost_yesterday_car = 7.0
    _drive(my_predbat, Stages(my_predbat), previous_values=previous)
    failed |= _check("savings: car cost total published", float(my_predbat.get_state_wrapper(prefix + ".cost_total_car")), 7.0)

    my_predbat.minutes_now = saved_minutes_now
    my_predbat.num_cars = saved_num_cars
    my_predbat.calculate_savings = False
    return failed


def test_iboost_model_follows_the_sensor_or_resets_at_midnight(my_predbat):
    """iBoost tracks a real energy sensor when there is one, and otherwise resets at midnight."""
    print("*** Running test: iBoost model follows the sensor or resets at midnight")
    failed = 0
    _reset_for_update_pred(my_predbat)
    saved_minutes_now = my_predbat.minutes_now
    saved_iboost_enable = my_predbat.iboost_enable
    saved_iboost_energy_today = my_predbat.iboost_energy_today
    my_predbat.plan_valid = False
    my_predbat.iboost_enable = True
    my_predbat.calculate_plan_every = 30

    # A real sensor wins: the model just follows today's reading
    my_predbat.iboost_energy_today = 3.5
    my_predbat.iboost_today = 3.5
    my_predbat.iboost_next = 0
    my_predbat.minutes_now = 12 * 60
    _drive(my_predbat, Stages(my_predbat))
    failed |= _check("iboost: follows the sensor", my_predbat.iboost_next, 3.5)

    # No sensor, and a recompute in the first slot of the day: the model resets
    my_predbat.iboost_energy_today = 0
    my_predbat.iboost_next = 9.0
    my_predbat.plan_valid = False
    my_predbat.minutes_now = 0
    _drive(my_predbat, Stages(my_predbat))
    failed |= _check("iboost: resets at midnight", my_predbat.iboost_next, 0)

    # No sensor and mid-afternoon: the model is left where it was
    my_predbat.iboost_next = 4.0
    my_predbat.plan_valid = False
    my_predbat.minutes_now = 15 * 60
    _drive(my_predbat, Stages(my_predbat))
    failed |= _check("iboost: untouched during the day", my_predbat.iboost_next, 4.0)

    # An unscheduled run does not move the model at all
    my_predbat.iboost_next = 4.0
    my_predbat.minutes_now = 0
    my_predbat.plan_valid = False
    _drive(my_predbat, Stages(my_predbat), scheduled=False)
    failed |= _check("iboost: manual run leaves it alone", my_predbat.iboost_next, 4.0)

    my_predbat.minutes_now = saved_minutes_now
    my_predbat.iboost_enable = saved_iboost_enable
    my_predbat.iboost_energy_today = saved_iboost_energy_today
    return failed


def test_car_manual_soc_and_holiday_countdown(my_predbat):
    """A scheduled run advances the manual car SoC and counts a holiday day down at midnight."""
    print("*** Running test: car manual SoC and holiday countdown")
    failed = 0
    _reset_for_update_pred(my_predbat)
    saved_minutes_now = my_predbat.minutes_now
    saved_num_cars = my_predbat.num_cars
    saved_manual_soc = list(my_predbat.car_charging_manual_soc)
    saved_soc = list(my_predbat.car_charging_soc)
    saved_soc_next = list(my_predbat.car_charging_soc_next)
    saved_holiday = my_predbat.holiday_days_left

    my_predbat.plan_valid = False
    my_predbat.num_cars = 1
    my_predbat.car_charging_manual_soc = [True]
    my_predbat.car_charging_soc = [10.0]
    my_predbat.car_charging_soc_next = [12.5]
    my_predbat.holiday_days_left = 3
    my_predbat.minutes_now = 0
    # The kWh item is gated on the manual-SoC switch, so turn that on before the cycle runs
    my_predbat.expose_config("car_charging_manual_soc", True)
    my_predbat.expose_config("car_charging_manual_soc_kwh", 0.0)

    _drive(my_predbat, Stages(my_predbat))

    failed |= _check("car: manual SoC advanced", my_predbat.config_index["car_charging_manual_soc_kwh"].get("value"), 12.5)
    failed |= _check("holiday: day counted down", my_predbat.holiday_days_left, 2)

    # Later in the day the holiday counter holds still
    my_predbat.plan_valid = False
    my_predbat.minutes_now = 10 * 60
    _drive(my_predbat, Stages(my_predbat))
    failed |= _check("holiday: unchanged during the day", my_predbat.holiday_days_left, 2)

    my_predbat.minutes_now = saved_minutes_now
    my_predbat.num_cars = saved_num_cars
    my_predbat.car_charging_manual_soc = saved_manual_soc
    my_predbat.car_charging_soc = saved_soc
    my_predbat.car_charging_soc_next = saved_soc_next
    my_predbat.holiday_days_left = saved_holiday
    my_predbat.expose_config("car_charging_manual_soc", False)
    return failed


def run_update_pred_tests(my_predbat):
    """Run every update_pred and lifecycle test, returning non-zero if any failed."""
    print("**** Running update_pred tests ****")
    failed = 0

    saved_args = my_predbat.args.copy()
    saved_plan_valid = my_predbat.plan_valid
    saved_plan_last_updated = my_predbat.plan_last_updated
    saved_calculate_plan_every = my_predbat.calculate_plan_every
    saved_rate_min = my_predbat.rate_min
    saved_rate_max = my_predbat.rate_max
    saved_comparison = my_predbat.comparison
    saved_plugin_system = my_predbat.plugin_system
    saved_count_inverter_writes = my_predbat.count_inverter_writes

    try:
        for test in (
            test_template_config_abandons_the_cycle,
            test_failed_inverter_read_abandons_the_cycle,
            test_all_zero_rates_abandon_the_cycle,
            test_stage_order_on_a_recompute,
            test_a_valid_recent_plan_is_reused,
            test_an_expiring_plan_is_recalculated_before_the_next_run,
            test_sensor_changes_force_a_recompute,
            test_dynamic_load_changes_force_a_recompute,
            test_an_unscheduled_run_always_recomputes,
            test_a_failed_second_inverter_read_stops_before_executing,
            test_plugin_hooks_fire_around_the_plan,
            test_register_write_counter_accumulates_and_resets,
            test_comparison_runs_at_midnight_and_publishes_otherwise,
            test_working_data_is_freed_unless_debugging,
            test_version_check_is_skipped_for_cloud_installs,
            test_savings_totals_roll_over_once_a_day,
            test_iboost_model_follows_the_sensor_or_resets_at_midnight,
            test_car_manual_soc_and_holiday_countdown,
            test_component_watchdog_restarts_after_sustained_failure,
            test_watchdog_history_ignores_old_and_corrupt_entries,
        ):
            failed |= test(my_predbat)
    finally:
        my_predbat.args = saved_args
        my_predbat.plan_valid = saved_plan_valid
        my_predbat.plan_last_updated = saved_plan_last_updated
        my_predbat.calculate_plan_every = saved_calculate_plan_every
        my_predbat.rate_min = saved_rate_min
        my_predbat.rate_max = saved_rate_max
        my_predbat.comparison = saved_comparison
        my_predbat.plugin_system = saved_plugin_system
        my_predbat.count_inverter_writes = saved_count_inverter_writes

    return failed
