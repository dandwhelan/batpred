# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------

"""Open-Meteo historical weather for the annual prediction tool.

Downloads two archives per PV array: ERA5 reanalysis actuals (what really
happened) and the archived short-range forecast for the same dates (what Predbat
would have been looking at). The gap between them is genuine day-ahead forecast
error, from which each month's P10 ratio is derived.
"""

import math
from datetime import datetime, timedelta

import pytz

from annual_http import fetch_json
from solar_model import convert_azimuth, gti_hourly_to_period_kwh

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_ARCHIVE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

HOURLY_VARIABLES = "global_tilted_irradiance,temperature_2m,wind_speed_10m"

# The heat model needs outdoor temperature and nothing else. Requested on its own rather
# than lifted out of the PV download because a heat pump can be modelled with no solar
# arrays configured at all, in which case there is no PV request to lift it from.
TEMPERATURE_VARIABLE = "temperature_2m"

# A local day must have at least this many of its 24 hours present to be usable. Short
# days are a real occurrence at the edges of the downloaded window and on the spring
# clock change; a day missing more than a few hours would understate its heat demand.
MIN_HOURS_PER_LOCAL_DAY = 20

# A month needs at least this many usable forecast/actual day pairs before its
# measured P10 ratio is trusted over the flat fallback.
MIN_DAYS_FOR_P10 = 7

# Fraction used for the P10 order statistic
P10_FRACTION = 0.10


def percentile(values, fraction):
    """Return the order statistic at ``fraction`` through a list of values.

    Uses the same convention as the Solcast ensemble P10 in ``solcast.py``:
    sort ascending and take index ``ceil(n * fraction) - 1``, clamped to zero.
    Returns 0.0 for an empty list.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


class WeatherYear:
    """A year of per-array-summed PV energy, for both actuals and forecast."""

    def __init__(self, actual_periods, forecast_periods, p10_ratios, forecast_available, fallback_months):
        """Hold the converted period data and the derived monthly P10 ratios."""
        self.actual_periods = actual_periods
        self.forecast_periods = forecast_periods if forecast_available else actual_periods
        self.p10_ratios = p10_ratios
        self.forecast_available = forecast_available
        self.fallback_months = fallback_months
        self._daily_actual = self._daily_totals(self.actual_periods)

    @staticmethod
    def _daily_totals(periods):
        """Sum hourly period energy into per-date totals."""
        totals = {}
        for stamp, kwh in periods.items():
            day = stamp.date()
            totals[day] = totals.get(day, 0.0) + kwh
        return totals

    def has_actual(self, day):
        """Return True when actuals exist for the given date."""
        return day in self._daily_actual

    def daily_actual_kwh(self, day):
        """Return the total actual PV kWh generated on the given date."""
        return self._daily_actual.get(day, 0.0)

    def p10_ratio(self, month):
        """Return the P10 scaling ratio for the given month number, 1 = January."""
        return self.p10_ratios.get(month, 1.0)

    def pv_minutes(self, series, midnight_utc, minutes):
        """Spread hourly period energy across per-minute kWh, keyed by absolute minute.

        ``series`` is "actual" or "forecast". Minutes outside [0, minutes) are
        discarded, so the caller always receives a window it asked for.
        """
        periods = self.actual_periods if series == "actual" else self.forecast_periods
        result = {}
        end_utc = midnight_utc + timedelta(minutes=minutes)
        for stamp, kwh in periods.items():
            if stamp < midnight_utc or stamp >= end_utc:
                continue
            offset = int((stamp - midnight_utc).total_seconds() // 60)
            per_minute = kwh / 60.0
            for minute in range(offset, offset + 60):
                if 0 <= minute < minutes:
                    result[minute] = result.get(minute, 0.0) + per_minute
        return result

    def pv_minutes_p10(self, midnight_utc, minutes, month):
        """Return the P10 per-minute series: the forecast series scaled by the month's ratio."""
        ratio = self.p10_ratio(month)
        return {minute: value * ratio for minute, value in self.pv_minutes("forecast", midnight_utc, minutes).items()}


