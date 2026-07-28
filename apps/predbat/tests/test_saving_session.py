# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init
import asyncio
import yaml
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from octopus import OctopusAPI


def test_saving_session(my_predbat):
    """
    Test the octopus saving session
    """
    print("Test saving session")
    ha = my_predbat.ha_interface
    failed = False
    date_last_year = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    date_yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    date_before_yesterday = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    date_today = datetime.now().strftime("%Y-%m-%d")
    tz_offset = int(my_predbat.midnight_utc.tzinfo.utcoffset(my_predbat.midnight_utc).total_seconds() / 3600)
    tz_offset = f"{tz_offset:02d}"
    session_binary = f"""

state: off
current_joined_event_start: '{date_today}T16:30:00+{tz_offset}:00'
current_joined_event_end: '{date_today}T17:30:00+{tz_offset}:00'
current_joined_event_duration_in_minutes: 60
next_joined_event_start: null
next_joined_event_end: null
next_joined_event_duration_in_minutes: null
icon: mdi:leaf
friendly_name: Octoplus Saving Session (A-4DD6C5EE)
""".format(
        date_last_year=date_last_year, date_yesterday=date_yesterday, date_today=date_today, date_before_yesterday=date_before_yesterday, tz_offset=tz_offset
    )

    session_sensor = f"""
state: '2025-01-23T12:10:11.108+{tz_offset}:00'
event_types: octopus_energy_all_octoplus_saving_sessions
event_type: octopus_energy_all_octoplus_saving_sessions
account_id: A-4DD6C5EE
available_events:
    - id: 1336
      start: '{date_today}T18:30:00+{tz_offset}:00'
      end: '{date_today}T19:30:00+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: null
      octopoints_per_kwh: 500
      code: 987654
joined_events:
    - id: 1327
      start: '{date_last_year}T17:00:00+{tz_offset}:00'
      end: '{date_last_year}T18:00:00+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: 936
      octopoints_per_kwh: 576
    - id: 1334
      start: '{date_yesterday}T17:30:00+{tz_offset}:00'
      end: '{date_yesterday}T18:30:00+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: null
      octopoints_per_kwh: 192
    - id: 1335
      start: '{date_today}T16:30:00+{tz_offset}:00'
      end: '{date_today}T17:30:00+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: null
      octopoints_per_kwh: 448
    - id: 1336
      start: '{date_before_yesterday}T23:30:00+{tz_offset}:00'
      end: '{date_yesterday}T10:30:00+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: null
      octopoints_per_kwh: 448
friendly_name: Octoplus Saving Session Events (A-12345678)
""".format(
        date_last_year=date_last_year, date_yesterday=date_yesterday, date_today=date_today, tz_offset=tz_offset
    )
    ha.dummy_items["binary_sensor.octopus_energy_a_12345678_octoplus_saving_sessions"] = yaml.safe_load(session_binary)
    ha.dummy_items["event.octopus_energy_a_12345678_octoplus_saving_session_event"] = yaml.safe_load(session_sensor)
    ha.dummy_items["sensor.octopus_free_session"] = {}
    my_predbat.args["octopus_saving_session"] = "event.octopus_energy_a_12345678_octoplus_saving_session_event"
    my_predbat.args["octopus_free_session"] = "sensor.octopus_free_session"
    if "octopus_free_url" in my_predbat.args:
        del my_predbat.args["octopus_free_url"]
    my_predbat.args["octopus_saving_session_octopoints_per_penny"] = 10
    # Notifications are covered by test_saving_session_notify_config; keeping them off here makes the
    # expected service calls independent of the wall clock (a joined event that has not finished yet alerts)
    my_predbat.expose_config("set_event_notify", False, quiet=True)

    ha.service_store_enable = True
    octopus_free_slots, octopus_saving_slots = my_predbat.fetch_octopus_sessions()
    service_result = ha.get_service_store()
    ha.service_store_enable = False
    my_predbat.expose_config("set_event_notify", True, quiet=True)

    expected_saving = [
        {"start": "{}T17:30:00+{}:00".format(date_yesterday, tz_offset), "end": "{}T18:30:00+{}:00".format(date_yesterday, tz_offset), "rate": 19.2, "state": False},
        {"start": "{}T16:30:00+{}:00".format(date_today, tz_offset), "end": "{}T17:30:00+{}:00".format(date_today, tz_offset), "rate": 44.8, "state": False},
        {"start": "{}T23:30:00+{}:00".format(date_before_yesterday, tz_offset), "end": "{}T10:30:00+{}:00".format(date_yesterday, tz_offset), "rate": 44.8, "state": False},
    ]

    expected_service = [
        ["octopus_energy/join_octoplus_saving_session_event", {"event_code": 987654, "entity_id": "event.octopus_energy_a_12345678_octoplus_saving_session_event"}],
    ]

    if json.dumps(octopus_saving_slots) != json.dumps(expected_saving):
        print("ERROR: Expecting saving slots should be {} got {}".format(expected_saving, octopus_saving_slots))
        failed = 1
    if json.dumps(service_result) != json.dumps(expected_service):
        print("ERROR: Expecting service store should be {} got {}".format(expected_service, service_result))
        failed = 1
    if octopus_free_slots:
        print("ERROR: Expecting no free slots")
        failed = 1

    rate_import_replicated = {}
    my_predbat.rate_import = {n: 0 for n in range(-24 * 60, 48 * 60)}
    my_predbat.load_saving_slot(expected_saving, export=False, rate_replicate=rate_import_replicated)
    price_ranges = [[(17.5 - 24) * 60, (18.5 - 24) * 60, 19.2], [(16.5) * 60, (17.5) * 60, 44.8], [-24 * 60, (10.5 - 24) * 60, 44.8]]
    for minute in range(-24 * 60, 48 * 60):
        rate = my_predbat.rate_import[minute]
        in_range = False
        for price_range in price_ranges:
            if minute >= price_range[0] and minute < price_range[1]:
                if rate != price_range[2]:
                    print("ERROR: Load Octopus Saving - minute {} Expecting rate to be {} got {}".format(minute, price_range[2], rate))
                    failed = 1
                    break
                in_range = True
        if not in_range:
            if rate != 0:
                print("ERROR: Load Octopus Saving - minute {} Expecting rate to be 0 got {}".format(minute, rate))
                failed = 1
                break

    return failed


