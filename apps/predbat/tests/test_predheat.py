# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt: off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init
# fmt: on

"""Unit tests for the PredHeat heat pump / gas boiler prediction module.

PredHeat is an opt-in feature (``predheat_enable``) and had no test coverage at all, so
these tests start from the pure helpers (table interpolation, historical lookups), move
through the physics loop in ``run_simulation`` and finish with the scheduling wrappers.

The physics tests deliberately isolate a single term at a time. ``run_simulation`` couples
outside temperature, heat loss, flow temperature and efficiency together, so asserting on
raw energy totals would prove very little. Instead each test changes exactly one input that
cannot feed back into the thermal trajectory (for example the efficiency tables, which
divide the electrical input but leave the heat *output* - and therefore the water and room
temperatures - untouched), which makes the expected ratio exact rather than directional.
"""

from datetime import datetime, timedelta

import pytz

from const import TIME_FORMAT
from predheat import DELTA_CORRECTION, GAS_EFFICIENCY, HEAT_PUMP_EFFICIENCY, PREDICT_STEP, PredHeat


class MockPredHeatBase:
    """Minimal stand-in for the PredBat orchestrator that PredHeat binds its helpers from.

    PredHeat only reaches the rest of Predbat through the handful of callables it copies in
    ``__init__``, so a small mock keeps these tests fast and free of the full PredBat fixture.
    """

    def __init__(self, args=None):
        """Set up empty capture buffers and the apps.yaml style argument dictionary."""
        self.args = dict(args or {})
        self.log_messages = []
        self.status_messages = []
        self.states = {}
        self.config = {}
        self.history = {}
        self.service_response = {}
        self.service_calls = []
        self.run_every_calls = []
        self.rate_import = {}
        self.rate_gas = {}

    def log(self, message):
        """Record a log line so tests can assert on what PredHeat reported."""
        self.log_messages.append(message)

    def record_status(self, message, debug="", had_errors=False, notify=False, extra=""):
        """Record a status update, including whether it was flagged as an error."""
        self.status_messages.append({"message": message, "had_errors": had_errors})

    def get_arg(self, arg, default=None, indirect=True, combine=False, attribute=None, index=None, domain=None, can_override=True, required_unit=None):
        """Look up an argument, mirroring the real get_arg's domain scoping and type coercion.

        PredHeat passes ``domain="predheat"`` for its own settings (which live under the
        ``predheat:`` key in apps.yaml) and omits it for global ones such as ``timezone``.
        """
        if domain:
            value = self.args.get(domain, {}).get(arg, default)
        else:
            value = self.args.get(arg, default)

        if isinstance(value, dict) or isinstance(default, dict):
            return value

        if isinstance(default, bool):
            return value
        if isinstance(default, float):
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        if isinstance(default, int):
            try:
                return int(float(value))
            except (ValueError, TypeError):
                return default
        if isinstance(default, list) and not isinstance(value, list):
            return [value]
        return value

    def set_state_wrapper(self, entity_id, state, attributes={}, required_unit=None):
        """Capture a published entity so tests can assert on state and attributes."""
        self.states[entity_id] = {"state": state, "attributes": attributes}

    def call_service_wrapper(self, service, **kwargs):
        """Record a service call and return the canned response for it."""
        self.service_calls.append({"service": service, "kwargs": kwargs})
        return self.service_response.get(service, None)

    def get_services_wrapper(self):
        """Return an empty service list; PredHeat does not use this in these tests."""
        return []

    def get_history_wrapper(self, entity_id, days=30, required=True, tracked=True):
        """Return the canned history for an entity, or an empty list when none is set."""
        return self.history.get(entity_id, [])

    def expose_config(self, name, value):
        """Capture a config value written back by PredHeat."""
        self.config[name] = value

    def run_every(self, callback, next_time, run_every, **kwargs):
        """Record a scheduled callback registration."""
        self.run_every_calls.append({"callback": callback, "next_time": next_time, "run_every": run_every, "kwargs": kwargs})


def make_predheat(predheat_args=None, base_args=None):
    """Build a PredHeat instance against a mock base, returning both."""
    args = dict(base_args or {})
    args["predheat"] = dict(predheat_args or {})
    base = MockPredHeatBase(args)
    return PredHeat(base), base


def configure_simulation(
    ph,
    base,
    internal_temp=15.0,
    external_temp=5.0,
    target_temp=21.0,
    volume_temp=35.0,
    mode="pump",
    forecast_days=1,
    rate=10.0,
    heat_energy_today=0.0,
    import_today_cost=0.0,
    smart_thermostat=False,
    target_temperature=None,
):
    """Populate the attributes ``run_simulation`` expects, which ``update_pred`` normally sets.

    Driving ``run_simulation`` directly (rather than through ``update_pred``) keeps the physics
    tests independent of history parsing and weather fetching, so a failure points at the
    simulation rather than at the plumbing that feeds it.
    """
    ph.forecast_days = forecast_days
    ph.forecast_minutes = forecast_days * 24 * 60
    ph.minutes_now = 0
    ph.midnight_utc = datetime(2026, 1, 15, 0, 0, 0, tzinfo=pytz.UTC)

    ph.internal_temperature = {0: internal_temp}
    ph.external_temperature = {0: external_temp}
    ph.temperatures = {minute: external_temp for minute in range(0, forecast_days * 24 * 60 + 1)}

    # get_historical() indexes backwards from midnight, so a single previous day of data with a
    # constant target covers every forecast minute once the negative wrap in get_from_history applies.
    if target_temperature is None:
        target_temperature = {minute: target_temp for minute in range(0, 24 * 60 + 1)}
    ph.target_temperature = target_temperature
    ph.days_previous = [1]
    ph.days_previous_weight = [1]
    ph.minute_data_age = 1

    ph.heat_energy_today = heat_energy_today
    ph.import_today_cost = import_today_cost
    ph.rate_import = {minute: rate for minute in range(0, (forecast_days + 1) * 24 * 60 + 1)}

    ph.mode = mode
    ph.smart_thermostat = smart_thermostat
    ph.flow_temp = 40.0
    ph.flow_difference_target = 20.0 if mode == "gas" else 5.0
    ph.heat_loss_watts = 100
    ph.heat_loss_degrees = 0.02
    ph.heat_gain_static = 0
    ph.watt_per_degree = ph.heat_loss_watts / ph.heat_loss_degrees
    ph.heat_output = 7000
    ph.heat_volume = 200
    ph.heat_max_power = 30000
    ph.heat_min_power = 7000
    ph.heat_cop = 0.9
    ph.prefix = "predheat"
    return volume_temp


def build_history(now, days, value, minutes_step=30, incrementing=False):
    """Build a Home Assistant style history series ending at ``now``.

    ``minute_data`` walks the records backwards from ``now``, so the newest record must sit at
    ``now`` for minute 0 to be populated - PredHeat reads ``self.internal_temperature[0]`` directly
    and would raise KeyError otherwise.
    """
    records = []
    total_minutes = days * 24 * 60
    steps = total_minutes // minutes_step
    for step in range(steps, -1, -1):
        stamp = now - timedelta(minutes=step * minutes_step)
        if incrementing:
            # A daily incrementing counter that resets at midnight, as an energy sensor would report
            minutes_into_day = stamp.hour * 60 + stamp.minute
            state = round(value * minutes_into_day, 4)
        else:
            state = value
        records.append({"state": str(state), "last_updated": stamp.strftime(TIME_FORMAT)})
    return [records]