class AnnualWeather:
    """Fetches and converts a calendar year of Open-Meteo data for one site."""

    def __init__(self, arrays, latitude, longitude, log, storage=None, p10_fallback=0.7, fetch_json=None):
        """Configure the site's PV arrays and the JSON fetcher used for downloads."""
        self.arrays = arrays
        self.latitude = latitude
        self.longitude = longitude
        self.log = log
        self.storage = storage
        self.p10_fallback = p10_fallback
        self.fetch_json = fetch_json or self._default_fetch_json

    async def _default_fetch_json(self, url):
        """Download and decode one JSON document, returning None on any failure."""
        return await fetch_json(url, self.log, "Open-Meteo request", {"accept": "application/json", "user-agent": "predbat/1.0"}, 120)

    def _build_url(self, base, array, year):
        """Build one Open-Meteo request URL for a single array and calendar year.

        The window runs to 1 January of the following year so the final sampled
        day still has the following day its 48 hour plan needs.
        """
        azimuth = array.get("azimuth", 180.0)
        if not array.get("azimuth_zero_south", False):
            azimuth = convert_azimuth(azimuth)
        return "{}?latitude={}&longitude={}&start_date={}-01-01&end_date={}-01-01&hourly={}&tilt={}&azimuth={}&wind_speed_unit=ms&timezone=UTC".format(
            base, self.latitude, self.longitude, year, year + 1, HOURLY_VARIABLES, array.get("declination", 35.0), azimuth
        )

    @staticmethod
    def _payload_problem(data):
        """Return why a payload cannot be trusted, or None when it is complete and usable.

        Shared by the cache-write gate, the cache-read gate and the fetch failure log, so a
        missing, empty or mid-year-truncated response is described identically wherever it
        is encountered, and a poisoned cache entry is described the same way a bad live
        download would be.
        """
        if not data:
            return "no data"
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        gti_values = hourly.get("global_tilted_irradiance", [])
        if not times or not gti_values:
            return "no hourly values"
        if len(gti_values) < len(times):
            return "truncated ({} stamps, {} values)".format(len(times), len(gti_values))
        return None

    async def _fetch_series(self, base, year, cache_tag):
        """Fetch one source for every array and return the summed hourly period energy.

        Every configured array must produce a usable payload for the series to count as
        available. If any array fails or returns an unusable payload, the whole series is
        abandoned (an empty dict) rather than silently blending a partial result for one
        array with complete results for the others, which would understate the true forecast
        error and let the P10 derate collapse to an artificially optimistic value.
        """
        totals = {}
        for index, array in enumerate(self.arrays):
            url = self._build_url(base, array, year)
            cache_key = "weather_{}_{}_{}_{}_{}".format(cache_tag, year, index, self.latitude, self.longitude)
            data = None
            if self.storage:
                cached = await self.storage.load("annual", cache_key)
                if self._payload_problem(cached) is None:
                    data = cached
                # A cache entry written before this guard existed, or poisoned by a past
                # rate-limit/error page, is discarded here and re-fetched below rather than
                # trusted forever.
            if not data:
                fetched = await self.fetch_json(url)
                problem = self._payload_problem(fetched)
                if problem is None:
                    data = fetched
                    if self.storage:
                        await self.storage.save("annual", cache_key, data, format="json")
                else:
                    self.log("Warn: Annual: {} data for array {} is unusable ({}); abandoning the {} series for {}".format(cache_tag, index, problem, cache_tag, year))
                    return {}

            hourly = data["hourly"]
            periods = gti_hourly_to_period_kwh(
                hourly["time"],
                hourly["global_tilted_irradiance"],
                hourly.get("temperature_2m", []),
                hourly.get("wind_speed_10m", []),
                kwp=array.get("kwp", 3.0),
                system_loss=1.0 - array.get("efficiency", 0.95),
                shading_factors=array.get("shading_factors", None),
            )
            for stamp, values in periods.items():
                totals[stamp] = totals.get(stamp, 0.0) + values["pv_estimate"]

        return totals

    def _derive_p10_ratios(self, actual_periods, forecast_periods, forecast_available):
        """Derive each month's P10 ratio from the measured actual/forecast daily energy error."""
        ratios = {}
        fallback_months = set()

        actual_daily = WeatherYear._daily_totals(actual_periods)
        forecast_daily = WeatherYear._daily_totals(forecast_periods)

        by_month = {}
        if forecast_available:
            for day, forecast_kwh in forecast_daily.items():
                if forecast_kwh <= 0:
                    continue
                if day not in actual_daily:
                    continue
                by_month.setdefault(day.month, []).append(actual_daily[day] / forecast_kwh)

        for month in range(1, 13):
            samples = by_month.get(month, [])
            if len(samples) >= MIN_DAYS_FOR_P10:
                ratios[month] = min(1.0, percentile(samples, P10_FRACTION))
            else:
                ratios[month] = self.p10_fallback
                fallback_months.add(month)

        if fallback_months:
            self.log("Warn: Annual: P10 fell back to the flat {} derate for months {}".format(self.p10_fallback, sorted(fallback_months)))

        return ratios, fallback_months

    def _build_temperature_url(self, year):
        """Build the Open-Meteo request for a calendar year of hourly outdoor temperature.

        No tilt or azimuth: temperature is a property of the site, not of any array, so
        this is a single request regardless of how many arrays are configured - or
        whether any are.
        """
        return "{}?latitude={}&longitude={}&start_date={}-01-01&end_date={}-01-01&hourly={}&timezone=UTC".format(ARCHIVE_URL, self.latitude, self.longitude, year, year + 1, TEMPERATURE_VARIABLE)

    @staticmethod
    def _temperature_payload_problem(data):
        """Return why a temperature payload cannot be trusted, or None when it is usable."""
        if not data:
            return "no data"
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        values = hourly.get(TEMPERATURE_VARIABLE, [])
        if not times or not values:
            return "no hourly values"
        if len(values) < len(times):
            return "truncated ({} stamps, {} values)".format(len(times), len(values))
        return None

    async def fetch_temperatures(self, year):
        """Download a calendar year of hourly outdoor temperature, keyed by tz-aware UTC hour.

        Returns an empty dict when the download is unusable, leaving the caller to decide
        whether that is fatal - it is for a heat pump run, and irrelevant for one without.
        Cached like the PV series, so a repeated run costs no request.
        """
        cache_key = "temperature_{}_{}_{}".format(year, self.latitude, self.longitude)
        data = None
        if self.storage:
            cached = await self.storage.load("annual", cache_key)
            if self._temperature_payload_problem(cached) is None:
                data = cached
        if not data:
            fetched = await self.fetch_json(self._build_temperature_url(year))
            problem = self._temperature_payload_problem(fetched)
            if problem is not None:
                self.log("Warn: Annual: outdoor temperature data for {} is unusable ({})".format(year, problem))
                return {}
            data = fetched
            if self.storage:
                await self.storage.save("annual", cache_key, data, format="json")

        hourly = data["hourly"]
        series = {}
        for index, stamp_text in enumerate(hourly["time"]):
            value = hourly[TEMPERATURE_VARIABLE][index]
            if value is None:
                continue
            try:
                stamp = datetime.strptime(stamp_text, "%Y-%m-%dT%H:%M").replace(tzinfo=pytz.utc)
            except (ValueError, TypeError):
                continue
            series[stamp] = float(value)
        return series

    async def fetch(self, year):
        """Download and convert a calendar year, returning a populated WeatherYear."""
        actual_periods = await self._fetch_series(ARCHIVE_URL, year, "actual")
        forecast_periods = await self._fetch_series(FORECAST_ARCHIVE_URL, year, "forecast")
        forecast_available = bool(forecast_periods)

        if not forecast_available:
            self.log("Warn: Annual: the Open-Meteo forecast archive returned nothing for {}; planning on actuals with the flat P10 derate".format(year))

        ratios, fallback_months = self._derive_p10_ratios(actual_periods, forecast_periods, forecast_available)
        return WeatherYear(actual_periods, forecast_periods, ratios, forecast_available, fallback_months)


