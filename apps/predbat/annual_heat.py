# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------

"""Heat pump and gas boiler model for the annual prediction tool.

Answers "should I swap my gas boiler for a heat pump?" by turning a year of real
hourly outdoor temperatures into two things: what the gas boiler would have cost,
and what electrical load a heat pump would have drawn instead. The electrical load
goes back through the ordinary Predbat scenarios, so the heat pump gets the benefit
of PV, the battery and Predbat's planning exactly as any other load would.

Pure arithmetic over plain dicts and a temperature series: no network, no Predbat
state. ``annual_weather.py`` owns the temperature download.

Three ideas carry most of the weight:

* **Degree days, not a flat smear.** Space heating is spread across the year in
  proportion to each hour's heating degree hours, so January carries its real share
  and July carries almost none. A flat smear would put summer heat demand into
  months with no heating in them and understate the winter electricity peak that
  decides whether a battery can cover it.

* **COP varies with outdoor temperature.** A heat pump is least efficient exactly
  when it is working hardest, which is the single most important effect in the whole
  comparison - a fixed COP flatters the heat pump badly. The shape comes from the
  Carnot limit; its level is calibrated to the user's own SCOP figure, so the number
  they recognise from a datasheet is preserved while the seasonal variation around it
  is physical.

* **Hot water is separate.** It is weather independent, and needs a higher flow
  temperature than radiators, so it runs at a materially worse COP than space
  heating. Folding it into the space heating figure would overstate the heat pump.
"""

import calendar
import math
from datetime import date, timedelta

# Ofgem's medium Typical Domestic Consumption Value for gas, kWh a year. Only ever a
# default for a user who has not looked at a bill yet.
DEFAULT_ANNUAL_GAS_KWH = 11500.0

# Gas used for hot water and cooking rather than space heating. Weather independent,
# so it is held out of the degree-day distribution entirely. Roughly a fifth of a
# typical household's gas.
DEFAULT_WATER_GAS_KWH = 2500.0

# Seasonal efficiency of a real condensing boiler in a real house, NOT the ~92%
# nameplate figure: most systems never reach their condensing return temperature in
# anger. Using the nameplate figure would credit the boiler with heat it does not
# deliver and make the heat pump look worse than it is.
DEFAULT_BOILER_EFFICIENCY = 0.85

# Hot water is worse than space heating on gas, and gets its own figure. A stored
# cylinder loses heat around the clock and the boiler short-cycles to reheat it, so
# the gas burned per kWh of USEFUL hot water is higher than for space heating. Holding
# these apart matters because the standing loss of the OLD cylinder is what this
# efficiency absorbs - the heat pump's own cylinder loss is then added back
# explicitly, so both sides carry their own losses rather than sharing one number.
DEFAULT_WATER_BOILER_EFFICIENCY = 0.80

# A common cylinder for a UK family home, and the temperature it is charged to.
DEFAULT_CYLINDER_VOLUME_L = 180.0
DEFAULT_HOT_WATER_TARGET_C = 50.0

# Incoming mains temperature. Varies seasonally in reality (roughly 5-15C), but the
# annual average is what the charge-size arithmetic needs.
DEFAULT_COLD_MAINS_C = 10.0

# How many times a day the cylinder is reheated. One is the usual pattern for a heat
# pump: a single overnight charge that lasts the day, which is also what a smart
# cylinder is designed around.
DEFAULT_WATER_CHARGES_PER_DAY = 1

# Thermal output the heat pump puts into the cylinder while charging it. Decides how
# LONG a charge takes, and therefore how much of a cheap rate band it has to fit into.
DEFAULT_DHW_KW = 3.0

# Standing loss of the heat pump's own cylinder, kWh a day. A modern 180 L cylinder
# loses roughly this much; it is charged to the heat pump rather than absorbed into an
# efficiency figure so that shifting a charge earlier is not silently free.
DEFAULT_CYLINDER_STANDING_LOSS_KWH = 1.0

# Specific heat capacity of water, kJ per kg per K, for the cylinder charge size.
WATER_SPECIFIC_HEAT_KJ = 4.186
SECONDS_PER_HOUR = 3600.0

# 2025/26 price-cap gas figures. Overridden from the form in the normal case.
DEFAULT_GAS_P_PER_KWH = 6.4
DEFAULT_GAS_STANDING_CHARGE_P_PER_DAY = 32.0