def _test_fill_table_gaps_empty(_my_predbat):
    """An empty table is returned unchanged rather than raising."""
    print("Test: fill_table_gaps returns an empty table untouched")
    ph, _base = make_predheat()
    if ph.fill_table_gaps({}) != {}:
        print("ERROR: expected an empty table back")
        return 1
    return 0


def _test_fill_table_gaps_interpolates(_my_predbat):
    """Missing keys are linearly interpolated and known keys are preserved exactly."""
    print("Test: fill_table_gaps linearly interpolates between known points")
    ph, _base = make_predheat()

    filled = ph.fill_table_gaps({0: 0.0, 4: 8.0})
    expected = {0: 0.0, 1: 2.0, 2: 4.0, 3: 6.0, 4: 8.0}
    if filled != expected:
        print("ERROR: expected {} got {}".format(expected, filled))
        return 1

    # Every integer key between the endpoints must exist, with the endpoints unchanged
    filled = ph.fill_table_gaps({10: 1.0, 20: 2.0})
    if sorted(filled.keys()) != list(range(10, 21)):
        print("ERROR: gaps not filled contiguously, got keys {}".format(sorted(filled.keys())))
        return 1
    if filled[10] != 1.0 or filled[20] != 2.0:
        print("ERROR: endpoints were modified: {} {}".format(filled[10], filled[20]))
        return 1
    if filled[15] != 1.5:
        print("ERROR: expected midpoint 1.5 got {}".format(filled[15]))
        return 1
    return 0


def _test_fill_table_gaps_single_entry(_my_predbat):
    """A single-entry table has no gaps to fill and comes back unchanged."""
    print("Test: fill_table_gaps handles a single-entry table")
    ph, _base = make_predheat()
    filled = ph.fill_table_gaps({7: 3.5})
    if filled != {7: 3.5}:
        print("ERROR: expected {{7: 3.5}} got {}".format(filled))
        return 1
    return 0


def _test_default_tables_filled(_my_predbat):
    """The three built-in correction tables are gap-filled at construction time.

    The simulation looks these up with a plain ``.get(key, fallback)`` on integer temperatures,
    so a table with holes would silently fall back mid-run instead of interpolating.
    """
    print("Test: the default correction tables are gap-filled on construction")
    ph, _base = make_predheat()

    for name, table, source in (
        ("delta_correction", ph.delta_correction, DELTA_CORRECTION),
        ("gas_efficiency", ph.gas_efficiency, GAS_EFFICIENCY),
        ("heat_pump_efficiency", ph.heat_pump_efficiency, HEAT_PUMP_EFFICIENCY),
    ):
        expected_keys = list(range(min(source.keys()), max(source.keys()) + 1))
        if sorted(table.keys()) != expected_keys:
            print("ERROR: {} is not contiguous, got {} keys expected {}".format(name, len(table), len(expected_keys)))
            return 1
        for key, value in source.items():
            if table[key] != value:
                print("ERROR: {} changed known point {} from {} to {}".format(name, key, value, table[key]))
                return 1

    if ph.heat_pump_efficiency_max != max(HEAT_PUMP_EFFICIENCY.values()):
        print("ERROR: heat_pump_efficiency_max is {} expected {}".format(ph.heat_pump_efficiency_max, max(HEAT_PUMP_EFFICIENCY.values())))
        return 1
    return 0


def _test_config_overrides_tables(_my_predbat):
    """User supplied efficiency points override the defaults and re-derive the maximum."""
    print("Test: apps.yaml efficiency overrides are merged into the default tables")
    ph, _base = make_predheat(
        {
            "heat_pump_efficiency": {0: 9.0},
            "gas_efficiency": {50: 0.5},
            "delta_correction": {50: 2.0},
        }
    )

    if ph.heat_pump_efficiency[0] != 9.0:
        print("ERROR: heat pump efficiency override not applied, got {}".format(ph.heat_pump_efficiency[0]))
        return 1
    if ph.gas_efficiency[50] != 0.5:
        print("ERROR: gas efficiency override not applied, got {}".format(ph.gas_efficiency[50]))
        return 1
    if ph.delta_correction[50] != 2.0:
        print("ERROR: delta correction override not applied, got {}".format(ph.delta_correction[50]))
        return 1
    # The override is the new maximum, so the COP normalisation must follow it
    if ph.heat_pump_efficiency_max != 9.0:
        print("ERROR: expected heat_pump_efficiency_max 9.0 got {}".format(ph.heat_pump_efficiency_max))
        return 1
    return 0


def _test_weather_compensation_flag(_my_predbat):
    """Weather compensation stays disabled until a table is configured."""
    print("Test: weather compensation is only enabled when a table is supplied")
    ph, _base = make_predheat()
    if ph.weather_compensation_enabled:
        print("ERROR: weather compensation should default to disabled")
        return 1
    if ph.weather_compensation != {}:
        print("ERROR: expected an empty weather compensation table, got {}".format(ph.weather_compensation))
        return 1

    ph, _base = make_predheat({"weather_compensation": {-10: 55, 10: 35}})
    if not ph.weather_compensation_enabled:
        print("ERROR: weather compensation should be enabled once configured")
        return 1
    if sorted(ph.weather_compensation.keys()) != list(range(-10, 11)):
        print("ERROR: weather compensation table was not gap filled, got {}".format(sorted(ph.weather_compensation.keys())))
        return 1
    if ph.weather_compensation[0] != 45.0:
        print("ERROR: expected an interpolated flow temp of 45.0 at 0C, got {}".format(ph.weather_compensation[0]))
        return 1
    return 0


def _test_hysteresis_absolute(_my_predbat):
    """Hysteresis values are stored as magnitudes so a negative config cannot invert the thermostat."""
    print("Test: hysteresis settings are made absolute")
    ph, _base = make_predheat({"hysteresis": -0.8, "hysteresis_off": -0.3})
    if ph.hysteresis_on != 0.8 or ph.hysteresis_off != 0.3:
        print("ERROR: expected 0.8/0.3 got {}/{}".format(ph.hysteresis_on, ph.hysteresis_off))
        return 1
    return 0


def _test_minutes_to_time(_my_predbat):
    """minutes_to_time returns whole minutes for both forward and multi-day offsets."""
    print("Test: minutes_to_time converts a timedelta into minutes")
    ph, _base = make_predheat()
    now = datetime(2026, 1, 15, 12, 0, 0)

    if ph.minutes_to_time(now + timedelta(minutes=90), now) != 90:
        print("ERROR: expected 90 minutes")
        return 1
    if ph.minutes_to_time(now + timedelta(days=2, minutes=30), now) != 2 * 24 * 60 + 30:
        print("ERROR: expected two days plus 30 minutes")
        return 1
    if ph.minutes_to_time(now, now) != 0:
        print("ERROR: expected 0 minutes for the same timestamp")
        return 1
    return 0