def temperature_by_local_day(series, timezone):
    """Regroup a UTC-keyed hourly temperature series into 24 values per LOCAL date.

    The heat model works in local days because the load profile does: minute 0 of a
    ``load_forecast`` is local midnight, so hour 0 of a day's heat demand has to be
    local midnight too. Feeding it a UTC-keyed series would shift the whole heating
    profile by the UTC offset - an hour in British Summer Time, which lands the evening
    peak in the wrong half-hourly rate slot.

    Both clock changes are handled rather than discarded. In autumn two UTC hours map
    onto the same local hour, so they are averaged; in spring one local hour never
    happens, so it is filled from its neighbours along with any other isolated gap.
    A local day still short of ``MIN_HOURS_PER_LOCAL_DAY`` real readings is dropped
    entirely - that is the first and last day of the downloaded window, which only ever
    hold a partial day, and a day filled mostly from guesses would understate its heat.
    """
    zone = pytz.timezone(timezone)
    collected = {}
    for stamp, value in series.items():
        local = stamp.astimezone(zone)
        collected.setdefault(local.date(), {}).setdefault(local.hour, []).append(value)

    by_day = {}
    for day, hours in collected.items():
        if len(hours) < MIN_HOURS_PER_LOCAL_DAY:
            continue
        averaged = {hour: sum(values) / len(values) for hour, values in hours.items()}
        by_day[day] = _fill_missing_hours(averaged)
    return by_day


def _fill_missing_hours(averaged):
    """Return 24 hourly values, filling any gap from the nearest hour that has one.

    Only ever called for a day already known to hold at least
    ``MIN_HOURS_PER_LOCAL_DAY`` real readings, so a filled hour is always surrounded by
    real ones - this is interpolation across a clock change or an isolated dropout, not
    invention of a day that was never downloaded.
    """
    present = sorted(averaged)
    filled = []
    for hour in range(24):
        if hour in averaged:
            filled.append(averaged[hour])
            continue
        nearest = min(present, key=lambda candidate: abs(candidate - hour))
        filled.append(averaged[nearest])
    return filled


POSTCODE_URL = "https://api.postcodes.io/postcodes/{}"


async def resolve_postcode(postcode, fetch_json, log):
    """Resolve a UK postcode to (latitude, longitude), or None when it cannot be resolved."""
    data = await fetch_json(POSTCODE_URL.format(postcode))
    result = (data or {}).get("result", {}) if isinstance(data, dict) else {}
    if "latitude" in result and "longitude" in result:
        log("Annual: postcode {} resolved to latitude {} longitude {}".format(postcode, result["latitude"], result["longitude"]))
        return result["latitude"], result["longitude"]
    log("Warn: Annual: postcode {} could not be resolved".format(postcode))
    return None