# A realistic whole-system SCOP for a UK retrofit, in line with field-trial results
# rather than the optimistic figure on an MCS design certificate.
DEFAULT_SCOP = 3.2

# Flow temperatures. 45C suits a retrofit with somewhat upsized radiators; underfloor
# heating runs nearer 35C, an untouched radiator circuit nearer 55C.
DEFAULT_FLOW_TEMP_C = 45.0

# A cylinder has to reach a higher temperature than any radiator circuit, which is why
# hot water is modelled with its own flow temperature and therefore its own, worse, COP.
DEFAULT_WATER_FLOW_TEMP_C = 55.0

# The base temperature for heating degree days. 15.5C is the long-standing UK
# convention: internal gains from people, cooking and appliances make up the gap
# between it and a ~19C room.
DEFAULT_BASE_TEMP_C = 15.5

# Overnight setback. Heat pumps run best continuously, so this is a gentle trim rather
# than the boiler-style "off at night" a timer would give.
SETBACK_HOURS = frozenset([23, 0, 1, 2, 3, 4, 5])
SETBACK_FACTOR = 0.75

# When each of the day's cylinder charges has to be FINISHED by, and what share of the
# day's hot water it carries, indexed by how many charges a day are configured. A charge
# is scheduled to complete by its ready time, not started at it - hot water is only
# useful if it is already hot when someone turns the tap on.
WATER_READY_SCHEDULE = {
    1: ((7, 1.0),),
    2: ((7, 0.6), (16, 0.4)),
    3: ((7, 0.5), (13, 0.25), (18, 0.25)),
}

# The earliest a charge may be scheduled, as an hour of the local day. A smart cylinder
# will happily charge at 02:00 for a 07:00 draw-off; it will not charge at 07:00 the
# previous morning, because the standing loss over that long would swamp the saving.
WATER_EARLIEST_CHARGE_HOUR = 0

# The Carnot COP is unbounded as the lift approaches zero, which would hand a heat
# pump an absurd efficiency on a mild day. Floor the lift and clamp the result.
MIN_TEMP_LIFT_C = 8.0
MIN_COP = 1.0
MAX_COP = 6.0

HOURS_PER_DAY = 24
MINUTES_PER_HOUR = 60
DAY_MINUTES = HOURS_PER_DAY * MINUTES_PER_HOUR
KELVIN = 273.15


class AnnualHeatError(ValueError):
    """Raised when the heat configuration is unusable."""


def carnot_cop(outdoor_c, flow_c):
    """Return the ideal (Carnot) COP for lifting heat from ``outdoor_c`` to ``flow_c``.

    This is the thermodynamic ceiling, not an achievable efficiency - ``HeatModel``
    scales it down by a quality factor derived from the user's SCOP. The lift is
    floored at ``MIN_TEMP_LIFT_C`` because the ratio diverges as the lift approaches
    zero, which would otherwise report a COP of several hundred on a mild spring day.
    """
    lift = max(MIN_TEMP_LIFT_C, float(flow_c) - float(outdoor_c))
    return (float(flow_c) + KELVIN) / lift


