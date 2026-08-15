# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long

"""Parity tests for the kernel's round_py() against CPython's round().

The kernel mirrors CPython round() exactly - PkResult values and every soc_out
entry are rounded with it, so a single ULP of drift shows up as a plan that
differs from the Python engine. run_kernel_parity_tests covers this end to end,
but only over realistic SoC magnitudes, which is why an earlier implementation
shipped for some time with a snprintf buffer too small for large inputs:
round(1e300, 3) silently returned 1e62 and no test noticed.

These tests drive round_py directly through the pk_round_py_test hook and diff it
bit-for-bit against round(), concentrating on the inputs that break naive
implementations: exact ties, values either side of a rounding boundary, extreme
magnitudes and subnormals.
"""

import ctypes
import math
import os
import struct

import prediction_kernel
from tests.test_kernel_parity import kernel_available

# The two ndigits the hot loop actually uses, plus 2 and 6 which appear elsewhere
# (car SoC / iBoost) and exercise the wide-integer path on 64-bit builds.
NDIGITS = (1, 2, 3, 6)


def _bits(value):
    """Raw bit pattern, so -0.0 and NaN payloads compare exactly rather than by value"""
    return struct.pack("<d", value)


def _round_hook():
    """Return the pk_round_py_test callable, or None when the kernel has no hook"""
    lib = prediction_kernel.KERNEL_LIB
    if lib is None or not hasattr(lib, "pk_round_py_test"):
        return None
    hook = lib.pk_round_py_test
    hook.restype = ctypes.c_double
    hook.argtypes = [ctypes.c_double, ctypes.c_int32]
    return hook


def _candidate_values():
    """Values chosen to break naive decimal rounding, as (value, description) pairs"""
    values = []

    # Ordinary SoC / kWh magnitudes, the range the kernel really works in
    for i in range(0, 1000):
        values.append((i * 0.0317, "soc-range"))

    # Exact ties: j / 2**p lands exactly on a .xxx5 boundary for small p, so these
    # are the only inputs where round-half-to-even is observable at all
    for p in range(1, 21):
        for j in range(1, 60):
            values.append((j / float(1 << p), "exact-tie"))

    # Either side of a decimal boundary - catches an implementation that scales by
    # 10**n in floating point and inherits the scaling error
    for i in range(0, 400):
        base = i / 1000.0
        values.append((base, "boundary"))
        values.append((math.nextafter(base, math.inf), "boundary-above"))
        values.append((math.nextafter(base, -math.inf), "boundary-below"))
        tenth = i / 10.0
        values.append((tenth, "boundary-1dp"))
        values.append((math.nextafter(tenth, math.inf), "boundary-1dp-above"))

    # Halfway at 4dp, i.e. exact ties for ndigits=3
    for i in range(0, 400):
        values.append(((i + 0.5) / 10000.0, "halfway-4dp"))

    # Negatives - rounding must stay symmetric about zero
    for i in range(1, 300):
        values.append((-i * 0.0317, "negative"))

    # Magnitudes far outside the plausible SoC range. These are what the old
    # snprintf("%.*f") implementation got wrong: 1e300 needs 305 characters and a
    # short buffer truncates into an entirely different number.
    for exponent in (12, 13, 15, 16, 20, 40, 62, 63, 64, 100, 200, 300, 308):
        magnitude = float(10**exponent)
        values.append((magnitude, "huge"))
        values.append((-magnitude, "huge-negative"))
        values.append((math.nextafter(magnitude, math.inf), "huge-neighbour"))

    # Tiny values and subnormals, where scaling by 10**n can overflow the shift
    for exponent in (5, 10, 20, 100, 300, 320, 323):
        tiny = float(10**-exponent) if exponent < 308 else 5e-324
        values.append((tiny, "tiny"))
        values.append((-tiny, "tiny-negative"))

    # Specials and known awkward cases
    values.extend(
        [
            (0.0, "zero"),
            (-0.0, "negative-zero"),
            (0.0625, "sixteenth-tie"),
            (-0.0625, "sixteenth-tie-negative"),
            (2.675, "classic-float-surprise"),
            (5e-324, "smallest-subnormal"),
            (float(2**53), "2^53"),
            (float(2**53 + 2), "2^53+2"),
            (1.7976931348623157e308, "dbl-max"),
            (float("inf"), "inf"),
            (float("-inf"), "-inf"),
            (float("nan"), "nan"),
        ]
    )
    return values


def test_round_py_parity(my_predbat):
    """Diff the kernel's round_py against CPython round() bit-for-bit. Returns True on failure."""
    print("**** Running round_py parity tests ****")
    available, required_failure = kernel_available()
    if not available:
        return required_failure

    hook = _round_hook()
    if hook is None:
        # A binary predating the hook cannot be tested; only fail when the kernel is mandatory
        message = "round_py parity: kernel has no pk_round_py_test hook"
        if os.environ.get("PREDBAT_KERNEL_REQUIRED"):
            print("ERROR: {} but the kernel is required".format(message))
            return True
        print("WARNING: {} - SKIPPED".format(message))
        return False

    failed = False
    checked = 0
    mismatches = 0

    for value, description in _candidate_values():
        for ndigits in NDIGITS:
            got = hook(value, ndigits)

            if math.isnan(value):
                # round() propagates NaN; compare by predicate since NaN != NaN
                if not math.isnan(got):
                    print("ERROR: round_py({!r}, {}) [{}] returned {!r}, expected NaN".format(value, ndigits, description, got))
                    mismatches += 1
                    failed = True
                checked += 1
                continue

            expected = round(value, ndigits)
            checked += 1
            if _bits(got) != _bits(expected):
                if mismatches < 10:
                    print("ERROR: round_py({!r}, {}) [{}] returned {!r}, CPython round() gives {!r}".format(value, ndigits, description, got, expected))
                mismatches += 1
                failed = True

    if failed:
        print("ERROR: round_py parity FAILED - {} mismatches out of {} values".format(mismatches, checked))
    else:
        print("round_py parity: {} values match CPython round() exactly".format(checked))
    return failed


def run_round_py_parity_tests(my_predbat):
    """Entry point used by unit_test.py. Returns True on failure."""
    return test_round_py_parity(my_predbat)
