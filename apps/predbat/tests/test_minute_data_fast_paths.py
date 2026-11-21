# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init
"""Tests pinning the faster history paths to the behaviour of the loops they replaced.

minute_data, clean_incrementing_reverse, MinuteArray and get_filtered_load_window run over every minute of every
history series on each five-minute cycle, so they were rewritten for speed: clamped ranges instead of a bounds
check per minute, the window's first record found from the keys rather than by scanning, flags hoisted out of
loops. None of that may change a result, down to dict key order and NaN handling, so the helpers are fuzzed
against verbatim copies of the old implementations, and minute_data's fill paths are checked case by case.
"""

import math
import random
from datetime import datetime, timedelta

import pytz

from utils import MinuteArray, clean_incrementing_reverse, minute_data

UTC = pytz.UTC
NOW = datetime(2024, 10, 4, 12, 0, 0, tzinfo=UTC)


def reference_clean_incrementing_reverse(data, max_increment=0):
    """clean_incrementing_reverse as it was before the rewrite, kept as the oracle"""
    new_data = {}
    if not data:
        return new_data
    length = max(data) + 1

    increment = 0
    last = data[length - 1]

    for index in range(length):
        rindex = length - index - 1
        nxt = data.get(rindex, last)
        if nxt >= last:
            if (max_increment > 0) and ((nxt - last) > max_increment):
                pass
            else:
                increment += nxt - last
            last = nxt
        elif nxt < last:
            if nxt <= 0 or ((last - nxt) >= (1.0)):
                last = nxt
        new_data[rindex] = increment

    return new_data


def reference_get_filtered_load_window(base, data, indices, step):
    """Fetch.get_filtered_load_window as it was before the rewrite, kept as the oracle"""
    raw = 0.0
    subtract_energy = 0.0
    for idx in indices:
        raw += base.get_from_incrementing(data, idx)
        if base.car_charging_hold and base.car_charging_energy:
            subtract_energy += base.get_from_incrementing(base.car_charging_energy, idx)
        if base.iboost_energy_subtract and base.iboost_energy_today:
            subtract_energy += base.get_from_incrementing(base.iboost_energy_today, idx)
    load = max(0.0, raw - subtract_energy)
    if base.car_charging_hold and (not base.car_charging_energy) and (load >= (base.car_charging_threshold * step)):
        load = max(load - (base.car_charging_rate[0] * step / 60.0), 0)
    return load, raw


def same(a, b):
    """True when two results match exactly, NaN included, and dicts also match in key order"""
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(same(a[k], b[k]) for k in a)
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b and type(a) is type(b)


def random_series(rng, length):
    """A noisy backwards cumulative series with resets, spikes, small dips, gaps and the odd NaN"""
    data = {}
    value = rng.uniform(0, 50)
    for minute in range(length - 1, -1, -1):
        roll = rng.random()
        if roll < 0.03:
            continue  # gap
        if roll < 0.05:
            value = 0.0  # reset
        elif roll < 0.07:
            value += rng.uniform(5, 40)  # spike
        elif roll < 0.10:
            value -= rng.uniform(0, 1.5)  # dip, some below the 1.0 threshold
        elif roll < 0.105:
            data[minute] = float("nan")
            continue
        else:
            value += rng.uniform(0, 0.2)
        data[minute] = value
    return data


def test_clean_incrementing_reverse_matches_the_old_loop():
    """Fuzz clean_incrementing_reverse against the old loop, with and without a spike limit"""
    print("**** test_clean_incrementing_reverse_matches_the_old_loop ****")
    rng = random.Random(1234)
    for trial in range(300):
        data = random_series(rng, rng.randint(1, 400))
        if not data:
            continue
        for max_increment in (0, 1.0, 10.0):
            expect = reference_clean_incrementing_reverse(dict(data), max_increment)
            got = clean_incrementing_reverse(dict(data), max_increment)
            if not same(got, expect):
                print("ERROR: trial {} max_increment {} differs from the old loop".format(trial, max_increment))
                return True
    # NaN as the newest value: both comparisons against it fail, so the old loop never followed a drop from it
    data = {0: float("nan"), 1: -1.0, 2: 3.0}
    if not same(clean_incrementing_reverse(dict(data)), reference_clean_incrementing_reverse(dict(data))):
        print("ERROR: a NaN reading changed which drops are followed")
        return True
    print("PASS")
    return False