def _test_get_from_series(_my_predbat):
    """The incrementing and plain history accessors wrap negative indices into the previous day."""
    print("Test: get_from_incrementing / get_from_history index handling")
    ph, _base = make_predheat()

    data = {0: 10.0, 1: 8.0, 2: 5.0, 1439: 100.0, 1440: 99.0}
    if ph.get_from_incrementing(data, 0) != 2.0:
        print("ERROR: expected 2.0 for the most recent minute, got {}".format(ph.get_from_incrementing(data, 0)))
        return 1
    if ph.get_from_incrementing(data, 1) != 3.0:
        print("ERROR: expected 3.0, got {}".format(ph.get_from_incrementing(data, 1)))
        return 1
    # A negative index wraps forward by a day rather than reading off the end of the series
    if ph.get_from_incrementing(data, -1) != 1.0:
        print("ERROR: expected the wrapped index to read minute 1439, got {}".format(ph.get_from_incrementing(data, -1)))
        return 1

    if ph.get_from_history(data, 2) != 5.0:
        print("ERROR: expected 5.0 from history, got {}".format(ph.get_from_history(data, 2)))
        return 1
    if ph.get_from_history(data, -1) != 100.0:
        print("ERROR: expected the wrapped index to read minute 1439, got {}".format(ph.get_from_history(data, -1)))
        return 1
    if ph.get_from_history(data, 500) != 0:
        print("ERROR: a missing minute should read as 0")
        return 1
    return 0


def _test_get_historical_weighting(_my_predbat):
    """get_historical averages across days_previous using the configured weights."""
    print("Test: get_historical weighted average across previous days")
    ph, _base = make_predheat()

    # Empty data short circuits to 0 before any weighting happens
    if ph.get_historical({}, 0, default=20.0) != 0:
        print("ERROR: empty data should return 0")
        return 1

    # With minute_data_age of 0 no day qualifies, so the caller supplied default is used
    ph.days_previous = [1]
    ph.days_previous_weight = [1]
    ph.minute_data_age = 0
    if ph.get_historical({0: 5.0}, 0, default=20.0) != 20.0:
        print("ERROR: expected the default when no days of data are available")
        return 1

    # One day of data: minute 0 reads minute 1440 of the series (24h ago)
    ph.minute_data_age = 1
    data = {1440: 18.0, 1380: 19.0}
    if ph.get_historical(data, 0, default=20.0) != 18.0:
        print("ERROR: expected 18.0 got {}".format(ph.get_historical(data, 0, default=20.0)))
        return 1
    if ph.get_historical(data, 60, default=20.0) != 19.0:
        print("ERROR: expected 19.0 at minute 60, got {}".format(ph.get_historical(data, 60, default=20.0)))
        return 1

    # Two days with unequal weights produce a weighted mean, not a plain average
    ph.days_previous = [1, 2]
    ph.days_previous_weight = [3, 1]
    ph.minute_data_age = 2
    data = {1440: 10.0, 2880: 20.0}
    expected = (10.0 * 3 + 20.0 * 1) / 4
    result = ph.get_historical(data, 0, default=0)
    if abs(result - expected) > 1e-9:
        print("ERROR: expected weighted mean {} got {}".format(expected, result))
        return 1
    return 0


def _test_simulation_warm_house_stays_off(_my_predbat):
    """A house already above target uses no heating energy and publishes a flat cost."""
    print("Test: run_simulation leaves the heating off when the house is above target")
    ph, base = make_predheat()
    volume_temp = configure_simulation(ph, base, internal_temp=25.0, target_temp=21.0, heat_energy_today=4.0, import_today_cost=50.0)

    ph.run_simulation(volume_temp, heating_active=False, save="best")

    energy_h1 = base.states["predheat.heat_energy_h1"]["state"]
    cost_h1 = base.states["predheat.cost_h1"]["state"]
    if energy_h1 != 4.0:
        print("ERROR: expected heat energy to stay at 4.0 kWh, got {}".format(energy_h1))
        return 1
    if cost_h1 != 50.0:
        print("ERROR: expected cost to stay at 50.0p, got {}".format(cost_h1))
        return 1
    return 0


def _test_simulation_cold_house_heats(_my_predbat):
    """A cold house turns the heating on, burning energy and accruing cost at the import rate."""
    print("Test: run_simulation runs the heating when the house is below target")
    ph, base = make_predheat()
    volume_temp = configure_simulation(ph, base, internal_temp=15.0, target_temp=21.0, heat_energy_today=0.0, import_today_cost=0.0, rate=10.0)

    ph.run_simulation(volume_temp, heating_active=True, save="best")

    energy_h1 = base.states["predheat.heat_energy_h1"]["state"]
    cost_h1 = base.states["predheat.cost_h1"]["state"]
    if energy_h1 <= 0:
        print("ERROR: expected heating energy to be consumed, got {}".format(energy_h1))
        return 1
    if cost_h1 <= 0:
        print("ERROR: expected a cost to accrue, got {}".format(cost_h1))
        return 1
    # Cost is energy multiplied by a flat 10p rate, so the two must stay in step
    if abs(cost_h1 - energy_h1 * 10.0) > 0.05:
        print("ERROR: cost {} does not match energy {} at 10p/kWh".format(cost_h1, energy_h1))
        return 1
    return 0


def _test_simulation_heat_pump_cop(_my_predbat):
    """Halving the heat pump COP at the current outside temperature doubles the electrical input.

    ``cop_adjust`` is the ratio of the COP at this outside temperature to the table maximum, and it
    divides the electrical input only. The heat *output* is untouched, so the water and room
    temperature trajectories are identical between the two runs and the ratio is exact.
    """
    print("Test: run_simulation scales heat pump input energy by the COP curve")

    def run_with_cop_at_zero(cop_at_zero):
        """Run one simulation with the COP at 0C set to the given value."""
        ph, base = make_predheat({"heat_pump_efficiency": {-20: 4.0, 0: cop_at_zero, 20: 4.0}})
        volume_temp = configure_simulation(ph, base, internal_temp=15.0, external_temp=0.0, target_temp=21.0, mode="pump")
        ph.run_simulation(volume_temp, heating_active=True, save="best")
        return base.states["predheat.heat_energy_h1"]["state"]

    energy_full_cop = run_with_cop_at_zero(4.0)
    energy_half_cop = run_with_cop_at_zero(2.0)

    if energy_full_cop <= 0:
        print("ERROR: expected the baseline run to consume energy, got {}".format(energy_full_cop))
        return 1
    if abs(energy_half_cop - energy_full_cop * 2.0) > 0.02:
        print("ERROR: halving the COP should double the input: {} vs {}".format(energy_half_cop, energy_full_cop))
        return 1
    return 0


def _test_simulation_gas_efficiency(_my_predbat):
    """Gas mode divides the input energy by the condensing efficiency at the return temperature."""
    print("Test: run_simulation applies the gas condensing efficiency curve")

    def run_with_efficiency(efficiency):
        """Run one gas mode simulation with a flat boiler efficiency.

        Every tenth of the table is overridden so gap filling produces a flat curve; overriding
        only the endpoints would leave the default values in place around the return temperature
        the simulation actually looks up.
        """
        ph, base = make_predheat({"gas_efficiency": {key: efficiency for key in range(0, 101, 10)}})
        volume_temp = configure_simulation(ph, base, internal_temp=15.0, target_temp=21.0, mode="gas")
        ph.run_simulation(volume_temp, heating_active=True, save="best")
        return base.states["predheat.heat_energy_h1"]["state"]

    energy_efficient = run_with_efficiency(1.0)
    energy_poor = run_with_efficiency(0.5)

    if energy_efficient <= 0:
        print("ERROR: expected the efficient boiler to consume energy, got {}".format(energy_efficient))
        return 1
    if abs(energy_poor - energy_efficient * 2.0) > 0.02:
        print("ERROR: halving boiler efficiency should double the gas input: {} vs {}".format(energy_poor, energy_efficient))
        return 1
    return 0