def test_saving_session_null_octopoints(my_predbat):
    """
    Test the octopus saving session with null octopoints_per_kwh
    This tests the fix for GitHub issue #3079
    """
    print("Test saving session with null octopoints_per_kwh (issue #3079)")
    ha = my_predbat.ha_interface
    failed = False
    date_today = datetime.now().strftime("%Y-%m-%d")
    tz_offset = int(my_predbat.midnight_utc.tzinfo.utcoffset(my_predbat.midnight_utc).total_seconds() / 3600)
    tz_offset = f"{tz_offset:02d}"

    # Simulate data from a user who is no longer enrolled in saving sessions
    # All octopoints_per_kwh values are null, which previously caused TypeError
    session_sensor = f"""
state: '2025-01-23T12:10:11.108+{tz_offset}:00'
event_types: octopus_energy_all_octoplus_saving_sessions
event_type: octopus_energy_all_octoplus_saving_sessions
account_id: A-4DD6C5EE
available_events: []
joined_events:
    - id: 1342
      start: '2025-03-03T18:00:00+00:00'
      end: '2025-03-03T19:00:00+00:00'
      duration_in_minutes: 60
      rewarded_octopoints: 296
      octopoints_per_kwh: null
      code: null
    - id: 1343
      start: '2025-03-04T18:00:00+00:00'
      end: '2025-03-04T19:00:00+00:00'
      duration_in_minutes: 60
      rewarded_octopoints: 16
      octopoints_per_kwh: null
      code: null
    - id: 1344
      start: '2025-03-05T18:00:00+00:00'
      end: '2025-03-05T19:00:00+00:00'
      duration_in_minutes: 60
      rewarded_octopoints: 64
      octopoints_per_kwh: null
      code: null
friendly_name: Octopus Intelligent Saving Sessions
"""

    session_binary = f"""
state: off
current_joined_event_start: null
current_joined_event_end: null
current_joined_event_duration_in_minutes: null
next_joined_event_start: null
next_joined_event_end: null
next_joined_event_duration_in_minutes: null
icon: mdi:currency-usd
friendly_name: Octopus Intelligent Saving Sessions
"""

    ha.dummy_items["binary_sensor.octopus_energy_a_12345678_octoplus_saving_sessions"] = yaml.safe_load(session_binary)
    ha.dummy_items["event.octopus_energy_a_12345678_octoplus_saving_session_event"] = yaml.safe_load(session_sensor)
    ha.dummy_items["sensor.octopus_free_session"] = {}
    my_predbat.args["octopus_saving_session"] = "event.octopus_energy_a_12345678_octoplus_saving_session_event"
    my_predbat.args["octopus_free_session"] = "sensor.octopus_free_session"
    if "octopus_free_url" in my_predbat.args:
        del my_predbat.args["octopus_free_url"]
    my_predbat.args["octopus_saving_session_octopoints_per_penny"] = 8

    # This should not raise TypeError anymore
    try:
        octopus_free_slots, octopus_saving_slots = my_predbat.fetch_octopus_sessions()
    except TypeError as e:
        print(f"ERROR: TypeError raised when handling null octopoints_per_kwh: {e}")
        failed = True
        return failed

    # All events with null octopoints_per_kwh should be ignored
    if octopus_saving_slots:
        print(f"ERROR: Expected no saving slots (null octopoints_per_kwh should be ignored), got {octopus_saving_slots}")
        failed = True

    if octopus_free_slots:
        print("ERROR: Expecting no free slots")
        failed = True

    print("PASS: Null octopoints_per_kwh handled correctly - no TypeError raised, events ignored")
    return failed


