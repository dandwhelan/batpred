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

"""Unit tests for the apps.yaml write handler in web.py.

``html_apps_post`` is the only path in Predbat that rewrites the user's apps.yaml from the web
UI, and it had no tests. A fault here does not just return a bad response - it can corrupt the
configuration file the add-on needs to start, so the write path, the type coercion and every
rejection route are worth pinning down.

The handler resolves ``apps.yaml`` relative to the working directory, so each test runs inside
a temporary directory holding a purpose-built file. That keeps the suite's own coverage/apps.yaml
untouched no matter what the handler does.
"""

import json
import os
import shutil
import tempfile

from web import WebInterface

from tests.test_infra import run_async

SAMPLE_APPS_YAML = """\
pred_bat:
  module: predbat
  class: PredBat

  # A comment that must survive the round trip
  battery_size: 9.5
  inverter_type: GE
  debug_enable: false
  car_charging_hold: true
  battery_charge_low:
    normal: 10
    holiday: 20
  charge_rates:
  - 3.6
  - 2.4
"""


class MockPostRequest:
    """Stand-in for an aiohttp POST request carrying form data."""

    def __init__(self, changes=None, raw=None):
        """Build a request whose ``changes`` field holds the encoded edit set."""
        if raw is not None:
            self._data = {"changes": raw}
        elif changes is None:
            self._data = {}
        else:
            self._data = {"changes": json.dumps(changes)}

    async def post(self):
        """Return the form body."""
        return dict(self._data)


class AppsYamlSandbox:
    """Runs a test inside a temporary working directory holding its own apps.yaml.

    ``html_apps_post`` opens ``apps.yaml`` by relative path, so without this the tests would
    rewrite the apps.yaml the rest of the suite depends on.
    """

    def __init__(self, contents=SAMPLE_APPS_YAML):
        """Record the file contents to seed the sandbox with."""
        self.contents = contents

    def __enter__(self):
        """Create the sandbox, write apps.yaml into it and switch to it."""
        self.saved_cwd = os.getcwd()
        self.directory = tempfile.mkdtemp(prefix="predbat_apps_post_")
        self.path = os.path.join(self.directory, "apps.yaml")
        if self.contents is not None:
            with open(self.path, "w") as handle:
                handle.write(self.contents)
        os.chdir(self.directory)
        return self

    def read(self):
        """Return the current contents of the sandbox apps.yaml."""
        with open(self.path, "r") as handle:
            return handle.read()

    def __exit__(self, exc_type, exc_value, traceback):
        """Restore the working directory and remove the sandbox."""
        os.chdir(self.saved_cwd)
        shutil.rmtree(self.directory, ignore_errors=True)
        return False


def make_web(my_predbat):
    """Create a WebInterface bound to the shared fixture."""
    return WebInterface(my_predbat, web_port=5052)


def post_changes(web_interface, changes=None, raw=None):
    """Run the handler and return the decoded JSON response body."""
    response = run_async(web_interface.html_apps_post(MockPostRequest(changes=changes, raw=raw)))
    return json.loads(response.body.decode())


def _test_nested_path_updates(my_predbat):
    """Dot and bracket paths reach nested mappings and list entries."""
    print("Test: _update_nested_yaml_value walks dot and bracket paths")
    web_interface = make_web(my_predbat)

    data = {"battery_charge_low": {"normal": 10, "holiday": 20}, "charge_rates": [3.6, 2.4], "nested": {"deep": {"value": 1}}}

    web_interface._update_nested_yaml_value(data, "battery_charge_low.normal", 15)
    if data["battery_charge_low"]["normal"] != 15:
        print("ERROR: the nested mapping was not updated, got {}".format(data["battery_charge_low"]))
        return 1
    # The sibling key must be left alone
    if data["battery_charge_low"]["holiday"] != 20:
        print("ERROR: a sibling key was modified, got {}".format(data["battery_charge_low"]))
        return 1

    web_interface._update_nested_yaml_value(data, "charge_rates[1]", 5.0)
    if data["charge_rates"] != [3.6, 5.0]:
        print("ERROR: the list entry was not updated, got {}".format(data["charge_rates"]))
        return 1

    web_interface._update_nested_yaml_value(data, "nested.deep.value", 99)
    if data["nested"]["deep"]["value"] != 99:
        print("ERROR: the deep path was not updated, got {}".format(data["nested"]))
        return 1
    return 0


