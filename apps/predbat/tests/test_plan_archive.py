# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init

"""Tests for the plan archive (plan_archive.py): saving, listing, loading and expiring past plans and
tariff comparisons, the hooks that feed it, and the web routes that read it back."""

import asyncio
import gzip
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytz

from compare import Compare
from plan_archive import PlanArchive, snapshot_label, snapshot_day
from web import WebInterface


class FakeRequest:
    """A minimal aiohttp-request stand-in exposing only what the handlers read."""

    def __init__(self, query=None):
        self.query = query or {}


def _make_web(my_predbat):
    """A WebInterface bound to my_predbat without standing up the aiohttp app (see test_web_debug_history_routes.py)"""
    w = WebInterface.__new__(WebInterface)
    w.base = my_predbat
    w.log = my_predbat.log
    w.prefix = my_predbat.prefix
    return w


def _test_save_list_load_prune(tmpdir, log):
    failed = False
    tz = pytz.timezone("Europe/London")
    archive = PlanArchive(tmpdir, log)

    # Nothing saved yet - an archive that has never written must read as empty, not fail
    if archive.list("plan") != [] or archive.load("plan", "20261019-0930") is not None:
        print("  ERROR: an empty archive should list nothing and load None")
        failed = True

    base = tz.localize(datetime(2026, 10, 15, 0, 0))
    for hours in range(0, 5 * 24, 6):
        archive.save("plan", base + timedelta(hours=hours), {"rows": [hours], "timestamp": "x"})
    archive.save("compare", base, {"results": {"igo_agile": {"cost": 41.86}}})

    plans = archive.list("plan")
    if len(plans) != 20 or plans[0] != "20261019-1800" or plans[-1] != "20261015-0000":
        print("  ERROR: expected 20 plan ids newest first, got {}".format(plans))
        failed = True
    if archive.list("compare") != ["20261015-0000"]:
        print("  ERROR: compare snapshots should be listed apart from plans, got {}".format(archive.list("compare")))
        failed = True

    snapshot = archive.load("plan", "20261017-1200")
    if not snapshot or snapshot.get("data", {}).get("rows") != [60] or not snapshot.get("time", "").startswith("2026-10-17T12:00:00"):
        print("  ERROR: loaded snapshot wrong: {}".format(snapshot))
        failed = True

    # The id comes from a web request - nothing but the exact id shape may reach the filesystem
    for bad in ["../secrets", "20261017-1200/../../x", "20261017-120", "", None, 20261017]:
        if archive.load("plan", bad) is not None:
            print("  ERROR: load accepted a malformed id {!r}".format(bad))
            failed = True
    if archive.load("secrets", "20261017-1200") is not None:
        print("  ERROR: load accepted an unknown kind")
        failed = True

    # Expire everything more than four days before 19 Oct 18:00
    removed = archive.prune(tz.localize(datetime(2026, 10, 19, 18, 0)), 4)
    plans = archive.list("plan")
    if plans[-1] != "20261015-1800" or removed != 4 or archive.list("compare") != []:
        print("  ERROR: prune should remove the 3 plans before 15 Oct 18:00 and the 15 Oct 00:00 compare, removed {}, oldest left {}".format(removed, plans[-1]))
        failed = True

    # A truncated file is reported and skipped, not raised
    path = os.path.join(archive.directory, "plan_20261019-1800.json.gz")
    with open(path, "wb") as handle:
        handle.write(gzip.compress(b'{"time": "x", "data": {')[:20])
    if archive.load("plan", "20261019-1800") is not None:
        print("  ERROR: a corrupt snapshot should load as None")
        failed = True
    return failed


def _test_save_plan_once_per_slot(tmpdir, log):
    failed = False
    tz = pytz.timezone("Europe/London")
    archive = PlanArchive(tmpdir, log)
    start = tz.localize(datetime(2026, 10, 19, 9, 0))
    saved = [archive.save_plan(start + timedelta(minutes=minutes), {"m": minutes}, 30) for minutes in (0, 15, 29, 30, 45, 60)]
    if saved != ["20261019-0900", None, None, "20261019-0930", None, "20261019-1000"]:
        print("  ERROR: expected one plan per 30 minute slot, the first in each, got {}".format(saved))
        failed = True
    if archive.load("plan", "20261019-0900")["data"] != {"m": 0}:
        print("  ERROR: a later re-plan in the same slot must not replace the slot's first plan")
        failed = True
    return failed


