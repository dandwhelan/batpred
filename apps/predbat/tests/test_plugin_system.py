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
Tests for the plugin discovery and hook system.

Plugins are a documented user feature (docs/plugins.md), so people write modules against
load_plugin's three class-detection strategies - and none of them had a test, leaving
plugin_system.py at 18%. Every case here writes a real plugin file into a temporary directory and
loads it, because what is under test is the loading itself.
"""

import os
import tempfile

from plugin_system import PluginSystem

# Plugin modules are written to a temporary directory and imported from there, so coverage traces
# files that no longer exist by the time it writes its report. coverage/setup.cfg omits this prefix
# for that reason - keep the two in step.
TEMP_PREFIX = "predbat_plugin_test_"

# A plugin found by the preferred strategy: a class whose name ends in "Plugin"
NAMED_CLASS_PLUGIN = '''
"""A plugin discovered by class name."""


class ExampleThingPlugin:
    """Discovered because its name ends in Plugin."""

    def __init__(self, base):
        self.base = base
        self.registered = False
        self.shutdown_called = False

    def register_hooks(self, plugin_system):
        """Register a hook so the plugin hears about updates."""
        self.registered = True
        plugin_system.register_hook("on_update", self.on_update)

    def on_update(self, *args, **kwargs):
        """Record that the update hook fired."""
        self.base.plugin_saw_update = True

    def shutdown(self):
        """Record that shutdown was called."""
        self.shutdown_called = True
'''

# A plugin found by the second strategy: marked with PREDBAT_PLUGIN rather than named for it
MARKED_CLASS_PLUGIN = '''
"""A plugin discovered by its marker attribute."""


class SomethingEntirelyDifferent:
    """Discovered because it carries PREDBAT_PLUGIN."""

    PREDBAT_PLUGIN = True

    def __init__(self, base):
        self.base = base
'''

# A plugin found by the final strategy: a module-level initialisation function
FUNCTION_PLUGIN = '''
"""A plugin discovered by its initialisation function."""


class Built:
    """The object the initialisation function hands back."""

    def __init__(self, base):
        self.base = base


def initialize_plugin(base):
    """Build and return the plugin instance."""
    return Built(base)
'''

# A module with nothing a plugin loader can use
EMPTY_PLUGIN = '''
"""A module with no plugin in it."""

VALUE = 1
'''

# A plugin whose constructor fails - the loader must not take the process down with it
BROKEN_CLASS_PLUGIN = '''
"""A plugin whose constructor raises."""


class BrokenPlugin:
    """Raises on construction."""

    def __init__(self, base):
        raise RuntimeError("plugin refused to start")
'''

# A named class that fails, with a marked class behind it: the fallback must still be reached
FALLBACK_PLUGIN = '''
"""A failing named class with a working marked class behind it."""


class FirstChoicePlugin:
    """Raises, so discovery has to fall through to the marker strategy."""

    def __init__(self, base):
        raise RuntimeError("no good")


class SecondChoice:
    """Picked up by its marker once the named class fails."""

    PREDBAT_PLUGIN = True

    def __init__(self, base):
        self.base = base
'''

# A plugin whose hook registration fails, and whose shutdown fails
UNRELIABLE_PLUGIN = '''
"""A plugin that raises during registration and shutdown."""


class UnreliablePlugin:
    """Constructs cleanly, then fails at everything else."""

    def __init__(self, base):
        self.base = base

    def register_hooks(self, plugin_system):
        """Fail to register."""
        raise RuntimeError("registration failed")

    def shutdown(self):
        """Fail to shut down."""
        raise RuntimeError("shutdown failed")
'''


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


def _write_plugin(directory, name, source):
    """Write one plugin module into a directory and return its module name."""
    with open(os.path.join(directory, name + ".py"), "w") as handle:
        handle.write(source)
    return name


def test_hooks_fire_and_survive_a_failing_callback(my_predbat):
    """
    A hook fires every registered callback, and one that raises must not stop the others.

    Plugins are third-party code; a plugin that throws on every plan should not stop Predbat
    executing it.
    """
    print("*** Running test: hooks fire and survive a failing callback")
    failed = 0
    plugin_system = PluginSystem(my_predbat)
    seen = []

    def good(value):
        """Record the call."""
        seen.append(("good", value))

    def bad(value):
        """Fail the call."""
        raise RuntimeError("callback exploded")

    def also_good(value):
        """Record the call."""
        seen.append(("also_good", value))

    plugin_system.register_hook("on_update", good)
    plugin_system.register_hook("on_update", bad)
    plugin_system.register_hook("on_update", also_good)
    plugin_system.call_hooks("on_update", 7)

    failed |= _check("hooks: both good callbacks ran", seen, [("good", 7), ("also_good", 7)])

    # A hook name nobody registered for is simply ignored
    plugin_system.call_hooks("on_nothing_at_all")

    # A new hook name can be registered on the fly
    plugin_system.register_hook("on_custom", good)
    plugin_system.call_hooks("on_custom", 9)
    failed |= _check("hooks: custom hook fired", seen[-1], ("good", 9))
    return failed


def test_a_named_class_is_preferred(my_predbat):
    """A class whose name ends in Plugin is the preferred way to be discovered."""
    print("*** Running test: a named plugin class is discovered")
    failed = 0

    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as directory:
        name = _write_plugin(directory, "named_example_plugin", NAMED_CLASS_PLUGIN)
        plugin_system = PluginSystem(my_predbat)
        plugin_system.load_plugin(directory, name)

        instance = plugin_system.get_plugin(name)
        failed |= _check_true("named: plugin loaded", instance is not None)
        failed |= _check("named: listed", plugin_system.list_plugins(), [name])
        failed |= _check_true("named: hooks registered", getattr(instance, "registered", False))

        # The hook the plugin registered must actually fire
        my_predbat.plugin_saw_update = False
        plugin_system.call_hooks("on_update")
        failed |= _check("named: registered hook fired", my_predbat.plugin_saw_update, True)

        plugin_system.shutdown_plugins()
        failed |= _check_true("named: shutdown called", getattr(instance, "shutdown_called", False))

    if hasattr(my_predbat, "plugin_saw_update"):
        del my_predbat.plugin_saw_update
    return failed


def test_a_marked_class_is_the_first_fallback(my_predbat):
    """A class carrying PREDBAT_PLUGIN is found even when its name says nothing."""
    print("*** Running test: a marked plugin class is discovered")
    failed = 0

    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as directory:
        name = _write_plugin(directory, "marked_example_plugin", MARKED_CLASS_PLUGIN)
        plugin_system = PluginSystem(my_predbat)
        plugin_system.load_plugin(directory, name)
        failed |= _check_true("marked: plugin loaded", plugin_system.get_plugin(name) is not None)
    return failed


def test_an_initialisation_function_is_the_last_fallback(my_predbat):
    """A module offering initialize_plugin is used when no suitable class is found."""
    print("*** Running test: an initialisation function is used as a last resort")
    failed = 0

    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as directory:
        name = _write_plugin(directory, "function_example_plugin", FUNCTION_PLUGIN)
        plugin_system = PluginSystem(my_predbat)
        plugin_system.load_plugin(directory, name)
        instance = plugin_system.get_plugin(name)
        failed |= _check_true("function: plugin loaded", instance is not None)
        failed |= _check("function: bound to predbat", getattr(instance, "base", None), my_predbat)
    return failed


def test_discovery_falls_through_a_broken_first_choice(my_predbat):
    """When the preferred class fails to construct, discovery carries on to the next strategy."""
    print("*** Running test: discovery falls through a broken first choice")
    failed = 0

    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as directory:
        name = _write_plugin(directory, "fallback_example_plugin", FALLBACK_PLUGIN)
        plugin_system = PluginSystem(my_predbat)
        plugin_system.load_plugin(directory, name)

        instance = plugin_system.get_plugin(name)
        failed |= _check_true("fallthrough: something loaded", instance is not None)
        failed |= _check("fallthrough: the marked class won", type(instance).__name__, "SecondChoice")
    return failed


def test_a_module_with_no_plugin_loads_nothing(my_predbat):
    """A module with nothing to instantiate is skipped rather than half-registered."""
    print("*** Running test: a module with no plugin loads nothing")
    failed = 0

    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as directory:
        name = _write_plugin(directory, "empty_example_plugin", EMPTY_PLUGIN)
        plugin_system = PluginSystem(my_predbat)
        plugin_system.load_plugin(directory, name)
        failed |= _check("empty: nothing registered", plugin_system.list_plugins(), [])

        # A plugin file that is not there at all is a no-op, not an error
        plugin_system.load_plugin(directory, "no_such_plugin")
        failed |= _check("missing file: nothing registered", plugin_system.list_plugins(), [])
    return failed


def test_a_plugin_that_will_not_start_is_left_out(my_predbat):
    """A plugin whose constructor raises is reported and skipped, not registered."""
    print("*** Running test: a plugin that will not start is left out")
    failed = 0

    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as directory:
        name = _write_plugin(directory, "broken_example_plugin", BROKEN_CLASS_PLUGIN)
        plugin_system = PluginSystem(my_predbat)
        plugin_system.load_plugin(directory, name)
        failed |= _check("broken: nothing registered", plugin_system.list_plugins(), [])
    return failed


def test_failures_after_loading_are_contained(my_predbat):
    """A plugin that fails to register hooks, or to shut down, must not take Predbat with it."""
    print("*** Running test: failures after loading are contained")
    failed = 0

    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as directory:
        name = _write_plugin(directory, "unreliable_example_plugin", UNRELIABLE_PLUGIN)
        plugin_system = PluginSystem(my_predbat)
        plugin_system.load_plugin(directory, name)

        # It constructed, so it is loaded, even though its registration failed
        failed |= _check("unreliable: still loaded", plugin_system.list_plugins(), [name])

        # And a failing shutdown is contained too
        plugin_system.shutdown_plugins()
    return failed


def test_discovery_scans_directories(my_predbat):
    """
    Discovery loads every *_plugin.py it finds, ignores anything else, and skips absent directories.

    The suffix is the contract users are told about, so a module named anything else must not be
    imported - importing a stranger's utility module as a plugin would run its side effects.
    """
    print("*** Running test: discovery scans directories")
    failed = 0

    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as directory:
        _write_plugin(directory, "first_example_plugin", MARKED_CLASS_PLUGIN)
        _write_plugin(directory, "second_example_plugin", FUNCTION_PLUGIN)
        _write_plugin(directory, "not_a_plugin_module", NAMED_CLASS_PLUGIN)
        _write_plugin(directory, "broken_example_plugin", BROKEN_CLASS_PLUGIN)

        plugin_system = PluginSystem(my_predbat)
        plugin_system.discover_plugins([directory, os.path.join(directory, "does_not_exist")])

        loaded = sorted(plugin_system.list_plugins())
        failed |= _check("discovery: only the well-named, working plugins loaded", loaded, ["first_example_plugin", "second_example_plugin"])
        failed |= _check("discovery: unknown plugin lookup returns None", plugin_system.get_plugin("not_a_plugin_module"), None)
    return failed


def run_plugin_system_tests(my_predbat):
    """Run the plugin discovery and hook tests, returning non-zero if any failed."""
    print("**** Running plugin system tests ****")
    failed = 0
    for test in (
        test_hooks_fire_and_survive_a_failing_callback,
        test_a_named_class_is_preferred,
        test_a_marked_class_is_the_first_fallback,
        test_an_initialisation_function_is_the_last_fallback,
        test_discovery_falls_through_a_broken_first_choice,
        test_a_module_with_no_plugin_loads_nothing,
        test_a_plugin_that_will_not_start_is_left_out,
        test_failures_after_loading_are_contained,
        test_discovery_scans_directories,
    ):
        failed |= test(my_predbat)
    return failed