def resolve_heat(raw):
    """Return the validated heat settings, or None when no heat pump is being modelled.

    Returns ``None`` for a missing block or one with ``enable`` false, which is how
    "no heat pump in this run" is expressed all the way down: every other function
    here is only reached with a real configuration. Raises ``AnnualHeatError`` naming
    the offending field otherwise, rather than silently defaulting a bad value and
    costing the user's heating at a price they did not ask for.
    """
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise AnnualHeatError("annual.heat must be a mapping")
    if not _as_bool(raw.get("enable", True)):
        return None

    settings = {
        "annual_gas_kwh": _number(raw, "annual_gas_kwh", DEFAULT_ANNUAL_GAS_KWH, minimum=0),
        "water_gas_kwh": _number(raw, "water_gas_kwh", DEFAULT_WATER_GAS_KWH, minimum=0),
        "boiler_efficiency": _number(raw, "boiler_efficiency", DEFAULT_BOILER_EFFICIENCY, minimum=0, maximum=1, exclusive_minimum=True),
        "gas_p_per_kwh": _number(raw, "gas_p_per_kwh", DEFAULT_GAS_P_PER_KWH, minimum=0),
        "gas_standing_charge_p_per_day": _number(raw, "gas_standing_charge_p_per_day", DEFAULT_GAS_STANDING_CHARGE_P_PER_DAY, minimum=0),
        "scop": _number(raw, "scop", DEFAULT_SCOP, minimum=1, maximum=MAX_COP),
        "flow_temp_c": _number(raw, "flow_temp_c", DEFAULT_FLOW_TEMP_C, minimum=20, maximum=70),
        "water_flow_temp_c": _number(raw, "water_flow_temp_c", DEFAULT_WATER_FLOW_TEMP_C, minimum=20, maximum=80),
        "base_temp_c": _number(raw, "base_temp_c", DEFAULT_BASE_TEMP_C, minimum=5, maximum=25),
        "water_boiler_efficiency": _number(raw, "water_boiler_efficiency", DEFAULT_WATER_BOILER_EFFICIENCY, minimum=0, maximum=1, exclusive_minimum=True),
        # A smart cylinder (Mixergy and similar) charges on the cheapest half-hours ahead
        # of when the water is needed, rather than at a fixed hour. That is usually the
        # single biggest lever in the whole comparison, so it is modelled explicitly.
        "smart_hot_water": _as_bool(raw.get("smart_hot_water", False)),
        "cylinder_volume_l": _number(raw, "cylinder_volume_l", DEFAULT_CYLINDER_VOLUME_L, minimum=0, exclusive_minimum=True),
        "hot_water_target_c": _number(raw, "hot_water_target_c", DEFAULT_HOT_WATER_TARGET_C, minimum=30, maximum=80),
        "cold_mains_c": _number(raw, "cold_mains_c", DEFAULT_COLD_MAINS_C, minimum=0, maximum=30),
        "water_charges_per_day": _number(raw, "water_charges_per_day", DEFAULT_WATER_CHARGES_PER_DAY, minimum=1, maximum=3, integer=True),
        "dhw_kw": _number(raw, "dhw_kw", DEFAULT_DHW_KW, minimum=0, exclusive_minimum=True),
        "cylinder_standing_loss_kwh_per_day": _number(raw, "cylinder_standing_loss_kwh_per_day", DEFAULT_CYLINDER_STANDING_LOSS_KWH, minimum=0),
        # Whether the gas supply is cut off completely. A household keeping a gas hob
        # still pays the standing charge, and that is often most of the remaining bill,
        # so it must not be assumed away.
        "remove_gas_supply": _as_bool(raw.get("remove_gas_supply", True)),
    }

    if settings["water_gas_kwh"] > settings["annual_gas_kwh"]:
        raise AnnualHeatError("annual.heat.water_gas_kwh ({}) cannot exceed annual_gas_kwh ({}): hot water and cooking are part of the gas total, not on top of it".format(settings["water_gas_kwh"], settings["annual_gas_kwh"]))

    # A heat pump cannot charge a cylinder to a temperature its flow never reaches. Caught
    # here rather than left to produce a quietly optimistic COP against a target the
    # system could not actually hit.
    if settings["water_flow_temp_c"] <= settings["hot_water_target_c"]:
        raise AnnualHeatError(
            "annual.heat.water_flow_temp_c ({}) must be above hot_water_target_c ({}): the heat pump cannot charge the cylinder to a temperature hotter than the water it sends it".format(settings["water_flow_temp_c"], settings["hot_water_target_c"])
        )

    # Delivered heat, not gas burned. Everything downstream works in heat. Space heating
    # and hot water use their OWN efficiency, so the gas side's cylinder standing loss is
    # absorbed into water_boiler_efficiency and the heat pump's is added back separately -
    # see HeatModel.water_heat_for_heat_pump_per_day().
    settings["space_heat_kwh"] = (settings["annual_gas_kwh"] - settings["water_gas_kwh"]) * settings["boiler_efficiency"]
    settings["water_heat_kwh"] = settings["water_gas_kwh"] * settings["water_boiler_efficiency"]
    return settings


