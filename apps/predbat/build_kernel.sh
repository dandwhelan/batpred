#!/bin/bash
# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# -----------------------------------------------------------------------------
# Build the C++ prediction kernel as a shared library for local testing or
# inside the addon Docker image. Falls back to the Python engine when absent.
#
# Usage: bash apps/predbat/build_kernel.sh [output.so]
#
# EXTRA_CXXFLAGS appends compiler flags, which CI uses to build the -DPK_NO_INT128
# variant so the narrow-integer rounding path that ships to the 32-bit targets gets
# exercised on a 64-bit runner (see code-quality.yml).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
# Note: the library must NOT be named prediction_kernel.so or Python's importer
# would pick it up instead of prediction_kernel.py
OUT="${1:-$DIR/prediction_kernel_lib.so}"
CXX="${CXX:-g++}"
# -ffp-contract=off: no fused multiply-add, so floating point results match the
# Python engine bit-for-bit (CPython never fuses operations)
# shellcheck disable=SC2086
"$CXX" -std=c++17 -O2 -shared -fPIC -fno-fast-math -ffp-contract=off -Wall -Werror $EXTRA_CXXFLAGS -o "$OUT" "$DIR/prediction_kernel.cpp"
echo "Built $OUT"