def test_minute_array_matches_the_dict():
    """MinuteArray holds data[i] for every i below size, 0.0 where the dict has no entry"""
    print("**** test_minute_array_matches_the_dict ****")
    data = {0: 1.5, 2: 2.5, 5: 7.0, 9: 1.0}
    array = MinuteArray(data, 8)
    expect = [1.5, 0.0, 2.5, 0.0, 0.0, 7.0, 0.0, 0.0]
    got = [array[i] for i in range(len(array))]
    if got != expect:
        print("ERROR: expected {} got {}".format(expect, got))
        return True
    if array.get(8, -1) != -1 or array.get(-1, -1) != -1:
        print("ERROR: out of range reads should return the default")
        return True
    print("PASS")
    return False


def test_filtered_load_window_matches_the_old_loop(my_predbat):
    """Fuzz get_filtered_load_window against the old loop across the car and iBoost subtraction flags"""
    print("**** test_filtered_load_window_matches_the_old_loop ****")
    rng = random.Random(99)
    saved = {name: getattr(my_predbat, name, None) for name in ("car_charging_hold", "car_charging_energy", "iboost_energy_subtract", "iboost_energy_today", "car_charging_threshold", "car_charging_rate")}
    failed = False
    try:
        for trial in range(200):
            day = 24 * 60
            data = random_series(rng, day + 2)
            my_predbat.car_charging_hold = rng.random() < 0.5
            my_predbat.car_charging_energy = random_series(rng, day + 2) if rng.random() < 0.5 else {}
            my_predbat.iboost_energy_subtract = rng.random() < 0.5
            my_predbat.iboost_energy_today = random_series(rng, day + 2) if rng.random() < 0.5 else {}
            my_predbat.car_charging_threshold = rng.choice([0.0, 0.05, 6.0])
            my_predbat.car_charging_rate = [rng.choice([0.0, 7.4])]
            step = rng.choice([1, 5, 30])
            start = rng.randint(-2 * day, day)  # negative indices wrap into the day
            indices = [start + offset for offset in range(step)]
            expect = reference_get_filtered_load_window(my_predbat, data, indices, step)
            got = my_predbat.get_filtered_load_window(data, indices, step)
            if not same(got, expect):
                print("ERROR: trial {} expected {} got {}".format(trial, expect, got))
                failed = True
                break
    finally:
        for name, value in saved.items():
            setattr(my_predbat, name, value)
    if not failed:
        print("PASS")
    return failed


def stamp(minutes_ago):
    """An ISO timestamp minutes_ago before NOW"""
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def test_short_history_is_padded_only_when_asked():
    """A backwards window longer than the history: pad fills the older minutes, pad=False leaves them out"""
    print("**** test_short_history_is_padded_only_when_asked ****")
    failed = False
    history = [{"state": "5.0", "last_updated": stamp(120)}, {"state": "6.0", "last_updated": stamp(60)}, {"state": "7.0", "last_updated": stamp(0)}]

    padded, _ = minute_data(history, 1, NOW, "state", "last_updated", backwards=True, smoothing=False, pad=True)
    unpadded, _ = minute_data(history, 1, NOW, "state", "last_updated", backwards=True, smoothing=False, pad=False)

    # The window is minutes 0 .. 24*60-1
    if len(padded) != 24 * 60 or padded.get(24 * 60 - 1) != 5.0:
        print("ERROR: pad should fill the whole day with the oldest state, got {} minutes, oldest {}".format(len(padded), padded.get(24 * 60 - 1)))
        failed = True
    if max(unpadded) != 120 or len(unpadded) != 121:
        print("ERROR: pad=False should stop at the oldest record (minute 120), got {} minutes up to {}".format(len(unpadded), max(unpadded)))
        failed = True
    if unpadded.get(90) != 5.0 or unpadded.get(30) != 6.0 or unpadded.get(0) != 7.0:
        print("ERROR: gaps should hold the state in force at the time, got {}".format((unpadded.get(90), unpadded.get(30), unpadded.get(0))))
        failed = True
    # Key order is part of the result: the history loop's writes come first, then the fill, oldest minute first
    if list(unpadded)[:3] != [120, 60, 61] or list(padded)[-3:] != [123, 122, 121]:
        print("ERROR: unexpected key order {} / {}".format(list(unpadded)[:3], list(padded)[-3:]))
        failed = True

    if not failed:
        print("PASS")
    return failed