def _test_blocked_directory(tmpdir, log):
    failed = False
    # A file where the archive directory should be: every save fails, and must say so without raising
    blocker = os.path.join(tmpdir, "blocked")
    with open(blocker, "w") as handle:
        handle.write("x")
    messages = []
    archive = PlanArchive(blocker, lambda message: messages.append(message))
    if archive.save("plan", pytz.utc.localize(datetime(2026, 10, 19, 9, 0)), {}) is not None:
        print("  ERROR: a save that cannot be written should return None")
        failed = True
    if not any("could not save" in message for message in messages):
        print("  ERROR: a failed save should be logged, got {}".format(messages))
        failed = True
    return failed


def _test_archive_plan_hook(my_predbat, tmpdir):
    """output.archive_plan() saves the live plan and honours plan_archive_days"""
    failed = False
    saved_archive = getattr(my_predbat, "plan_archive", None)
    saved_now = my_predbat.now_utc
    saved_args = dict(my_predbat.args)
    try:
        my_predbat.plan_archive = PlanArchive(tmpdir, my_predbat.log)
        my_predbat.now_utc = pytz.timezone("Europe/London").localize(datetime(2026, 10, 19, 9, 5))
        my_predbat.args["plan_archive_days"] = 4
        my_predbat.archive_plan({"rows": [], "timestamp": "2026-10-19T09:05:00+01:00"})
        my_predbat.archive_plan({"rows": [1], "timestamp": "2026-10-19T09:10:00+01:00"})
        if my_predbat.plan_archive.list("plan") != ["20261019-0905"]:
            print("  ERROR: archive_plan should save once per slot, got {}".format(my_predbat.plan_archive.list("plan")))
            failed = True

        my_predbat.args["plan_archive_days"] = 0
        my_predbat.now_utc = my_predbat.now_utc + timedelta(hours=1)
        my_predbat.archive_plan({"rows": [2]})
        if len(my_predbat.plan_archive.list("plan")) != 1:
            print("  ERROR: plan_archive_days 0 should turn the archive off")
            failed = True

        # Without an archive (tests, the annual tool's headless PredBat) nothing is written and nothing raises
        my_predbat.plan_archive = None
        my_predbat.args["plan_archive_days"] = 4
        my_predbat.archive_plan({"rows": [3]})
    finally:
        my_predbat.plan_archive = saved_archive
        my_predbat.now_utc = saved_now
        my_predbat.args.clear()
        my_predbat.args.update(saved_args)
    return failed


def _test_compare_archive_results(my_predbat, tmpdir):
    """Compare.archive_results() keeps each run; called on a stand-in so no real comparisons.yaml is touched"""
    failed = False
    saved_archive = getattr(my_predbat, "plan_archive", None)
    saved_now = my_predbat.now_utc
    try:
        my_predbat.plan_archive = PlanArchive(tmpdir, my_predbat.log)
        my_predbat.now_utc = pytz.timezone("Europe/London").localize(datetime(2026, 10, 19, 0, 1))
        stand_in = SimpleNamespace(pb=my_predbat)
        results = {"igo_agile": {"name": "Intelligent GO import/Agile export", "cost": -117.34, "html": "<table></table>"}}
        Compare.archive_results(stand_in, results)
        Compare.archive_results(stand_in, {})
        runs = my_predbat.plan_archive.list("compare")
        if runs != ["20261019-0001"]:
            print("  ERROR: expected one archived run, got {}".format(runs))
            failed = True
        elif my_predbat.plan_archive.load("compare", runs[0])["data"]["results"] != results:
            print("  ERROR: archived run does not round-trip its results")
            failed = True
    finally:
        my_predbat.plan_archive = saved_archive
        my_predbat.now_utc = saved_now
    return failed