def _test_nested_path_rejects_bad_paths(my_predbat):
    """Unknown keys and out-of-range indices raise rather than creating new entries.

    Silently creating a key would write a setting Predbat never reads into the user's file.
    """
    print("Test: _update_nested_yaml_value rejects paths that do not exist")
    web_interface = make_web(my_predbat)
    data = {"battery_charge_low": {"normal": 10}, "charge_rates": [3.6]}

    for path in ("battery_charge_low.missing", "no_such_key.normal", "charge_rates[5]", "charge_rates[0].deeper"):
        try:
            web_interface._update_nested_yaml_value(data, path, 1)
        except (KeyError, TypeError):
            continue
        print("ERROR: path {} was accepted, data is now {}".format(path, data))
        return 1

    if data != {"battery_charge_low": {"normal": 10}, "charge_rates": [3.6]}:
        print("ERROR: a rejected path still modified the data, got {}".format(data))
        return 1
    return 0


def _test_post_requires_changes(my_predbat):
    """A request with no changes, empty changes or bad JSON is refused."""
    print("Test: the apps.yaml handler validates its payload")
    web_interface = make_web(my_predbat)

    with AppsYamlSandbox() as sandbox:
        original = sandbox.read()

        for description, kwargs in (
            ("no changes field", {}),
            ("empty changes", {"raw": ""}),
            ("malformed JSON", {"raw": "{not json"}),
            ("empty change set", {"changes": {}}),
        ):
            body = post_changes(web_interface, **kwargs)
            if body.get("success"):
                print("ERROR: {} was accepted: {}".format(description, body))
                return 1

        # None of the rejections may have touched the file
        if sandbox.read() != original:
            print("ERROR: a rejected request rewrote apps.yaml")
            return 1
    return 0


def _test_post_updates_top_level(my_predbat):
    """A valid top-level edit is written to the file and mirrored into the live args."""
    print("Test: the apps.yaml handler writes a top-level change")
    web_interface = make_web(my_predbat)
    saved_args = my_predbat.args.copy()

    try:
        with AppsYamlSandbox() as sandbox:
            my_predbat.args["battery_size"] = 9.5
            body = post_changes(web_interface, {"battery_size": {"newValue": "12.5", "type": "numerical"}})

            if not body.get("success"):
                print("ERROR: the update was refused: {}".format(body))
                return 1

            contents = sandbox.read()
            if "battery_size: 12.5" not in contents:
                print("ERROR: the new value was not written, file reads:\n{}".format(contents))
                return 1
            # The live configuration must reflect the change without waiting for a restart
            if my_predbat.args.get("battery_size") != 12.5:
                print("ERROR: the live args were not updated, got {}".format(my_predbat.args.get("battery_size")))
                return 1
            # Comments and untouched settings must survive the rewrite
            if "A comment that must survive the round trip" not in contents:
                print("ERROR: the rewrite dropped the file's comments")
                return 1
            if "inverter_type: GE" not in contents:
                print("ERROR: the rewrite lost an unrelated setting")
                return 1
    finally:
        my_predbat.args = saved_args
    return 0