def test_saving_session_notify_config(my_predbat):
    """
    Test that set_event_notify configuration controls Octopus saving session notifications

    The alert is raised when an event is confirmed as joined (it appears in joined_events), not when
    the join is requested, because Octopus can refuse a join - see test_saving_session_join_rejected.
    """
    print("Test saving session notification configuration")
    ha = my_predbat.ha_interface
    failed = False
    date_today = datetime.now().strftime("%Y-%m-%d")
    tz_offset = int(my_predbat.midnight_utc.tzinfo.utcoffset(my_predbat.midnight_utc).total_seconds() / 3600)
    tz_offset = f"{tz_offset:02d}"
    # A joined event that has not finished yet, so the confirmation alert is due
    joined_start = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:00")
    joined_end = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:00")

    session_binary = f"""
state: off
current_joined_event_start: null
current_joined_event_end: null
current_joined_event_duration_in_minutes: null
next_joined_event_start: null
next_joined_event_end: null
next_joined_event_duration_in_minutes: null
icon: mdi:leaf
friendly_name: Octoplus Saving Session
"""

    session_sensor = f"""
state: '2025-01-23T12:10:11.108+{tz_offset}:00'
event_types: octopus_energy_all_octoplus_saving_sessions
event_type: octopus_energy_all_octoplus_saving_sessions
account_id: A-4DD6C5EE
available_events:
    - id: 9999
      start: '{date_today}T18:30:00+{tz_offset}:00'
      end: '{date_today}T19:30:00+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: null
      octopoints_per_kwh: 500
      code: TEST123
joined_events:
    - id: 8888
      start: '{joined_start}+{tz_offset}:00'
      end: '{joined_end}+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: null
      octopoints_per_kwh: 500
      code: JOINED456
friendly_name: Octoplus Saving Session Events
"""

    # Test 1: Notifications enabled (default)
    print("  Test 1: Notifications enabled (set_event_notify not set, should default to True)")
    ha.dummy_items.clear()
    ha.dummy_items["binary_sensor.octopus_energy_test_octoplus_saving_sessions"] = yaml.safe_load(session_binary)
    ha.dummy_items["event.octopus_energy_test_octoplus_saving_session_event"] = yaml.safe_load(session_sensor)
    ha.dummy_items["sensor.octopus_free_session"] = {}
    my_predbat.args["octopus_saving_session"] = "event.octopus_energy_test_octoplus_saving_session_event"
    my_predbat.args["octopus_free_session"] = "sensor.octopus_free_session"
    if "octopus_free_url" in my_predbat.args:
        del my_predbat.args["octopus_free_url"]
    my_predbat.args["octopus_saving_session_octopoints_per_penny"] = 10
    # Don't set set_event_notify - should default to True
    if "set_event_notify" in my_predbat.args:
        del my_predbat.args["set_event_notify"]
    # Reset the last joined try timer so it will attempt to join, and the alerted-events record so the
    # confirmed join alerts again rather than being suppressed as a repeat
    my_predbat.octopus_last_joined_try = None
    my_predbat.octopus_saving_notified = {}

    ha.service_store_enable = True
    ha.service_store = []
    octopus_free_slots, octopus_saving_slots = my_predbat.fetch_octopus_sessions()
    service_result = ha.get_service_store()
    ha.service_store_enable = False

    # Should have notification service call
    notify_calls = [svc for svc in service_result if svc[0] == "notify/notify"]
    if len(notify_calls) != 1:
        print(f"ERROR: Expected 1 notification call with default set_event_notify, got {len(notify_calls)}")
        print(f"  Service calls: {service_result}")
        failed = True
    else:
        print("  PASS: Notification sent when set_event_notify defaults to True")

    # Test 2: Notifications explicitly enabled
    print("  Test 2: Notifications explicitly enabled (set_event_notify=True)")
    ha.dummy_items.clear()
    ha.dummy_items["binary_sensor.octopus_energy_test_octoplus_saving_sessions"] = yaml.safe_load(session_binary)
    ha.dummy_items["event.octopus_energy_test_octoplus_saving_session_event"] = yaml.safe_load(session_sensor)
    ha.dummy_items["sensor.octopus_free_session"] = {}
    my_predbat.args["set_event_notify"] = True
    # Reset the last joined try timer so it will attempt to join, and the alerted-events record so the
    # confirmed join alerts again rather than being suppressed as a repeat
    my_predbat.octopus_last_joined_try = None
    my_predbat.octopus_saving_notified = {}

    ha.service_store_enable = True
    ha.service_store = []
    octopus_free_slots, octopus_saving_slots = my_predbat.fetch_octopus_sessions()
    service_result = ha.get_service_store()
    ha.service_store_enable = False

    # Should have notification service call
    notify_calls = [svc for svc in service_result if svc[0] == "notify/notify"]
    if len(notify_calls) != 1:
        print(f"ERROR: Expected 1 notification call with set_event_notify=True, got {len(notify_calls)}")
        print(f"  Service calls: {service_result}")
        failed = True
    else:
        print("  PASS: Notification sent when set_event_notify=True")

    # Test 3: Notifications disabled
    print("  Test 3: Notifications disabled (set_event_notify=False)")
    ha.dummy_items.clear()
    ha.dummy_items["binary_sensor.octopus_energy_test_octoplus_saving_sessions"] = yaml.safe_load(session_binary)
    ha.dummy_items["event.octopus_energy_test_octoplus_saving_session_event"] = yaml.safe_load(session_sensor)
    ha.dummy_items["sensor.octopus_free_session"] = {}
    # Update config_index to set the value to False
    my_predbat.expose_config("set_event_notify", False, quiet=True)
    # Reset the last joined try timer so it will attempt to join, and the alerted-events record so the
    # confirmed join alerts again rather than being suppressed as a repeat
    my_predbat.octopus_last_joined_try = None
    my_predbat.octopus_saving_notified = {}

    ha.service_store_enable = True
    ha.service_store = []
    octopus_free_slots, octopus_saving_slots = my_predbat.fetch_octopus_sessions()
    service_result = ha.get_service_store()
    ha.service_store_enable = False

    # Should NOT have notification service call
    notify_calls = [svc for svc in service_result if svc[0] == "notify/notify"]
    if len(notify_calls) != 0:
        print(f"ERROR: Expected 0 notification calls with set_event_notify=False, got {len(notify_calls)}")
        print(f"  Service calls: {service_result}")
        failed = True
    else:
        print("  PASS: Notification blocked when set_event_notify=False")

    # Verify that the saving session was still joined (only notification blocked, not the join)
    join_calls = [svc for svc in service_result if "join" in svc[0]]
    if len(join_calls) != 1:
        print(f"ERROR: Expected 1 join service call even with notifications disabled, got {len(join_calls)}")
        failed = True
    else:
        print("  PASS: Saving session still joined when notifications disabled")

    if not failed:
        print("PASS: All notification configuration tests passed")

    return failed