def _test_web_routes(my_predbat, tmpdir):
    """/api/plan_archive lists and returns snapshots, and 404s anything it does not have"""
    failed = False
    saved_archive = getattr(my_predbat, "plan_archive", None)
    try:
        w = _make_web(my_predbat)

        my_predbat.plan_archive = None
        resp = asyncio.run(w.html_api_plan_archive(FakeRequest()))
        body = json.loads(resp.text)
        if resp.status != 200 or body != {"enabled": False, "plans": [], "compares": []}:
            print("  ERROR: with no archive expected an empty listing, got {} {}".format(resp.status, body))
            failed = True

        archive = PlanArchive(tmpdir, my_predbat.log)
        my_predbat.plan_archive = archive
        when = pytz.timezone("Europe/London").localize(datetime(2026, 10, 18, 17, 30))
        archive.save("plan", when, {"rows": [{"time": "2026-10-18T17:30:00+0100"}], "timestamp": when.isoformat()})
        archive.save("compare", when, {"results": {}})

        body = json.loads(asyncio.run(w.html_api_plan_archive(FakeRequest())).text)
        if body != {"enabled": True, "plans": ["20261018-1730"], "compares": ["20261018-1730"]}:
            print("  ERROR: listing wrong: {}".format(body))
            failed = True

        resp = asyncio.run(w.html_api_plan_archive(FakeRequest({"kind": "plan", "id": "20261018-1730"})))
        if resp.status != 200 or json.loads(resp.text)["data"]["rows"][0]["time"] != "2026-10-18T17:30:00+0100":
            print("  ERROR: fetching a saved plan failed: {} {}".format(resp.status, resp.text[:200]))
            failed = True

        for query in [{"kind": "plan", "id": "20261018-1800"}, {"kind": "plan", "id": "../../secrets"}, {"kind": "secrets", "id": "20261018-1730"}]:
            resp = asyncio.run(w.html_api_plan_archive(FakeRequest(query)))
            if resp.status != 404:
                print("  ERROR: expected 404 for {}, got {}".format(query, resp.status))
                failed = True

        options, count = w.saved_snapshot_options("plan", selected="20261018-1730")
        if count != 1 or 'value="20261018-1730" selected' not in options or "Sun 18 Oct" not in options:
            print("  ERROR: plan picker options wrong: {}".format(options))
            failed = True
    finally:
        my_predbat.plan_archive = saved_archive
    return failed


def _test_debug_yaml_skips_archive(my_predbat, tmpdir):
    """
    The plan archive must stay out of the debug yaml.

    create_debug_yaml() dumps every non-callable PredBat member not in DEBUG_EXCLUDE_LIST. The archive
    holds my_predbat.log, a bound method, so yaml.dump() followed it into the whole PredBat graph and
    died on the first in-flight coroutine there - "Failed to capture debug history snapshot: cannot
    pickle 'coroutine' object" on the first live run of v712.38. A pending coroutine is planted on
    ha_interface below, as on a live system, so dropping "plan_archive" from the exclude list fails this.
    """
    failed = False

    async def _pending():
        """A stand-in for an in-flight component call."""
        return None

    coro = _pending()
    saved_archive = getattr(my_predbat, "plan_archive", None)
    try:
        my_predbat.plan_archive = PlanArchive(tmpdir, my_predbat.log)
        my_predbat.ha_interface.plan_archive_test_pending_call = coro
        try:
            text = my_predbat.create_debug_yaml(write_file=False)
        except Exception as e:
            print("  ERROR: create_debug_yaml raised {}: {}".format(type(e).__name__, e))
            return True
        if "plan_archive:" in text:
            print("  ERROR: the plan archive was dumped into the debug yaml")
            failed = True
    finally:
        my_predbat.plan_archive = saved_archive
        my_predbat.ha_interface.__dict__.pop("plan_archive_test_pending_call", None)
        coro.close()
    return failed


def test_plan_archive(my_predbat):
    """Run the plan archive tests"""
    print("**** Testing plan archive ****")
    failed = False

    if snapshot_label("20261019-0930") != "Mon 19 Oct 09:30" or snapshot_day("20261019-0930") != "Mon 19 Oct" or snapshot_label("bad") != "bad":
        print("  ERROR: snapshot labels wrong: {} / {}".format(snapshot_label("20261019-0930"), snapshot_day("20261019-0930")))
        failed = True

    for name, test in [
        ("save/list/load/prune", lambda d: _test_save_list_load_prune(d, my_predbat.log)),
        ("one plan per slot", lambda d: _test_save_plan_once_per_slot(d, my_predbat.log)),
        ("blocked directory", lambda d: _test_blocked_directory(d, my_predbat.log)),
        ("archive_plan hook", lambda d: _test_archive_plan_hook(my_predbat, d)),
        ("compare results hook", lambda d: _test_compare_archive_results(my_predbat, d)),
        ("web routes", lambda d: _test_web_routes(my_predbat, d)),
        ("debug yaml skips the archive", lambda d: _test_debug_yaml_skips_archive(my_predbat, d)),
    ]:
        tmpdir = tempfile.mkdtemp(prefix="predbat_test_plan_archive_")
        try:
            print("Test: {}".format(name))
            if test(tmpdir):
                failed = True
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    if failed:
        print("**** Plan archive tests FAILED ****")
    else:
        print("**** Plan archive tests PASSED ****")
    return failed