def _as_bool(value):
    """Coerce a config value to a bool the way the rest of the annual tool does.

    ``bool("false")`` is ``True`` in plain Python, so an explicit ``"false"`` from a
    YAML file or a submitted form would otherwise silently enable the feature.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "on", "1", "yes")


def _number(raw, field, default, minimum=None, maximum=None, exclusive_minimum=False, integer=False):
    """Coerce one heat setting to a number, raising AnnualHeatError with an actionable message.

    Booleans are rejected explicitly: ``True`` quietly becoming ``1.0`` would model a
    system nobody described. NaN and infinity are rejected too - both are valid YAML.
    With ``integer`` set, a fractional value is rejected rather than silently truncated -
    "one and a half cylinder charges a day" is a mistake worth surfacing.
    """
    if field not in raw or raw[field] is None or raw[field] == "":
        return int(default) if integer else float(default)
    value = raw[field]
    if isinstance(value, bool):
        raise AnnualHeatError("annual.heat.{} must be a number, got {!r}".format(field, value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AnnualHeatError("annual.heat.{} must be a number, got {!r}".format(field, value))
    if not math.isfinite(number):
        raise AnnualHeatError("annual.heat.{} must be a number, got {!r}".format(field, value))
    if integer:
        if number != int(number):
            raise AnnualHeatError("annual.heat.{} must be a whole number, got {}".format(field, number))
        number = int(number)
    if minimum is not None:
        if exclusive_minimum and number <= minimum:
            raise AnnualHeatError("annual.heat.{} must be greater than {}, got {}".format(field, minimum, number))
        if not exclusive_minimum and number < minimum:
            raise AnnualHeatError("annual.heat.{} must be at least {}, got {}".format(field, minimum, number))
    if maximum is not None and number > maximum:
        raise AnnualHeatError("annual.heat.{} must be at most {}, got {}".format(field, maximum, number))
    return number


class HeatModel:
    """A year of heat demand, heat pump electricity and gas cost for one household.

    Built once per run from the whole year's hourly temperatures, because both the
    degree-day distribution and the SCOP calibration are properties of the year as a
    whole rather than of any single sampled day.
    """

    def __init__(self, settings, temperatures, year, log=None):
        """Build the year's heat model from the settings and an hourly temperature series.

        ``temperatures`` maps a local ``date`` to a list of 24 hourly temperatures in
        Celsius, as produced by ``annual_weather.temperature_by_local_day()``. A year
        with no usable temperatures at all is a configuration failure rather than
        something to paper over: every figure this class produces would be fictional.
        """
        self.settings = settings
        self.year = year
        self.log = log or (lambda message: None)
        self.temperatures = temperatures or {}

        if not self.temperatures:
            raise AnnualHeatError("No outdoor temperature data is available for {}, so heat demand cannot be modelled".format(year))

        self.days_in_year = 366 if calendar.isleap(year) else 365
        self.base_temp_c = settings["base_temp_c"]

        self._degree_hours = self._build_degree_hours()
        # Normalised over the MODELLED YEAR only, even though the series deliberately runs
        # past it. The weather download ends on 1 January of the following year so that a
        # sample on 31 December still has the second day its 48 hour plan needs, and
        # counting that day in the denominator would spread the year's heat over 366 days
        # instead of 365 - quietly losing about 0.8% of the space heating, and with it the
        # same slice of the gas bill the whole comparison rests on.
        self._year_degree_hours = sum(sum(hours) for day, hours in self._degree_hours.items() if day.year == year)
        if self._year_degree_hours <= 0:
            raise AnnualHeatError("Every hour of {} was at or above the {}C degree-day base temperature, so there is no space heating to model".format(year, self.base_temp_c))

        # Useful hot water delivered to the taps, which is what the GAS side is billed for.
        self.water_heat_per_day_kwh = settings["water_heat_kwh"] / float(self.days_in_year)
        # What the HEAT PUMP has to put into the cylinder: the same useful hot water plus
        # the new cylinder's own standing loss. The old cylinder's loss is already inside
        # water_boiler_efficiency, so neither side gets its losses for free.
        self.water_charge_per_day_kwh = self.water_heat_per_day_kwh + settings["cylinder_standing_loss_kwh_per_day"]

        # A full charge of the cylinder, from cold mains to its target temperature. Used to
        # tell the user when their configured number of charges a day cannot physically
        # hold the hot water they use.
        self.full_charge_kwh = settings["cylinder_volume_l"] * WATER_SPECIFIC_HEAT_KJ * max(0.0, settings["hot_water_target_c"] - settings["cold_mains_c"]) / SECONDS_PER_HOUR

        # Rates for the plan currently being run, set by the caller via set_plan_rates().
        # None means "no rates known", which is the fixed-schedule fallback.
        self._plan_rates = None
        self._plan_base_day = None

        self.quality = self._calibrate_quality()

        # Scale factor applied to the sampled days of the month currently being run.
        # See set_month_scale() for why this exists.
        self.month_scale = 1.0

    def _build_degree_hours(self):
        """Return per-day lists of 24 heating degree hours, after the overnight setback.

        A degree hour is ``max(0, base - outdoor)`` for that hour. The setback is applied
        here, to the demand, rather than later to the shape, so the day's own total is
        already the post-setback figure and the whole year normalises consistently.
        """
        degree_hours = {}
        for day, hourly in self.temperatures.items():
            values = []
            for hour in range(HOURS_PER_DAY):
                temperature = hourly[hour] if hour < len(hourly) else None
                if temperature is None:
                    values.append(0.0)
                    continue
                deficit = max(0.0, self.base_temp_c - float(temperature))
                if hour in SETBACK_HOURS:
                    deficit *= SETBACK_FACTOR
                values.append(deficit)
            degree_hours[day] = values
        return degree_hours

    def _calibrate_quality(self):
        """Return the Carnot fraction that makes the year's space-heating SCOP match the configured one.

        Closed form, no iteration. With ``COP_i = q * carnot_i`` the realised seasonal
        figure is ``sum(h_i) / sum(h_i / (q * carnot_i))``, which is linear in ``q``, so

            q = SCOP * sum(h_i / carnot_i) / sum(h_i)

        lands it exactly. The clamps in ``space_cop()`` can still move the realised
        figure slightly at the extremes, which is why ``summary()`` reports what was
        actually achieved rather than echoing the configured SCOP back.

        Calibrating on SPACE HEATING ONLY is deliberate: a quoted SCOP is a space-heating
        figure (EN 14825), and hot water is then derived from the same hardware quality at
        its own higher flow temperature. The whole-system SPF that results is lower than
        the configured SCOP, which is the real behaviour of a real installation.
        """
        flow = self.settings["flow_temp_c"]
        weighted_inverse = 0.0
        total_heat = 0.0
        for day, hours in self._degree_hours.items():
            if day.year != self.year:
                continue
            hourly = self.temperatures[day]
            for hour in range(HOURS_PER_DAY):
                heat = hours[hour]
                if heat <= 0:
                    continue
                temperature = hourly[hour] if hour < len(hourly) else self.base_temp_c
                total_heat += heat
                weighted_inverse += heat / carnot_cop(temperature, flow)
        if total_heat <= 0 or weighted_inverse <= 0:
            return 0.5
        return self.settings["scop"] * weighted_inverse / total_heat

    def space_cop(self, outdoor_c):
        """Return the space-heating COP at the given outdoor temperature."""
        return min(MAX_COP, max(MIN_COP, self.quality * carnot_cop(outdoor_c, self.settings["flow_temp_c"])))

    def water_cop(self, outdoor_c):
        """Return the hot-water COP at the given outdoor temperature.

        Same hardware quality as space heating, but against the cylinder's higher flow
        temperature, so it is always the worse of the two.
        """
        return min(MAX_COP, max(MIN_COP, self.quality * carnot_cop(outdoor_c, self.settings["water_flow_temp_c"])))

    def day_degree_hours(self, day):
        """Return the day's total post-setback degree hours, or 0.0 when it is unknown."""
        return sum(self._degree_hours.get(day, []))

    def unscaled_space_heat_kwh(self, day):
        """Return the day's space heat before any monthly sampling correction.

        The day's share of the year's degree hours, times the year's space heat. This is
        the honest figure for that individual date; ``set_month_scale()`` exists to correct
        the separate problem that the sampled days may not represent their month.
        """
        return self.settings["space_heat_kwh"] * self.day_degree_hours(day) / self._year_degree_hours

    def month_space_heat_kwh(self, month):
        """Return the whole month's space heat, computed over every day in it.

        Used both for the reported gas bill and as the target ``set_month_scale()``
        corrects the sampled days onto, so neither depends on which days were sampled.
        """
        total = 0.0
        for day, hours in self._degree_hours.items():
            if day.month == month and day.year == self.year:
                total += sum(hours)
        return self.settings["space_heat_kwh"] * total / self._year_degree_hours

    def month_water_heat_kwh(self, month):
        """Return the whole month's hot water heat."""
        return self.water_heat_per_day_kwh * calendar.monthrange(self.year, month)[1]

    def set_month_scale(self, month, samples):
        """Correct the sampled days so they carry the month's true heat demand, and return the scale.

        The annual tool picks its sample days by IRRADIANCE percentile, not temperature,
        so the sampled days of a month are not a fair temperature sample of it: a month
        sampled on its two sunniest days can be far colder (clear winter skies) or far
        milder than its own average. Weighting those days up unmodified would then carry
        that bias straight into the month's heat demand, and from there into both the gas
        bill and the heat pump's electricity.

        ``samples`` is the ``[(day, weight), ...]`` list the month is about to run, so the
        weighted sum of the sampled days can be compared against the month's own total and
        the difference divided out. A month whose sampled days carry no heat at all (high
        summer) is left at a scale of 1.0 rather than dividing by zero - there is no heat
        to redistribute.
        """
        target = self.month_space_heat_kwh(month)
        sampled = sum(self.unscaled_space_heat_kwh(day) * weight for day, weight in samples)
        if sampled <= 0 or target <= 0:
            self.month_scale = 1.0
        else:
            self.month_scale = target / sampled
        return self.month_scale

    def day_space_heat_kwh(self, day):
        """Return the day's space heat, including the current month's sampling correction."""
        return self.unscaled_space_heat_kwh(day) * self.month_scale

    def set_plan_rates(self, base_day, rate_import):
        """Install the import rates for the plan about to be run, for smart cylinder scheduling.

        ``base_day`` is the local date minute 0 of ``rate_import`` corresponds to, so a
        date later in the plan (the second day of a 48 hour plan) can be offset into the
        same series. Passing ``None`` clears them, which puts the cylinder back on its
        fixed schedule - the correct behaviour when no rates are known, since a smart
        cylinder with no tariff information cannot do better than a timer either.
        """
        self._plan_base_day = base_day
        self._plan_rates = rate_import

    def _rates_for_day(self, day):
        """Return this local date's 1440 import rates from the installed plan rates, or None."""
        if not self._plan_rates or self._plan_base_day is None:
            return None
        offset = (day - self._plan_base_day).days * DAY_MINUTES
        if offset < 0:
            return None
        rates = [self._plan_rates.get(offset + minute) for minute in range(DAY_MINUTES)]
        if any(rate is None for rate in rates):
            return None
        return rates

    def water_charge_windows(self, day):
        """Return the day's cylinder charges as ``(start_minute, end_minute, heat_kwh)``.

        Each configured charge has to be finished by its ready time (``WATER_READY_SCHEDULE``),
        because hot water is only useful once it is already hot. Its length comes from the
        energy it carries and the heat pump's hot-water output, so a bigger draw takes
        longer and has to fit into more of a cheap band.

        With ``smart_hot_water`` set, the charge is placed in the cheapest contiguous window
        that finishes by the ready time - which is what a smart cylinder (Mixergy and
        similar) actually does, and usually the single biggest lever in the comparison.
        Without it, or when no rates are known, the charge simply ends at the ready time,
        which is what a plain timer would give.
        """
        schedule = WATER_READY_SCHEDULE.get(int(self.settings["water_charges_per_day"]), WATER_READY_SCHEDULE[1])
        rates = self._rates_for_day(day) if self.settings["smart_hot_water"] else None

        windows = []
        earliest = WATER_EARLIEST_CHARGE_HOUR * MINUTES_PER_HOUR
        for ready_hour, share in schedule:
            energy = self.water_charge_per_day_kwh * share
            if energy <= 0:
                continue
            ready_minute = ready_hour * MINUTES_PER_HOUR
            duration = max(1, int(math.ceil(energy / self.settings["dhw_kw"] * MINUTES_PER_HOUR)))
            # A charge longer than the whole window it has to fit in still has to happen;
            # it simply starts at the earliest allowed minute and runs on past the others.
            duration = min(duration, max(1, ready_minute - earliest)) if ready_minute > earliest else duration

            if rates is not None:
                start = _cheapest_start(rates, earliest, ready_minute, duration)
            else:
                start = max(earliest, ready_minute - duration)
            windows.append((start, start + duration, energy))
            # The next charge cannot borrow the window this one just used
            earliest = max(earliest, start + duration)
        return windows

    def _water_minute_heat(self, day):
        """Return 1440 per-minute hot water HEAT figures for the given local date."""
        profile = [0.0] * DAY_MINUTES
        for start, end, energy in self.water_charge_windows(day):
            length = max(1, end - start)
            per_minute = energy / length
            for minute in range(start, min(end, DAY_MINUTES)):
                profile[minute] += per_minute
        return profile

    def _space_minute_heat(self, day):
        """Return 1440 per-minute space heating HEAT figures for the given local date."""
        hours = self._degree_hours.get(day)
        if hours is None:
            return [0.0] * DAY_MINUTES
        total = self.day_space_heat_kwh(day)
        degree_total = sum(hours)
        profile = []
        for hour in range(HOURS_PER_DAY):
            hour_heat = (total * hours[hour] / degree_total) if degree_total > 0 else 0.0
            profile.extend([hour_heat / MINUTES_PER_HOUR] * MINUTES_PER_HOUR)
        return profile

    def minute_electricity_kwh(self, day):
        """Return 1440 per-minute heat pump electricity figures for the given local date.

        Space heat follows the hour's own degree hours; hot water follows the cylinder's
        charge windows. Each is divided by its OWN COP at that minute's outdoor
        temperature, so the electrical peak lands where the heat pump is genuinely least
        efficient - and a cylinder charged at 3am is costed at 3am's COP, not the day's
        average, which is part of what makes shifting it a real trade-off rather than a
        free win.
        """
        hourly_temps = self.temperatures.get(day)
        if hourly_temps is None:
            return [0.0] * DAY_MINUTES

        space_heat = self._space_minute_heat(day)
        water_heat = self._water_minute_heat(day)

        profile = []
        for minute in range(DAY_MINUTES):
            hour = minute // MINUTES_PER_HOUR
            temperature = hourly_temps[hour] if hour < len(hourly_temps) else self.base_temp_c
            if temperature is None:
                temperature = self.base_temp_c
            used = 0.0
            if space_heat[minute] > 0:
                used += space_heat[minute] / self.space_cop(temperature)
            if water_heat[minute] > 0:
                used += water_heat[minute] / self.water_cop(temperature)
            profile.append(used)
        return profile

    def hourly_electricity_kwh(self, day):
        """Return 24 hourly heat pump electricity figures, aggregated from the minute profile.

        Derived from ``minute_electricity_kwh`` rather than computed alongside it, so the
        two can never disagree - a cylinder charge that straddles an hour boundary is
        counted once, in the right places.
        """
        minutes = self.minute_electricity_kwh(day)
        return [sum(minutes[hour * MINUTES_PER_HOUR : (hour + 1) * MINUTES_PER_HOUR]) for hour in range(HOURS_PER_DAY)]

    def day_heat_kwh(self, day):
        """Return the day's total delivered heat, space plus hot water."""
        return self.day_space_heat_kwh(day) + self.water_heat_per_day_kwh

    def month_gas(self, month):
        """Return the gas the boiler would have burned this month, and what it would have cost.

        The standing charge is reported separately from the unit cost because whether it
        is actually avoided depends on ``remove_gas_supply`` - a household keeping a gas
        hob still pays it, and it is a large part of a small summer bill.
        """
        days = calendar.monthrange(self.year, month)[1]
        space_heat = self.month_space_heat_kwh(month)
        water_heat = self.month_water_heat_kwh(month)
        heat_kwh = space_heat + water_heat
        # Each is divided by its OWN efficiency. Using a single figure here would break the
        # identity the whole gas side rests on - space gas plus water gas must add back up
        # to exactly the annual_gas_kwh the user typed off their bill.
        gas_kwh = space_heat / self.settings["boiler_efficiency"] + water_heat / self.settings["water_boiler_efficiency"]
        return {
            "heat_kwh": round(heat_kwh, 3),
            "space_heat_kwh": round(self.month_space_heat_kwh(month), 3),
            "water_heat_kwh": round(self.month_water_heat_kwh(month), 3),
            "gas_kwh": round(gas_kwh, 3),
            "gas_unit_cost_p": round(gas_kwh * self.settings["gas_p_per_kwh"], 3),
            "gas_standing_charge_p": round(days * self.settings["gas_standing_charge_p_per_day"], 3),
        }

    def realised_scop(self):
        """Return the space-heating SCOP actually achieved once the COP clamps are applied.

        Reported rather than assumed: the clamps in ``space_cop()`` can pull the realised
        figure away from the configured one at an extreme flow temperature, and quietly
        echoing the configured SCOP back would hide that.
        """
        heat = 0.0
        electricity = 0.0
        for day, hours in self._degree_hours.items():
            if day.year != self.year:
                continue
            hourly = self.temperatures[day]
            for hour in range(HOURS_PER_DAY):
                if hours[hour] <= 0:
                    continue
                temperature = hourly[hour] if hour < len(hourly) else self.base_temp_c
                heat += hours[hour]
                electricity += hours[hour] / self.space_cop(temperature)
        return (heat / electricity) if electricity > 0 else 0.0

    def summary(self):
        """Return the run-level description of the modelled heating system."""
        return {
            "annual_gas_kwh": round(self.settings["annual_gas_kwh"], 3),
            "space_heat_kwh": round(self.settings["space_heat_kwh"], 3),
            "water_heat_kwh": round(self.settings["water_heat_kwh"], 3),
            "boiler_efficiency": self.settings["boiler_efficiency"],
            "gas_p_per_kwh": self.settings["gas_p_per_kwh"],
            "gas_standing_charge_p_per_day": self.settings["gas_standing_charge_p_per_day"],
            "remove_gas_supply": self.settings["remove_gas_supply"],
            "configured_scop": self.settings["scop"],
            "realised_scop": round(self.realised_scop(), 3),
            "carnot_quality": round(self.quality, 4),
            "flow_temp_c": self.settings["flow_temp_c"],
            "water_flow_temp_c": self.settings["water_flow_temp_c"],
            "base_temp_c": self.base_temp_c,
            "water_boiler_efficiency": self.settings["water_boiler_efficiency"],
            "smart_hot_water": self.settings["smart_hot_water"],
            "cylinder_volume_l": self.settings["cylinder_volume_l"],
            "hot_water_target_c": self.settings["hot_water_target_c"],
            "water_charges_per_day": self.settings["water_charges_per_day"],
            "dhw_kw": self.settings["dhw_kw"],
            "cylinder_standing_loss_kwh_per_day": self.settings["cylinder_standing_loss_kwh_per_day"],
            "full_charge_kwh": round(self.full_charge_kwh, 3),
            "water_charge_per_day_kwh": round(self.water_charge_per_day_kwh, 3),
            "days_with_temperature": len(self.temperatures),
        }