def test_saving_session_axle_conflict(my_predbat):
    """
    Test that an available Octopus saving session is not auto-joined when it overlaps an Axle VPP session
    Covers GitHub issue #4120
    """
    print("Test saving session Axle conflict avoidance (issue #4120)")
    ha = my_predbat.ha_interface
    failed = False
    date_today = datetime.now().strftime("%Y-%m-%d")
    tz_offset = int(my_predbat.midnight_utc.tzinfo.utcoffset(my_predbat.midnight_utc).total_seconds() / 3600)
    tz_offset = f"{tz_offset:02d}"

    session_binary = f"""
state: off
current_joined_event_start: null
current_joined_event_end: null
current_joined_event_duration_in_minutes: null
next_joined_event_start: null
next_joined_event_end: null
next_joined_event_duration_in_minutes: null
icon: mdi:leaf
friendly_name: Octoplus Saving Session
"""

    # A single available saving session 18:30-19:30
    session_sensor = f"""
state: '2025-01-23T12:10:11.108+{tz_offset}:00'
event_types: octopus_energy_all_octoplus_saving_sessions
event_type: octopus_energy_all_octoplus_saving_sessions
account_id: A-4DD6C5EE
available_events:
    - id: 9999
      start: '{date_today}T18:30:00+{tz_offset}:00'
      end: '{date_today}T19:30:00+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: null
      octopoints_per_kwh: 500
      code: TEST123
joined_events: []
friendly_name: Octoplus Saving Session Events
"""

    def setup_items():
        ha.dummy_items.clear()
        ha.dummy_items["binary_sensor.octopus_energy_test_octoplus_saving_sessions"] = yaml.safe_load(session_binary)
        ha.dummy_items["event.octopus_energy_test_octoplus_saving_session_event"] = yaml.safe_load(session_sensor)
        ha.dummy_items["sensor.octopus_free_session"] = {}
        my_predbat.args["octopus_saving_session"] = "event.octopus_energy_test_octoplus_saving_session_event"
        my_predbat.args["octopus_free_session"] = "sensor.octopus_free_session"
        if "octopus_free_url" in my_predbat.args:
            del my_predbat.args["octopus_free_url"]
        my_predbat.args["octopus_saving_session_octopoints_per_penny"] = 10
        # Reset throttle so a join is attempted
        my_predbat.octopus_last_joined_try = None

    # Test 1: Overlapping Axle session (Axle 19:00-20:00 overlaps saving 18:30-19:30) -> no join
    print("  Test 1: Overlapping Axle session blocks the join")
    setup_items()
    axle_overlap = [
        {
            "start_time": f"{date_today}T19:00:00+{tz_offset}:00",
            "end_time": f"{date_today}T20:00:00+{tz_offset}:00",
            "import_export": "export",
            "pence_per_kwh": 50,
        }
    ]
    ha.service_store_enable = True
    ha.service_store = []
    my_predbat.fetch_octopus_sessions(axle_overlap)
    service_result = ha.get_service_store()
    ha.service_store_enable = False

    join_calls = [svc for svc in service_result if "join" in svc[0]]
    if join_calls:
        print(f"ERROR: Expected no join when overlapping an Axle session, got {join_calls}")
        failed = True
    else:
        print("  PASS: Join skipped when saving session overlaps an Axle session")

    # Test 2: Non-overlapping Axle session (Axle 12:00-13:00) -> join proceeds
    print("  Test 2: Non-overlapping Axle session allows the join")
    setup_items()
    axle_clear = [
        {
            "start_time": f"{date_today}T12:00:00+{tz_offset}:00",
            "end_time": f"{date_today}T13:00:00+{tz_offset}:00",
            "import_export": "export",
            "pence_per_kwh": 50,
        }
    ]
    ha.service_store_enable = True
    ha.service_store = []
    my_predbat.fetch_octopus_sessions(axle_clear)
    service_result = ha.get_service_store()
    ha.service_store_enable = False

    join_calls = [svc for svc in service_result if "join" in svc[0]]
    if len(join_calls) != 1:
        print(f"ERROR: Expected 1 join when Axle session does not overlap, got {len(join_calls)}: {service_result}")
        failed = True
    else:
        print("  PASS: Join proceeds when no Axle session overlaps")

    # Test 3: No Axle sessions at all -> join proceeds (backwards compatible default)
    print("  Test 3: No Axle sessions allows the join")
    setup_items()
    ha.service_store_enable = True
    ha.service_store = []
    my_predbat.fetch_octopus_sessions()
    service_result = ha.get_service_store()
    ha.service_store_enable = False

    join_calls = [svc for svc in service_result if "join" in svc[0]]
    if len(join_calls) != 1:
        print(f"ERROR: Expected 1 join when no Axle sessions provided, got {len(join_calls)}: {service_result}")
        failed = True
    else:
        print("  PASS: Join proceeds when no Axle sessions are provided")

    if not failed:
        print("PASS: All Axle conflict avoidance tests passed")

    # Restore default throttle state so we do not leak it to other tests
    my_predbat.octopus_last_joined_try = None

    return failed