def test_forward_smoothing_clipped_at_the_window_edge():
    """Forward interpolation that runs past the window keeps counting from the span's own first minute"""
    print("**** test_forward_smoothing_clipped_at_the_window_edge ****")
    failed = False
    days = 1
    end = days * 24 * 60
    # Two spans: flat at 0 until 30 minutes before the window ends, then rising to 60 over the hour that straddles the end
    history = [
        {"state": "0.0", "start": (NOW + timedelta(minutes=end - 90)).isoformat(), "end": (NOW + timedelta(minutes=end - 30)).isoformat()},
        {"state": "60.0", "start": (NOW + timedelta(minutes=end - 30)).isoformat(), "end": (NOW + timedelta(minutes=end + 30)).isoformat()},
    ]
    data, _ = minute_data(history, days, NOW, "state", "start", backwards=False, smoothing=True, to_key="end")
    # 1 per minute from 0 at end-30: minute end-1 is 29, and the last minute stored (the window's bound, end) is 30
    for minute, expect in ((end - 90, 0.0), (end - 30, 0.0), (end - 1, 29.0), (end, 30.0)):
        if data.get(minute) != expect:
            print("ERROR: minute {} expected {} got {}".format(minute, expect, data.get(minute)))
            failed = True
    # The same at the window's start: a span rising 1 per minute from minute -end-30 is 30 by the first minute kept (-end),
    # so the interpolation counts from the span's start, not from the first minute inside the window
    history = [
        {"state": "0.0", "start": (NOW + timedelta(minutes=-end - 60)).isoformat(), "end": (NOW + timedelta(minutes=-end - 30)).isoformat()},
        {"state": "60.0", "start": (NOW + timedelta(minutes=-end - 30)).isoformat(), "end": (NOW + timedelta(minutes=-end + 30)).isoformat()},
    ]
    early, _ = minute_data(history, days, NOW, "state", "start", backwards=False, smoothing=True, to_key="end")
    if min(early) != -end or early.get(-end) != 30.0 or early.get(-end + 29) != 59.0:
        print("ERROR: clipped at the window start, expected 30.0 at {} and 59.0 at {}, got {} / {} (first key {})".format(-end, -end + 29, early.get(-end), early.get(-end + 29), min(early)))
        failed = True
    if any(k > end for k in data):
        print("ERROR: minutes past the window were stored: {}".format(sorted(k for k in data if k > end)[:5]))
        failed = True

    if not failed:
        print("PASS")
    return failed


def run_minute_data_fast_paths_tests(my_predbat):
    """Run the fast-path equivalence tests for the history helpers"""
    failed = False
    failed |= test_clean_incrementing_reverse_matches_the_old_loop()
    failed |= test_minute_array_matches_the_dict()
    failed |= test_filtered_load_window_matches_the_old_loop(my_predbat)
    failed |= test_short_history_is_padded_only_when_asked()
    failed |= test_forward_smoothing_clipped_at_the_window_edge()
    return failed