def _test_simulation_weather_compensation(_my_predbat):
    """With weather compensation on, the flow temperature comes from the table not flow_temp.

    At 20C outside the table gives a 30C flow temperature. Running with weather compensation must
    therefore match a fixed 30C run exactly, and differ from the configured 40C flow temperature -
    which pins down both that the table is consulted and which entry is used.
    """
    print("Test: run_simulation takes the flow temperature from the weather compensation table")

    ph, base = make_predheat({"weather_compensation": {-20: 55, 20: 30}})
    volume_temp = configure_simulation(ph, base, internal_temp=15.0, external_temp=20.0, target_temp=21.0)
    ph.run_simulation(volume_temp, heating_active=True, save="best")
    energy_compensated = base.states["predheat.heat_energy_h8"]["state"]

    def run_fixed_flow(flow_temp):
        """Run the same scenario with weather compensation off and a fixed flow temperature."""
        ph_fixed, base_fixed = make_predheat()
        start_temp = configure_simulation(ph_fixed, base_fixed, internal_temp=15.0, external_temp=20.0, target_temp=21.0)
        ph_fixed.flow_temp = flow_temp
        ph_fixed.run_simulation(start_temp, heating_active=True, save="best")
        return base_fixed.states["predheat.heat_energy_h8"]["state"]

    energy_fixed_30 = run_fixed_flow(30.0)
    energy_fixed_40 = run_fixed_flow(40.0)

    if energy_compensated <= 0:
        print("ERROR: expected the compensated run to consume energy, got {}".format(energy_compensated))
        return 1
    if energy_compensated != energy_fixed_30:
        print("ERROR: weather compensation at 20C should match a fixed 30C flow temp: {} vs {}".format(energy_compensated, energy_fixed_30))
        return 1
    if energy_compensated == energy_fixed_40:
        print("ERROR: weather compensation should not match the configured 40C flow temp")
        return 1
    return 0