def test_saving_session_auto_join_toggle(my_predbat):
    """
    Test that the octopus_saving_auto_join switch controls whether available saving sessions are auto-joined
    Covers GitHub issue #4120
    """
    print("Test saving session auto-join toggle (issue #4120)")
    ha = my_predbat.ha_interface
    failed = False
    date_today = datetime.now().strftime("%Y-%m-%d")
    tz_offset = int(my_predbat.midnight_utc.tzinfo.utcoffset(my_predbat.midnight_utc).total_seconds() / 3600)
    tz_offset = f"{tz_offset:02d}"

    session_binary = f"""
state: off
current_joined_event_start: null
current_joined_event_end: null
current_joined_event_duration_in_minutes: null
next_joined_event_start: null
next_joined_event_end: null
next_joined_event_duration_in_minutes: null
icon: mdi:leaf
friendly_name: Octoplus Saving Session
"""

    session_sensor = f"""
state: '2025-01-23T12:10:11.108+{tz_offset}:00'
event_types: octopus_energy_all_octoplus_saving_sessions
event_type: octopus_energy_all_octoplus_saving_sessions
account_id: A-4DD6C5EE
available_events:
    - id: 9999
      start: '{date_today}T18:30:00+{tz_offset}:00'
      end: '{date_today}T19:30:00+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: null
      octopoints_per_kwh: 500
      code: TEST123
joined_events: []
friendly_name: Octoplus Saving Session Events
"""

    def setup_items():
        ha.dummy_items.clear()
        ha.dummy_items["binary_sensor.octopus_energy_test_octoplus_saving_sessions"] = yaml.safe_load(session_binary)
        ha.dummy_items["event.octopus_energy_test_octoplus_saving_session_event"] = yaml.safe_load(session_sensor)
        ha.dummy_items["sensor.octopus_free_session"] = {}
        my_predbat.args["octopus_saving_session"] = "event.octopus_energy_test_octoplus_saving_session_event"
        my_predbat.args["octopus_free_session"] = "sensor.octopus_free_session"
        if "octopus_free_url" in my_predbat.args:
            del my_predbat.args["octopus_free_url"]
        my_predbat.args["octopus_saving_session_octopoints_per_penny"] = 10
        # Reset throttle so a join is attempted
        my_predbat.octopus_last_joined_try = None

    # Test 1: auto-join disabled -> no join
    print("  Test 1: octopus_saving_auto_join=False blocks the join")
    setup_items()
    my_predbat.expose_config("octopus_saving_auto_join", False, quiet=True)
    ha.service_store_enable = True
    ha.service_store = []
    my_predbat.fetch_octopus_sessions()
    service_result = ha.get_service_store()
    ha.service_store_enable = False

    join_calls = [svc for svc in service_result if "join" in svc[0]]
    if join_calls:
        print(f"ERROR: Expected no join when auto-join disabled, got {join_calls}")
        failed = True
    else:
        print("  PASS: Join skipped when octopus_saving_auto_join is False")

    # Test 2: auto-join enabled -> join proceeds
    print("  Test 2: octopus_saving_auto_join=True allows the join")
    setup_items()
    my_predbat.expose_config("octopus_saving_auto_join", True, quiet=True)
    ha.service_store_enable = True
    ha.service_store = []
    my_predbat.fetch_octopus_sessions()
    service_result = ha.get_service_store()
    ha.service_store_enable = False

    join_calls = [svc for svc in service_result if "join" in svc[0]]
    if len(join_calls) != 1:
        print(f"ERROR: Expected 1 join when auto-join enabled, got {len(join_calls)}: {service_result}")
        failed = True
    else:
        print("  PASS: Join proceeds when octopus_saving_auto_join is True")

    if not failed:
        print("PASS: All auto-join toggle tests passed")

    # Restore default state so we do not leak it to other tests
    my_predbat.expose_config("octopus_saving_auto_join", True, quiet=True)
    my_predbat.octopus_last_joined_try = None

    return failed