def _test_post_type_conversion(my_predbat):
    """Values are coerced to the declared type before being written."""
    print("Test: the apps.yaml handler coerces value types")
    web_interface = make_web(my_predbat)
    saved_args = my_predbat.args.copy()

    try:
        with AppsYamlSandbox() as sandbox:
            body = post_changes(
                web_interface,
                {
                    "battery_size": {"newValue": "10", "type": "numerical"},
                    "debug_enable": {"newValue": "true", "type": "boolean"},
                    "car_charging_hold": {"newValue": "false", "type": "boolean"},
                    "inverter_type": {"newValue": "GS", "type": "string"},
                },
            )
            if not body.get("success"):
                print("ERROR: the update was refused: {}".format(body))
                return 1

            # An integer-looking numerical value must not become a float
            if my_predbat.args.get("battery_size") != 10 or isinstance(my_predbat.args.get("battery_size"), float):
                print("ERROR: expected the integer 10, got {!r}".format(my_predbat.args.get("battery_size")))
                return 1
            if my_predbat.args.get("debug_enable") is not True:
                print("ERROR: expected a True boolean, got {!r}".format(my_predbat.args.get("debug_enable")))
                return 1
            if my_predbat.args.get("car_charging_hold") is not False:
                print("ERROR: expected a False boolean, got {!r}".format(my_predbat.args.get("car_charging_hold")))
                return 1
            if my_predbat.args.get("inverter_type") != "GS":
                print("ERROR: expected the string GS, got {!r}".format(my_predbat.args.get("inverter_type")))
                return 1

            contents = sandbox.read()
            if "debug_enable: true" not in contents:
                print("ERROR: the boolean was not written as YAML true, file reads:\n{}".format(contents))
                return 1
    finally:
        my_predbat.args = saved_args
    return 0


def _test_post_rejects_bad_number(my_predbat):
    """A value that will not parse as a number is refused and nothing is written."""
    print("Test: the apps.yaml handler refuses an unparseable number")
    web_interface = make_web(my_predbat)
    saved_args = my_predbat.args.copy()

    try:
        with AppsYamlSandbox() as sandbox:
            original = sandbox.read()
            body = post_changes(web_interface, {"battery_size": {"newValue": "not-a-number", "type": "numerical"}})

            if body.get("success"):
                print("ERROR: an unparseable number was accepted: {}".format(body))
                return 1
            if "Invalid value format" not in body.get("message", ""):
                print("ERROR: expected an invalid value message, got {}".format(body))
                return 1
            if sandbox.read() != original:
                print("ERROR: apps.yaml was rewritten despite the bad value")
                return 1
    finally:
        my_predbat.args = saved_args
    return 0


def _test_post_rejects_unknown_argument(my_predbat):
    """An argument that is not already in apps.yaml is refused rather than added."""
    print("Test: the apps.yaml handler refuses an unknown argument")
    web_interface = make_web(my_predbat)
    saved_args = my_predbat.args.copy()

    try:
        with AppsYamlSandbox() as sandbox:
            original = sandbox.read()
            body = post_changes(web_interface, {"totally_made_up": {"newValue": "1", "type": "numerical"}})

            if body.get("success"):
                print("ERROR: an unknown argument was accepted: {}".format(body))
                return 1
            if sandbox.read() != original:
                print("ERROR: apps.yaml was rewritten for an unknown argument")
                return 1
    finally:
        my_predbat.args = saved_args
    return 0


def _test_post_nested_change(my_predbat):
    """A nested edit reaches the right key and leaves its siblings alone."""
    print("Test: the apps.yaml handler writes a nested change")
    web_interface = make_web(my_predbat)
    saved_args = my_predbat.args.copy()

    try:
        with AppsYamlSandbox() as sandbox:
            my_predbat.args["battery_charge_low"] = {"normal": 10, "holiday": 20}
            body = post_changes(web_interface, {"battery_charge_low.normal": {"newValue": "15", "type": "numerical", "isNested": True}})

            if not body.get("success"):
                print("ERROR: the nested update was refused: {}".format(body))
                return 1
            if my_predbat.args["battery_charge_low"]["normal"] != 15:
                print("ERROR: the live args were not updated, got {}".format(my_predbat.args["battery_charge_low"]))
                return 1
            if my_predbat.args["battery_charge_low"]["holiday"] != 20:
                print("ERROR: a sibling key was changed, got {}".format(my_predbat.args["battery_charge_low"]))
                return 1
            if "normal: 15" not in sandbox.read():
                print("ERROR: the nested value was not written, file reads:\n{}".format(sandbox.read()))
                return 1
    finally:
        my_predbat.args = saved_args
    return 0