def _cheapest_start(rates, earliest, latest_end, duration):
    """Return the start minute of the cheapest contiguous ``duration`` window that ends by ``latest_end``.

    Ties are broken towards the LATEST start, so a charge sits as close as it can to the
    moment the water is wanted. On a flat tariff every window costs the same, which makes
    this fall back to exactly what a timer would do rather than parking the charge at
    midnight and letting it stand and cool for seven hours.

    A window that cannot fit before ``latest_end`` is pinned to ``earliest``, since the
    charge still has to happen.
    """
    span_end = min(latest_end, len(rates))
    if span_end - earliest < duration:
        return earliest

    # A running sum over the window, advanced one minute at a time rather than re-summed
    # per candidate: a 1440 minute day with a multi-hour charge is otherwise a lot of
    # repeated addition for every one of the ~25 sampled days in a run.
    total = sum(rates[earliest : earliest + duration])
    best_total = total
    best_start = earliest
    for start in range(earliest + 1, span_end - duration + 1):
        total += rates[start + duration - 1] - rates[start - 1]
        # <= keeps the LATEST of equally cheap windows, per the docstring
        if total <= best_total:
            best_total = total
            best_start = start
    return best_start


class HeatPumpLoadProfile:
    """A load source that adds a heat pump's electricity on top of another source.

    Wrapping the household's own load source, rather than replacing it, is what lets
    the heat pump leg run through the ordinary four scenarios untouched: every scenario
    sees one combined load series and the battery, PV and planner treat the heat pump as
    the ordinary electrical load it is.
    """

    def __init__(self, base, model):
        """Wrap ``base`` (any LoadProfileSource) with the heat pump load from ``model``."""
        self.base = base
        self.model = model

    def daily_kwh(self, day):
        """Return the day's total household kWh including the heat pump."""
        return self.base.daily_kwh(day) + sum(self.model.hourly_electricity_kwh(day))

    def minute_profile(self, day):
        """Return 1440 per-minute kWh values including the heat pump, or None when the base has none.

        A ``None`` from the base is passed straight through rather than substituted with
        heat-pump-only load: it means the underlying source has no data for that day, and
        ``build_load_forecast`` treats it as a day to skip. Returning heat pump load alone
        would turn a known gap into a day that looks like it had no household load at all.
        """
        base_profile = self.base.minute_profile(day)
        if base_profile is None:
            return None
        heat_profile = self.model.minute_electricity_kwh(day)
        return [base_value + heat_profile[index] for index, base_value in enumerate(base_profile)]