def test_saving_session_default_rate(my_predbat):
    """
    Test that saving sessions with no octopoints_per_kwh use the default rate
    from octopus_saving_session_rate config (flexibility API migration)
    """
    print("Test saving session default rate injection")
    ha = my_predbat.ha_interface
    failed = False
    date_today = datetime.now().strftime("%Y-%m-%d")
    date_yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    tz_offset = int(my_predbat.midnight_utc.tzinfo.utcoffset(my_predbat.midnight_utc).total_seconds() / 3600)
    tz_offset = f"{tz_offset:02d}"

    # Simulate new API format: joined events with no octopoints data
    session_binary = f"""
state: off
current_joined_event_start: '{date_today}T17:00:00+{tz_offset}:00'
current_joined_event_end: '{date_today}T18:00:00+{tz_offset}:00'
current_joined_event_duration_in_minutes: 60
next_joined_event_start: null
next_joined_event_end: null
next_joined_event_duration_in_minutes: null
icon: mdi:leaf
friendly_name: Octoplus Saving Session (A-TEST1234)
"""

    # Events with null octopoints_per_kwh — simulating new flexibility API
    # which does not return rewardPerKwhInOctoPoints
    session_sensor = f"""
state: '2026-03-23T12:00:00+{tz_offset}:00'
event_types: octopus_energy_all_octoplus_saving_sessions
event_type: octopus_energy_all_octoplus_saving_sessions
account_id: A-TEST1234
available_events: []
joined_events:
    - id: 2001
      start: '{date_today}T17:00:00+{tz_offset}:00'
      end: '{date_today}T18:00:00+{tz_offset}:00'
      duration_in_minutes: 60
      rewarded_octopoints: null
      octopoints_per_kwh: null
      code: FLEX-2001
friendly_name: Octoplus Saving Session Events
"""

    ha.dummy_items["binary_sensor.octopus_energy_a_test1234_octoplus_saving_sessions"] = yaml.safe_load(session_binary)
    ha.dummy_items["event.octopus_energy_a_test1234_octoplus_saving_session_event"] = yaml.safe_load(session_sensor)
    ha.dummy_items["sensor.octopus_free_session"] = {}
    my_predbat.args["octopus_saving_session"] = "event.octopus_energy_a_test1234_octoplus_saving_session_event"
    my_predbat.args["octopus_free_session"] = "sensor.octopus_free_session"
    if "octopus_free_url" in my_predbat.args:
        del my_predbat.args["octopus_free_url"]
    my_predbat.args["octopus_saving_session_octopoints_per_penny"] = 8
    my_predbat.args["octopus_saving_session_rate"] = 100  # 100 p/kWh default

    ha.service_store_enable = True
    ha.service_store = []
    octopus_free_slots, octopus_saving_slots = my_predbat.fetch_octopus_sessions()
    ha.service_store_enable = False

    # Should have saving slots with default rate injected
    # octopus_saving_session_rate=100 p/kWh used as fallback when octopoints_per_kwh is null
    if not octopus_saving_slots:
        print("ERROR: Expected saving slots with default rate, got none")
        failed = True
    else:
        slot = octopus_saving_slots[0]
        expected_rate = 100.0  # default_rate_pence (100) * octopoints_per_penny (8) / octopoints_per_penny (8) = 100 p/kWh
        if slot.get("rate") != expected_rate:
            print(f"ERROR: Expected default rate {expected_rate}, got {slot.get('rate')}")
            failed = True
        else:
            print(f"PASS: Default rate correctly injected for null octopoints: {expected_rate} p/kWh")

    return failed