def _test_simulation_publishes_entities(_my_predbat):
    """save="best" publishes the full entity set; any other value publishes nothing."""
    print("Test: run_simulation only publishes entities when saving the best plan")

    ph, base = make_predheat()
    volume_temp = configure_simulation(ph, base, internal_temp=15.0, target_temp=21.0)
    ph.run_simulation(volume_temp, heating_active=True, save="none")
    if base.states:
        print("ERROR: expected no entities to be published, got {}".format(sorted(base.states.keys())))
        return 1

    ph, base = make_predheat()
    volume_temp = configure_simulation(ph, base, internal_temp=15.0, target_temp=21.0)
    ph.run_simulation(volume_temp, heating_active=True, save="best")

    expected = [
        "predheat.cost",
        "predheat.cost_h1",
        "predheat.cost_h2",
        "predheat.cost_h8",
        "predheat.external_temp",
        "predheat.heat_energy",
        "predheat.heat_energy_h1",
        "predheat.heat_energy_h2",
        "predheat.heat_energy_h8",
        "predheat.heat_to_temp",
        "predheat.internal_temp",
        "predheat.internal_temp_h1",
        "predheat.internal_temp_h2",
        "predheat.internal_temp_h8",
        "predheat.target_temp",
        "predheat.volume_temp",
    ]
    missing = [entity for entity in expected if entity not in base.states]
    if missing:
        print("ERROR: entities not published: {}".format(missing))
        return 1

    # The charting series is sampled every 10 minutes over the forecast horizon
    results = base.states["predheat.internal_temp"]["attributes"]["results"]
    if len(results) != (24 * 60) // 10:
        print("ERROR: expected {} chart points got {}".format((24 * 60) // 10, len(results)))
        return 1
    if base.states["predheat.internal_temp"]["attributes"]["unit_of_measurement"] != "°C":
        print("ERROR: internal temperature is missing its unit")
        return 1
    if base.states["predheat.cost"]["attributes"]["unit_of_measurement"] != "p":
        print("ERROR: cost is missing its unit")
        return 1
    return 0


def _test_simulation_prefix_applied(_my_predbat):
    """The configured prefix is used for every published entity."""
    print("Test: run_simulation honours the configured entity prefix")
    ph, base = make_predheat()
    volume_temp = configure_simulation(ph, base, internal_temp=15.0, target_temp=21.0)
    ph.prefix = "heating"
    ph.run_simulation(volume_temp, heating_active=True, save="best")

    if "heating.internal_temp" not in base.states:
        print("ERROR: expected entities under the heating prefix, got {}".format(sorted(base.states.keys())))
        return 1
    if any(entity.startswith("predheat.") for entity in base.states):
        print("ERROR: some entities still used the default prefix")
        return 1
    return 0


def _test_simulation_returns_volume_temp(_my_predbat):
    """The returned water temperature is the value after the first step, not the end of the horizon.

    This value is fed back in as the starting water temperature on the next 5-minute cycle, so
    returning the end-of-horizon figure instead would compound a large error every run.
    """
    print("Test: run_simulation returns the water temperature after the first step")
    ph, base = make_predheat()
    # A house above target keeps the heating off, so the water only cools and the trajectory is monotonic
    volume_temp = configure_simulation(ph, base, internal_temp=25.0, target_temp=21.0)

    next_volume_temp, predict_minute = ph.run_simulation(volume_temp, heating_active=False, save="best")

    chart = list(base.states["predheat.volume_temp"]["attributes"]["results"].values())
    end_of_horizon = chart[-1]

    if next_volume_temp >= volume_temp:
        print("ERROR: expected the water to have cooled after one step: {} vs {}".format(next_volume_temp, volume_temp))
        return 1
    if next_volume_temp <= end_of_horizon:
        print("ERROR: the returned value looks like the end of the horizon ({}) not the first step ({})".format(end_of_horizon, next_volume_temp))
        return 1
    if 0 not in predict_minute or (24 * 60 - PREDICT_STEP) not in predict_minute:
        print("ERROR: the predicted minute series does not span the horizon")
        return 1
    return 0


def _test_simulation_smart_thermostat_preheats(_my_predbat):
    """The smart thermostat brings a scheduled temperature rise forward so the target is met sooner.

    A single adjustment point is used here. The pre-heat window is sized from how long the first
    pass took to reach the new target, so the step target has to be one the house can actually
    reach within the horizon - otherwise the window collapses to zero and nothing is brought
    forward. With the target reached around minute 715 the pre-heat starts almost immediately,
    which shows up as heating energy in the first two hours where the plain run uses none.
    """
    print("Test: the smart thermostat pre-heats ahead of a scheduled temperature rise")

    # get_historical reads backwards (forecast minute m maps to index 1440 - m), so a target that
    # falls with index rises with forecast minute - the step up lands 6 hours into the forecast.
    step_minute = 6 * 60
    target_series = {}
    for index in range(0, 24 * 60 + 1):
        forecast_minute = 24 * 60 - index
        target_series[index] = 19.0 if forecast_minute >= step_minute else 17.0

    def run(smart):
        """Run the two pass simulation with the smart thermostat on or off."""
        ph, base = make_predheat()
        volume_temp = configure_simulation(ph, base, internal_temp=17.0, target_temp=17.0, smart_thermostat=smart, target_temperature=target_series)
        _next_volume, predict_minute = ph.run_simulation(volume_temp, heating_active=False, save="none")
        ph.run_simulation(volume_temp, heating_active=False, last_predict_minute=predict_minute, save="best")
        return base

    base_plain = run(False)
    base_smart = run(True)

    energy_plain_h2 = base_plain.states["predheat.heat_energy_h2"]["state"]
    energy_smart_h2 = base_smart.states["predheat.heat_energy_h2"]["state"]

    # Without the smart thermostat the target is still 17C at +2hrs, so nothing has run yet
    if energy_plain_h2 != 0:
        print("ERROR: expected no heating before the step without the smart thermostat, got {}".format(energy_plain_h2))
        return 1
    if energy_smart_h2 <= 0:
        print("ERROR: expected the smart thermostat to pre-heat before the step, got {}".format(energy_smart_h2))
        return 1

    # And the house is warmer by the time the thermostat would have stepped up
    chart_plain = list(base_plain.states["predheat.internal_temp"]["attributes"]["results"].values())
    chart_smart = list(base_smart.states["predheat.internal_temp"]["attributes"]["results"].values())
    step_index = step_minute // 10
    if chart_smart[step_index] <= chart_plain[step_index]:
        print("ERROR: expected a warmer house at the step: {} vs {}".format(chart_smart[step_index], chart_plain[step_index]))
        return 1
    return 0


def _test_today_cost_no_data(_my_predbat):
    """today_cost returns zero and logs when there is no import history to work from."""
    print("Test: today_cost handles missing import data")
    ph, base = make_predheat()
    configure_simulation(ph, base)

    if ph.today_cost({}) != 0:
        print("ERROR: expected a zero cost with no import data")
        return 1
    if not any("No import data" in message for message in base.log_messages):
        print("ERROR: expected a log line about missing import data")
        return 1
    if base.states:
        print("ERROR: no entity should be published when there is no data")
        return 1
    return 0


def _test_today_cost_accumulates(_my_predbat):
    """today_cost multiplies each minute's energy by that minute's rate and publishes the total."""
    print("Test: today_cost accumulates energy against the import rate")
    ph, base = make_predheat()
    configure_simulation(ph, base)
    ph.minutes_now = 60

    # A backwards incrementing series: 0.001 kWh consumed per minute over the last hour
    import_today = {minute: round((61 - minute) * 0.001, 6) for minute in range(0, 62)}
    ph.rate_import = {minute: 10.0 for minute in range(0, 24 * 60)}

    cost = ph.today_cost(import_today)

    # 60 minutes at 0.001 kWh and 10p/kWh
    if abs(cost - 0.6) > 0.001:
        print("ERROR: expected a cost of 0.6p got {}".format(cost))
        return 1
    if "predheat.cost_today" not in base.states:
        print("ERROR: cost_today was not published")
        return 1
    published = base.states["predheat.cost_today"]
    if published["state"] != 0.6:
        print("ERROR: expected a published state of 0.6 got {}".format(published["state"]))
        return 1
    # Sampled every 10 minutes across the elapsed part of the day
    if len(published["attributes"]["results"]) != 6:
        print("ERROR: expected 6 chart points got {}".format(len(published["attributes"]["results"])))
        return 1
    return 0


def _test_reset(_my_predbat):
    """reset clears the error and run flags and reads the entity prefix from config."""
    print("Test: reset restores the run state")
    ph, _base = make_predheat({"prefix": "heating"})
    ph.had_errors = True
    ph.prediction_started = True
    ph.update_pending = True

    ph.reset()

    if ph.had_errors or ph.prediction_started or ph.update_pending:
        print("ERROR: reset did not clear the run flags")
        return 1
    if ph.prefix != "heating":
        print("ERROR: expected the configured prefix, got {}".format(ph.prefix))
        return 1
    if ph.days_previous != [7] or ph.days_previous_weight != [1]:
        print("ERROR: expected the default history window after reset")
        return 1
    return 0


def _test_minute_data_entity_missing(_my_predbat):
    """An unconfigured entity yields an empty series rather than raising."""
    print("Test: minute_data_entity returns empty data for an unconfigured entity")
    ph, base = make_predheat()
    ph.max_days_previous = 2

    data, age = ph.minute_data_entity(datetime.now(pytz.UTC), "external_temperature")
    if data != {} or age != 0:
        print("ERROR: expected empty data and zero age, got {} / {}".format(data, age))
        return 1
    if base.status_messages:
        print("ERROR: an unconfigured entity should not be reported as an error")
        return 1
    return 0


def _test_minute_data_entity_no_history(_my_predbat):
    """A configured entity with no history is reported as an error."""
    print("Test: minute_data_entity reports an entity that returns no history")
    ph, base = make_predheat({"external_temperature": "sensor.outside"})
    ph.max_days_previous = 2

    data, age = ph.minute_data_entity(datetime.now(pytz.UTC), "external_temperature")
    if data != {}:
        print("ERROR: expected empty data, got {}".format(data))
        return 1
    if age != 0:
        print("ERROR: expected a zero age, got {}".format(age))
        return 1
    if not any(status["had_errors"] for status in base.status_messages):
        print("ERROR: expected an error status for the missing history")
        return 1
    return 0


def _test_minute_data_entity_reads_history(_my_predbat):
    """A configured entity with history is converted into a per-minute series."""
    print("Test: minute_data_entity converts history into per-minute data")
    now = datetime.now(pytz.timezone("Europe/London"))
    ph, base = make_predheat({"external_temperature": "sensor.outside"})
    ph.max_days_previous = 2
    base.history["sensor.outside"] = build_history(now, days=2, value=8.0)

    data, age = ph.minute_data_entity(now, "external_temperature", smoothing=True)

    if not data:
        print("ERROR: expected per-minute data to be produced")
        return 1
    if 0 not in data:
        print("ERROR: minute 0 must be populated, the simulation reads it directly")
        return 1
    if abs(data[0] - 8.0) > 0.01:
        print("ERROR: expected 8.0 at minute 0, got {}".format(data[0]))
        return 1
    if age < 1:
        print("ERROR: expected at least one day of history age, got {}".format(age))
        return 1
    return 0


def _test_minute_data_entity_history_raises(_my_predbat):
    """A history backend that raises is treated as no history rather than propagating."""
    print("Test: minute_data_entity swallows a failing history lookup")
    ph, base = make_predheat({"external_temperature": "sensor.outside"})
    ph.max_days_previous = 2

    def failing_history(entity_id, days=30, required=True, tracked=True):
        """Stand in for a history backend that rejects the query."""
        raise ValueError("history unavailable")

    ph.get_history = failing_history

    data, age = ph.minute_data_entity(datetime.now(pytz.UTC), "external_temperature")

    if data != {} or age != 0:
        print("ERROR: expected empty data and zero age, got {} / {}".format(data, age))
        return 1
    if not any(status["had_errors"] for status in base.status_messages):
        print("ERROR: expected the failure to be recorded as an error")
        return 1
    return 0


def _test_minute_data_entity_bad_timestamp(_my_predbat):
    """An unparseable last_updated falls back to now rather than aborting the run."""
    print("Test: minute_data_entity tolerates an unparseable timestamp")
    now = datetime.now(pytz.timezone("Europe/London"))
    ph, base = make_predheat({"external_temperature": "sensor.outside"})
    ph.max_days_previous = 2

    history = build_history(now, days=2, value=8.0)
    # The newest record is the one whose timestamp is used to age the series
    history[0][0]["last_updated"] = "not-a-timestamp"
    base.history["sensor.outside"] = history

    data, age = ph.minute_data_entity(now, "external_temperature", smoothing=True)

    # Falling back to now means the series reads as zero days old rather than raising
    if age != 0:
        print("ERROR: expected a zero age from the fallback, got {}".format(age))
        return 1
    if not data:
        print("ERROR: the remaining records should still be converted")
        return 1
    return 0


def _test_minute_data_entity_multiple_entities(_my_predbat):
    """Multiple entities are summed and the age is the freshest of them."""
    print("Test: minute_data_entity combines several entities")
    now = datetime.now(pytz.timezone("Europe/London"))
    ph, base = make_predheat({"heating_energy": ["sensor.heat_a", "sensor.heat_b"]})
    ph.max_days_previous = 3

    # Two days of history on one entity, one day on the other
    base.history["sensor.heat_a"] = build_history(now, days=2, value=10.0)
    base.history["sensor.heat_b"] = build_history(now, days=1, value=20.0)

    data, age = ph.minute_data_entity(now, "heating_energy", smoothing=True)

    if not data:
        print("ERROR: expected combined data")
        return 1
    # Each entity is scaled by 1/count, so the combined value is the mean of the two
    if abs(data[0] - 15.0) > 0.1:
        print("ERROR: expected the mean of 10 and 20 at minute 0, got {}".format(data[0]))
        return 1
    # The age is the minimum across entities, so the shorter series wins
    if age != 1:
        print("ERROR: expected the freshest age of 1 day, got {}".format(age))
        return 1
    return 0


def _test_simulation_multiple_adjustment_points(_my_predbat):
    """Every scheduled temperature rise gets its own pre-heat window, not just the last one.

    Regression test for the smart thermostat indexing the adjustment list by the loop variable
    left over from building it (always the final rise) rather than the rise currently being
    tracked. With two rises that meant the first one was never pre-heated, because the window
    checked belonged to the second. The first rise here is the one that must show a pre-heat.
    """
    print("Test: run_simulation pre-heats each of two scheduled temperature rises")

    # Two rises: 17 -> 19 at minute 360, then 19 -> 20 at minute 900
    first_step = 6 * 60
    second_step = 15 * 60
    target_series = {}
    for index in range(0, 24 * 60 + 1):
        forecast_minute = 24 * 60 - index
        if forecast_minute >= second_step:
            target_series[index] = 20.0
        elif forecast_minute >= first_step:
            target_series[index] = 19.0
        else:
            target_series[index] = 17.0

    def run(smart):
        """Run the two pass simulation with the smart thermostat on or off."""
        ph, base = make_predheat()
        volume_temp = configure_simulation(ph, base, internal_temp=17.0, target_temp=17.0, smart_thermostat=smart, target_temperature=target_series)
        _next_volume, predict_minute = ph.run_simulation(volume_temp, heating_active=False, save="none")
        ph.run_simulation(volume_temp, heating_active=False, last_predict_minute=predict_minute, save="best")
        return base

    base_plain = run(False)
    base_smart = run(True)

    if "predheat.internal_temp" not in base_smart.states:
        print("ERROR: the two-rise schedule did not produce a prediction")
        return 1

    # The first rise is at +6hrs, so pre-heating for it has to show up before then
    energy_plain_h2 = base_plain.states["predheat.heat_energy_h2"]["state"]
    energy_smart_h2 = base_smart.states["predheat.heat_energy_h2"]["state"]
    if energy_plain_h2 != 0:
        print("ERROR: expected no heating before the first rise without the smart thermostat, got {}".format(energy_plain_h2))
        return 1
    if energy_smart_h2 <= 0:
        print("ERROR: the first of two rises was not pre-heated, got {} kWh by +2hrs".format(energy_smart_h2))
        return 1

    # And the house is warmer than the plain run at both step boundaries
    chart_plain = list(base_plain.states["predheat.internal_temp"]["attributes"]["results"].values())
    chart_smart = list(base_smart.states["predheat.internal_temp"]["attributes"]["results"].values())
    for step_minute, label in ((first_step, "first"), (second_step, "second")):
        index = step_minute // 10
        if chart_smart[index] <= chart_plain[index]:
            print("ERROR: the {} rise was not pre-heated: {} vs {}".format(label, chart_smart[index], chart_plain[index]))
            return 1
    return 0


def _make_update_pred_predheat(mode="pump"):
    """Build a PredHeat wired up with enough history and weather data for a full update_pred run."""
    now = datetime.now(pytz.timezone("Europe/London"))
    ph, base = make_predheat(
        {
            "external_temperature": "sensor.outside",
            "internal_temperature": "sensor.inside",
            "target_temperature": "sensor.target",
            "heating_energy": "sensor.heat_energy",
            "weather": "weather.home",
            "days_previous": [1],
            "forecast_days": 1,
            "mode": mode,
        },
        base_args={"timezone": "Europe/London", "predheat_enable": True},
    )

    base.history["sensor.outside"] = build_history(now, days=2, value=6.0)
    base.history["sensor.inside"] = build_history(now, days=2, value=18.0)
    base.history["sensor.target"] = build_history(now, days=2, value=21.0)
    base.history["sensor.heat_energy"] = build_history(now, days=2, value=0.001, incrementing=True)

    forecast = []
    for hour in range(0, 48):
        stamp = now + timedelta(hours=hour)
        forecast.append({"datetime": stamp.strftime(TIME_FORMAT), "temperature": 6.0})
    base.service_response["weather/get_forecasts"] = {"weather.home": {"forecast": forecast}}

    base.rate_import = {minute: 25.0 for minute in range(0, 4 * 24 * 60)}
    base.rate_gas = {minute: 7.0 for minute in range(0, 4 * 24 * 60)}
    return ph, base


def _test_update_pred_end_to_end(_my_predbat):
    """A full update_pred run fetches history and weather, simulates and publishes results."""
    print("Test: update_pred runs end to end and publishes the prediction")
    ph, base = _make_update_pred_predheat()
    ph.reset()

    ph.update_pred(scheduled=False)

    if ph.had_errors:
        print("ERROR: update_pred reported errors: {}".format(base.status_messages))
        return 1
    for entity in ("predheat.internal_temp", "predheat.heat_energy", "predheat.cost", "predheat.cost_today"):
        if entity not in base.states:
            print("ERROR: {} was not published".format(entity))
            return 1
    if not any(call["service"] == "weather/get_forecasts" for call in base.service_calls):
        print("ERROR: the weather forecast service was not called")
        return 1
    # scheduled=False must not write the water temperature back to config
    if "next_volume_temp" in base.config:
        print("ERROR: an unscheduled run should not persist next_volume_temp")
        return 1
    return 0


def _test_update_pred_scheduled_persists_volume_temp(_my_predbat):
    """A scheduled run carries the predicted water temperature into the next cycle."""
    print("Test: a scheduled update_pred persists the water temperature")
    ph, base = _make_update_pred_predheat()
    ph.reset()

    ph.update_pred(scheduled=True)

    if "next_volume_temp" not in base.config:
        print("ERROR: next_volume_temp was not persisted")
        return 1
    if not isinstance(base.config["next_volume_temp"], float):
        print("ERROR: expected a float water temperature, got {}".format(base.config["next_volume_temp"]))
        return 1
    return 0


def _test_update_pred_gas_uses_gas_rates(_my_predbat):
    """Gas mode prices the prediction from rate_gas, heat pump mode from rate_import."""
    print("Test: update_pred selects the rate table that matches the heating mode")

    ph_gas, base_gas = _make_update_pred_predheat(mode="gas")
    ph_gas.reset()
    ph_gas.update_pred(scheduled=False)
    if ph_gas.rate_import is not base_gas.rate_gas:
        print("ERROR: gas mode should price from rate_gas")
        return 1

    ph_pump, base_pump = _make_update_pred_predheat(mode="pump")
    ph_pump.reset()
    ph_pump.update_pred(scheduled=False)
    if ph_pump.rate_import is not base_pump.rate_import:
        print("ERROR: heat pump mode should price from rate_import")
        return 1
    return 0


def _test_update_pred_weights_extended(_my_predbat):
    """Missing days_previous_weight entries are padded so every history day is weighted."""
    print("Test: update_pred pads days_previous_weight to match days_previous")
    ph, base = _make_update_pred_predheat()
    base.args["predheat"]["days_previous"] = [1, 2, 3]
    base.args["predheat"]["days_previous_weight"] = [2]
    ph.reset()

    ph.update_pred(scheduled=False)

    if ph.days_previous_weight != [2, 1, 1]:
        print("ERROR: expected weights [2, 1, 1] got {}".format(ph.days_previous_weight))
        return 1
    if ph.max_days_previous != 4:
        print("ERROR: expected max_days_previous of 4 got {}".format(ph.max_days_previous))
        return 1
    return 0


def _test_update_pred_missing_weather(_my_predbat):
    """A weather source that returns nothing is reported but does not stop the run."""
    print("Test: update_pred survives a weather source that returns no forecast")
    ph, base = _make_update_pred_predheat()
    base.service_response["weather/get_forecasts"] = None
    ph.reset()

    ph.update_pred(scheduled=False)

    if not any(status["had_errors"] for status in base.status_messages):
        print("ERROR: expected an error status for the missing forecast")
        return 1
    # The simulation still ran using the last known outside temperature
    if "predheat.internal_temp" not in base.states:
        print("ERROR: the prediction should still be published")
        return 1
    return 0


def _test_update_pred_reports_errors(_my_predbat):
    """A run that hit a real problem logs the error completion line, not a clean one.

    Regression test for had_errors only ever being cleared and never set: record_status was bound
    straight to the base's, so PredHeat's own flag stayed False and a run whose weather fetch had
    failed still reported a clean completion.
    """
    print("Test: update_pred reports a run that hit an error")
    ph, base = _make_update_pred_predheat()
    base.service_response["weather/get_forecasts"] = None
    ph.reset()

    ph.update_pred(scheduled=False)

    if not ph.had_errors:
        print("ERROR: a failed weather fetch should leave the run flagged as errored")
        return 1
    if not any("with Errors reported" in message for message in base.log_messages):
        print("ERROR: expected the error completion log line")
        return 1

    # A clean run reports a clean completion and leaves the flag clear
    ph_ok, base_ok = _make_update_pred_predheat()
    ph_ok.reset()
    ph_ok.update_pred(scheduled=False)

    if ph_ok.had_errors:
        print("ERROR: a clean run should not be flagged as errored")
        return 1
    if any("with Errors reported" in message for message in base_ok.log_messages):
        print("ERROR: a clean run logged the error completion line")
        return 1
    return 0


def _test_initialize_schedules_loops(_my_predbat):
    """initialize registers both timers and aligns the first run to the run_every boundary."""
    print("Test: initialize registers the run and update loops")
    ph, base = make_predheat({"run_every": 10})

    ph.initialize()

    if len(base.run_every_calls) != 2:
        print("ERROR: expected two scheduled callbacks got {}".format(len(base.run_every_calls)))
        return 1
    if not ph.update_pending:
        print("ERROR: initialize should leave an update pending")
        return 1

    run_loop = base.run_every_calls[0]
    if run_loop["run_every"] != 600:
        print("ERROR: expected a 600 second interval got {}".format(run_loop["run_every"]))
        return 1
    # The first run must land exactly on a run_every boundary from midnight
    seconds_into_day = (run_loop["next_time"] - run_loop["next_time"].replace(hour=0, minute=0, second=0, microsecond=0)).seconds
    if seconds_into_day % 600 != 0:
        print("ERROR: the first run time is not aligned to the interval: {}".format(run_loop["next_time"]))
        return 1

    update_loop = base.run_every_calls[1]
    if update_loop["run_every"] != 5:
        print("ERROR: expected the update loop to run every 5 seconds got {}".format(update_loop["run_every"]))
        return 1
    return 0


def _test_initialize_reports_reset_failure(_my_predbat):
    """A failure inside reset is logged, recorded as an error and re-raised."""
    print("Test: initialize surfaces a failure during reset")
    ph, base = make_predheat()

    def failing_reset():
        """Stand in for a reset that cannot read its configuration."""
        raise ValueError("bad config")

    ph.reset = failing_reset

    try:
        ph.initialize()
    except ValueError:
        pass
    else:
        print("ERROR: expected the exception to propagate")
        return 1

    if not any(status["had_errors"] for status in base.status_messages):
        print("ERROR: expected an error status to be recorded")
        return 1
    if base.run_every_calls:
        print("ERROR: no timers should be registered after a failed reset")
        return 1
    return 0


def _install_recording_update_pred(ph, raises=False):
    """Replace update_pred with a recorder, optionally one that raises, and return the call log."""
    calls = []

    def recording_update_pred(scheduled):
        """Record the scheduled flag and optionally fail, standing in for a real prediction."""
        calls.append(scheduled)
        if raises:
            raise ValueError("simulated failure")

    ph.update_pred = recording_update_pred
    return calls


def _test_loops_respect_enable_switch(_my_predbat):
    """Both timer callbacks return immediately while predheat_enable is off."""
    print("Test: the timer loops do nothing while PredHeat is disabled")
    ph, base = make_predheat(base_args={"predheat_enable": False})
    ph.reset()
    calls = _install_recording_update_pred(ph)

    ph.update_time_loop(None)
    ph.run_time_loop(None)

    if calls:
        print("ERROR: update_pred should not run while disabled, got {} call(s)".format(len(calls)))
        return 1
    if base.states:
        print("ERROR: nothing should be published while disabled")
        return 1
    return 0


def _test_update_time_loop_runs_pending(_my_predbat):
    """The update loop consumes a pending flag once and runs an unscheduled prediction."""
    print("Test: the update loop runs a pending prediction exactly once")
    ph, _base = make_predheat(base_args={"predheat_enable": True})
    ph.reset()
    calls = _install_recording_update_pred(ph)
    ph.update_pending = True

    ph.update_time_loop(None)

    if calls != [False]:
        print("ERROR: expected one unscheduled run got {}".format(calls))
        return 1
    if ph.update_pending:
        print("ERROR: the pending flag should have been consumed")
        return 1
    if ph.prediction_started:
        print("ERROR: prediction_started should be cleared after the run")
        return 1

    # A second tick with nothing pending must not re-run
    ph.update_time_loop(None)
    if calls != [False]:
        print("ERROR: expected no further runs got {}".format(calls))
        return 1
    return 0


def _test_update_time_loop_skips_while_running(_my_predbat):
    """A prediction already in flight blocks a re-entrant run from the update loop."""
    print("Test: the update loop does not re-enter a running prediction")
    ph, _base = make_predheat(base_args={"predheat_enable": True})
    ph.reset()
    calls = _install_recording_update_pred(ph)
    ph.update_pending = True
    ph.prediction_started = True

    ph.update_time_loop(None)

    if calls:
        print("ERROR: expected no run while a prediction is in flight, got {}".format(calls))
        return 1
    if not ph.update_pending:
        print("ERROR: the pending flag should survive so the run happens later")
        return 1
    return 0


def _test_run_time_loop_runs_scheduled(_my_predbat):
    """The scheduled loop runs a scheduled prediction and clears any pending flag."""
    print("Test: the run loop performs a scheduled prediction")
    ph, _base = make_predheat(base_args={"predheat_enable": True})
    ph.reset()
    calls = _install_recording_update_pred(ph)
    ph.update_pending = True

    ph.run_time_loop(None)

    if calls != [True]:
        print("ERROR: expected one scheduled run got {}".format(calls))
        return 1
    if ph.update_pending:
        print("ERROR: the pending flag should be cleared by a scheduled run")
        return 1
    if ph.prediction_started:
        print("ERROR: prediction_started should be cleared after the run")
        return 1
    return 0


def _test_loops_clear_flag_on_exception(_my_predbat):
    """A failing prediction re-raises but still clears the in-flight flag.

    Without the ``finally`` block a single failure would wedge PredHeat permanently, since every
    later tick would see ``prediction_started`` and skip.
    """
    print("Test: a failed prediction does not wedge the loops")

    for loop_name in ("update_time_loop", "run_time_loop"):
        ph, base = make_predheat(base_args={"predheat_enable": True})
        ph.reset()
        _install_recording_update_pred(ph, raises=True)
        ph.update_pending = True

        try:
            getattr(ph, loop_name)(None)
        except ValueError:
            pass
        else:
            print("ERROR: {} should re-raise the failure".format(loop_name))
            return 1

        if ph.prediction_started:
            print("ERROR: {} left prediction_started set after a failure".format(loop_name))
            return 1
        if not any(status["had_errors"] for status in base.status_messages):
            print("ERROR: {} did not record an error status".format(loop_name))
            return 1

        # The loop must be able to run again after the failure
        calls = _install_recording_update_pred(ph)
        ph.update_pending = True
        getattr(ph, loop_name)(None)
        if not calls:
            print("ERROR: {} did not recover after a failure".format(loop_name))
            return 1
    return 0


def test_predheat(my_predbat=None):
    """Run the PredHeat test suite, returning non-zero if any sub-test failed."""
    sub_tests = [
        ("fill_gaps_empty", _test_fill_table_gaps_empty, "Empty correction table"),
        ("fill_gaps_interpolate", _test_fill_table_gaps_interpolates, "Correction table interpolation"),
        ("fill_gaps_single", _test_fill_table_gaps_single_entry, "Single entry correction table"),
        ("default_tables", _test_default_tables_filled, "Built-in tables gap filled"),
        ("config_overrides", _test_config_overrides_tables, "apps.yaml efficiency overrides"),
        ("weather_compensation_flag", _test_weather_compensation_flag, "Weather compensation enablement"),
        ("hysteresis", _test_hysteresis_absolute, "Hysteresis made absolute"),
        ("minutes_to_time", _test_minutes_to_time, "Timedelta to minutes"),
        ("series_accessors", _test_get_from_series, "History series accessors"),
        ("get_historical", _test_get_historical_weighting, "Weighted historical lookup"),
        ("sim_warm_house", _test_simulation_warm_house_stays_off, "Heating stays off above target"),
        ("sim_cold_house", _test_simulation_cold_house_heats, "Heating runs below target"),
        ("sim_heat_pump_cop", _test_simulation_heat_pump_cop, "Heat pump COP scaling"),
        ("sim_gas_efficiency", _test_simulation_gas_efficiency, "Gas condensing efficiency"),
        ("sim_weather_comp", _test_simulation_weather_compensation, "Weather compensated flow temp"),
        ("sim_publish", _test_simulation_publishes_entities, "Prediction entity publishing"),
        ("sim_prefix", _test_simulation_prefix_applied, "Entity prefix"),
        ("sim_volume_temp", _test_simulation_returns_volume_temp, "Returned water temperature"),
        ("sim_smart_thermostat", _test_simulation_smart_thermostat_preheats, "Smart thermostat pre-heat"),
        ("today_cost_empty", _test_today_cost_no_data, "Today cost with no data"),
        ("today_cost", _test_today_cost_accumulates, "Today cost accumulation"),
        ("reset", _test_reset, "Run state reset"),
        ("entity_missing", _test_minute_data_entity_missing, "Unconfigured entity"),
        ("entity_no_history", _test_minute_data_entity_no_history, "Entity with no history"),
        ("entity_history", _test_minute_data_entity_reads_history, "Entity history conversion"),
        ("entity_history_raises", _test_minute_data_entity_history_raises, "Failing history lookup"),
        ("entity_bad_timestamp", _test_minute_data_entity_bad_timestamp, "Unparseable timestamp"),
        ("entity_multiple", _test_minute_data_entity_multiple_entities, "Multiple source entities"),
        ("sim_multi_adjust", _test_simulation_multiple_adjustment_points, "Multiple thermostat rises"),
        ("update_pred", _test_update_pred_end_to_end, "Full update_pred run"),
        ("update_pred_scheduled", _test_update_pred_scheduled_persists_volume_temp, "Scheduled run persists state"),
        ("update_pred_rates", _test_update_pred_gas_uses_gas_rates, "Rate table selection by mode"),
        ("update_pred_weights", _test_update_pred_weights_extended, "days_previous_weight padding"),
        ("update_pred_no_weather", _test_update_pred_missing_weather, "Missing weather forecast"),
        ("update_pred_errors", _test_update_pred_reports_errors, "Error reporting on completion"),
        ("initialize", _test_initialize_schedules_loops, "Timer registration"),
        ("initialize_failure", _test_initialize_reports_reset_failure, "Reset failure handling"),
        ("loops_disabled", _test_loops_respect_enable_switch, "Enable switch gating"),
        ("update_loop", _test_update_time_loop_runs_pending, "Pending prediction run"),
        ("update_loop_reentry", _test_update_time_loop_skips_while_running, "Re-entry guard"),
        ("run_loop", _test_run_time_loop_runs_scheduled, "Scheduled prediction run"),
        ("loops_exception", _test_loops_clear_flag_on_exception, "Failure does not wedge the loops"),
    ]

    print("\n" + "=" * 70)
    print("PREDHEAT TEST SUITE")
    print("=" * 70)

    failed = 0
    passed = 0

    for test_name, test_func, test_desc in sub_tests:
        print("\n[{}] {}".format(test_name, test_desc))
        print("-" * 70)
        try:
            test_result = test_func(my_predbat)
        except Exception as e:
            import traceback

            traceback.print_exc()
            print("✗ FAILED: {} raised {}".format(test_name, e))
            failed += 1
            continue

        if test_result:
            print("✗ FAILED: {}".format(test_name))
            failed += 1
        else:
            print("✓ PASSED: {}".format(test_name))
            passed += 1

    print("\n" + "=" * 70)
    print("PREDHEAT RESULTS: {} passed, {} failed".format(passed, failed))
    print("=" * 70)
    return failed