def _test_post_rejects_bad_nested_path(my_predbat):
    """A nested path that does not exist is refused and nothing is written."""
    print("Test: the apps.yaml handler refuses an unknown nested path")
    web_interface = make_web(my_predbat)
    saved_args = my_predbat.args.copy()

    try:
        with AppsYamlSandbox() as sandbox:
            original = sandbox.read()
            body = post_changes(web_interface, {"battery_charge_low.no_such_key": {"newValue": "15", "type": "numerical", "isNested": True}})

            if body.get("success"):
                print("ERROR: an unknown nested path was accepted: {}".format(body))
                return 1
            if sandbox.read() != original:
                print("ERROR: apps.yaml was rewritten for an unknown nested path")
                return 1
    finally:
        my_predbat.args = saved_args
    return 0


def _test_post_missing_file(my_predbat):
    """A missing or unreadable apps.yaml is reported rather than raising."""
    print("Test: the apps.yaml handler reports an unreadable file")
    web_interface = make_web(my_predbat)

    with AppsYamlSandbox(contents=None):
        body = post_changes(web_interface, {"battery_size": {"newValue": "12.5", "type": "numerical"}})
        if body.get("success"):
            print("ERROR: the update succeeded without an apps.yaml: {}".format(body))
            return 1
        if "Error reading apps.yaml" not in body.get("message", ""):
            print("ERROR: expected a read error message, got {}".format(body))
            return 1
    return 0


def _test_post_missing_root_key(my_predbat):
    """An apps.yaml without the pred_bat section is refused."""
    print("Test: the apps.yaml handler requires the pred_bat section")
    web_interface = make_web(my_predbat)

    with AppsYamlSandbox(contents="something_else:\n  key: 1\n"):
        body = post_changes(web_interface, {"battery_size": {"newValue": "12.5", "type": "numerical"}})
        if body.get("success"):
            print("ERROR: the update succeeded without a pred_bat section: {}".format(body))
            return 1
        if "pred_bat" not in body.get("message", ""):
            print("ERROR: expected a message naming the missing section, got {}".format(body))
            return 1
    return 0


def test_web_apps_post(my_predbat=None):
    """Run the apps.yaml write handler test suite, returning non-zero if any sub-test failed."""
    sub_tests = [
        ("nested_update", _test_nested_path_updates, "Nested path updates"),
        ("nested_reject", _test_nested_path_rejects_bad_paths, "Nested path rejection"),
        ("payload", _test_post_requires_changes, "Payload validation"),
        ("top_level", _test_post_updates_top_level, "Top-level write"),
        ("types", _test_post_type_conversion, "Type coercion"),
        ("bad_number", _test_post_rejects_bad_number, "Unparseable number refused"),
        ("unknown_arg", _test_post_rejects_unknown_argument, "Unknown argument refused"),
        ("nested_write", _test_post_nested_change, "Nested write"),
        ("bad_nested", _test_post_rejects_bad_nested_path, "Unknown nested path refused"),
        ("missing_file", _test_post_missing_file, "Missing apps.yaml"),
        ("missing_root", _test_post_missing_root_key, "Missing pred_bat section"),
    ]

    print("\n" + "=" * 70)
    print("APPS.YAML WRITE HANDLER TEST SUITE")
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
    print("APPS.YAML WRITE RESULTS: {} passed, {} failed".format(passed, failed))
    print("=" * 70)
    return failed