def test_saving_session_bad_slot(my_predbat):
    """
    Test that a saving slot which cannot be decoded does not re-apply the previous slot's window
    """
    print("Test saving session with an undecodable slot")
    failed = False

    old_forecast_minutes = my_predbat.forecast_minutes
    old_rate_import = my_predbat.rate_import
    old_load_scaling_dynamic = my_predbat.load_scaling_dynamic

    my_predbat.forecast_minutes = 24 * 60
    plan_end = my_predbat.forecast_minutes + my_predbat.minutes_now
    my_predbat.rate_import = {n: 20.0 for n in range(0, plan_end)}
    my_predbat.load_scaling_dynamic = {n: 1.0 for n in range(0, plan_end)}

    midnight = my_predbat.midnight_utc
    good_start = (midnight + timedelta(minutes=600)).strftime("%Y-%m-%dT%H:%M:%S%z")
    good_end = (midnight + timedelta(minutes=660)).strftime("%Y-%m-%dT%H:%M:%S%z")

    slots = [
        {"start": good_start, "end": good_end, "rate": 10.0, "state": False},
        {"start": "not-a-time", "end": "also-not-a-time", "rate": 30.0, "state": False},
    ]

    rate_replicate = {}
    my_predbat.load_saving_slot(slots, export=False, rate_replicate=rate_replicate)

    # The good slot adds its rate once, and the undecodable slot must not add its own rate on top
    if my_predbat.rate_import[600] != 30.0 or my_predbat.rate_import[659] != 30.0:
        print("ERROR: Expected the good slot to add 10p once (30.0), got {}".format(my_predbat.rate_import[600]))
        failed = True
    elif my_predbat.rate_import[660] != 20.0 or my_predbat.rate_import[599] != 20.0:
        print("ERROR: Saving slot applied outside its own window")
        failed = True
    else:
        print("PASS: Undecodable slot ignored without re-applying the previous window")

    my_predbat.forecast_minutes = old_forecast_minutes
    my_predbat.rate_import = old_rate_import
    my_predbat.load_scaling_dynamic = old_load_scaling_dynamic

    return failed


def test_saving_session_join_rejected(my_predbat):
    """
    Test that a saving session event Octopus refuses to join is recorded and no longer offered

    Octopus can refuse a join for an event it advertises, e.g. a region-targeted event, returning
    HTTP 200 with a GraphQL error. Predbat must not report that event as joined or keep retrying it.
    """
    print("Test saving session join rejection")
    failed = False

    api = OctopusAPI(my_predbat, key="", account_id="A-TEST", automatic=False)
    fixed_time = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    api.saving_sessions = {
        "account": {"hasJoinedCampaign": True, "joinedEvents": []},
        "events": [
            {"id": 1, "code": "REGION_ONLY", "startAt": "2026-06-16T17:00:00+00:00", "endAt": "2026-06-16T18:00:00+00:00", "rewardPerKwhInOctoPoints": 100},
            {"id": 2, "code": "JOINABLE", "startAt": "2026-06-17T17:00:00+00:00", "endAt": "2026-06-17T18:00:00+00:00", "rewardPerKwhInOctoPoints": 150},
        ],
    }

    # Test 1: Both advertised events are offered before any rejection
    print("\n*** Test 1: Both events offered before a rejection ***")
    with patch.object(type(api), "now_utc_exact", new_callable=lambda: property(lambda self: fixed_time)):
        available, joined = api.get_saving_session_data()
    codes = sorted([event["code"] for event in available])
    if codes != ["JOINABLE", "REGION_ONLY"]:
        print("ERROR: Expected both events to be available, got {}".format(codes))
        failed = True
    else:
        print("PASS: Both events offered as joinable")

    # Test 2: A refused join is recorded along with the reason Octopus gave
    print("\n*** Test 2: Refused join is recorded with the reason ***")
    octopus_errors = [
        {
            "message": "Account ineligible to join Saving Sessions event.",
            "extensions": {"errorCode": "OE-1308", "reason": "Account's region is outside of the target regions for this event."},
        }
    ]

    async def refused_query(*args, **kwargs):
        api.last_graphql_errors = octopus_errors
        return None

    async def unchanged_sessions(account_id):
        return api.saving_sessions

    api.async_graphql_query = refused_query
    api.async_get_saving_sessions = unchanged_sessions
    asyncio.run(api.async_join_saving_session_events("A-TEST", "REGION_ONLY"))

    rejected = api.saving_session_join_rejected.get("REGION_ONLY", None)
    if not rejected:
        print("ERROR: Expected the refused event to be recorded, got {}".format(api.saving_session_join_rejected))
        failed = True
    elif "OE-1308" not in rejected.get("reason", "") or "region" not in rejected.get("reason", ""):
        print("ERROR: Expected the rejection reason to quote Octopus, got {}".format(rejected))
        failed = True
    else:
        print("PASS: Refused join recorded as {}".format(rejected))

    # Test 3: The refused event is no longer offered as joinable
    print("\n*** Test 3: Refused event is no longer offered ***")
    with patch.object(type(api), "now_utc_exact", new_callable=lambda: property(lambda self: fixed_time)):
        available, joined = api.get_saving_session_data()
    codes = [event["code"] for event in available]
    if codes != ["JOINABLE"]:
        print("ERROR: Expected only the joinable event to be offered, got {}".format(codes))
        failed = True
    else:
        print("PASS: Refused event dropped from the joinable events")

    # Test 4: A successful join clears a previous rejection
    print("\n*** Test 4: Successful join clears the rejection ***")

    async def accepted_query(*args, **kwargs):
        return {"joinSavingSessionsEvent": {"joinedEventCodes": ["REGION_ONLY"]}}

    api.async_graphql_query = accepted_query
    asyncio.run(api.async_join_saving_session_events("A-TEST", "REGION_ONLY"))
    if "REGION_ONLY" in api.saving_session_join_rejected:
        print("ERROR: Expected a successful join to clear the rejection, got {}".format(api.saving_session_join_rejected))
        failed = True
    else:
        print("PASS: Successful join cleared the rejection")

    # Test 5: With no errors recorded the reason is reported as unknown rather than blank
    print("\n*** Test 5: Missing error detail is described ***")
    api.last_graphql_errors = None
    if api.describe_graphql_errors() != "no reason given":
        print("ERROR: Expected 'no reason given', got '{}'".format(api.describe_graphql_errors()))
        failed = True
    else:
        print("PASS: Missing error detail described as 'no reason given'")

    if not failed:
        print("PASS: All saving session join rejection tests passed")

    return failed
