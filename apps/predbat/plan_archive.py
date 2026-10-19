# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------

"""Rolling archive of past plans and tariff comparisons.

The live plan is re-made every few minutes and only the latest copy is ever kept, and each tariff
comparison overwrites the last, so neither can be looked back on. This keeps compressed copies of
both under config_root/plan_archive for plan_archive_days days (default 4):

- plan_<YYYYMMDD-HHMM>.json.gz - the plan JSON the Plan page renders (predbat.plan_html's raw
  attribute), at most one per plan_archive_interval minutes (default 30).
- compare_<YYYYMMDD-HHMM>.json.gz - each tariff comparison run's results, keyed by tariff id.

A plan is about 100 KB of JSON that compresses to about 5 KB, so four days at one every 30 minutes
is roughly a megabyte. Each name carries the local time its snapshot was taken, so the directory
listing is the index and nothing else has to be kept in step with it.

The names end .json.gz on purpose: hass.py restarts Predbat when any file under the config tree
ending .py, or called apps.yaml, changes (see CLAUDE.md, "The file-watcher trap").
"""

import gzip
import json
import os
import re
from datetime import datetime, timedelta

ARCHIVE_DIR = "plan_archive"
KINDS = ("plan", "compare")
TIME_FORMAT_ID = "%Y%m%d-%H%M"
ID_PATTERN = re.compile(r"^\d{8}-\d{4}$")
NAME_PATTERN = re.compile(r"^(plan|compare)_(\d{8}-\d{4})\.json\.gz$")
DEFAULT_DAYS = 4
DEFAULT_INTERVAL_MINUTES = 30


def snapshot_label(snapshot_id):
    """A snapshot id as the day and time it was taken, e.g. 'Sun 19 Oct 09:30', or the id itself if it is not one"""
    try:
        return datetime.strptime(snapshot_id, TIME_FORMAT_ID).strftime("%a %d %b %H:%M")
    except (ValueError, TypeError):
        return str(snapshot_id)


def snapshot_day(snapshot_id):
    """The day part of a snapshot id's label, e.g. 'Sun 19 Oct'"""
    try:
        return datetime.strptime(snapshot_id, TIME_FORMAT_ID).strftime("%a %d %b")
    except (ValueError, TypeError):
        return ""


class PlanArchive:
    """
    Saves, lists, loads and expires plan and comparison snapshots in one directory.

    Writes go to a temporary file that is then renamed into place, so the web server's thread
    reading a snapshot never sees a half-written one.
    """

    def __init__(self, config_root, log):
        self.directory = os.path.join(config_root, ARCHIVE_DIR)
        self.log = log
        # The day and interval slot of the last plan saved, so a re-plan in the same slot is skipped
        self.last_plan_slot = None

    def _path(self, kind, snapshot_id):
        return os.path.join(self.directory, "{}_{}.json.gz".format(kind, snapshot_id))

    def save(self, kind, when, data):
        """
        Write one snapshot, named after when (local, timezone-aware), replacing any with the same name.

        Returns the snapshot id, or None when it could not be written - a full disk or a read-only
        config directory is logged but never allowed to stop the plan being made.
        """
        if kind not in KINDS:
            raise ValueError("Unknown plan archive kind {}".format(kind))
        snapshot_id = when.strftime(TIME_FORMAT_ID)
        path = self._path(kind, snapshot_id)
        temp_path = path + ".tmp"
        try:
            payload = json.dumps({"time": when.isoformat(), "data": data}).encode("utf-8")
            os.makedirs(self.directory, exist_ok=True)
            with gzip.open(temp_path, "wb", compresslevel=6) as handle:
                handle.write(payload)
            os.replace(temp_path, path)
        except (OSError, TypeError, ValueError) as e:
            self.log("Warn: Plan archive: could not save {} snapshot {}: {}".format(kind, snapshot_id, e))
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return None
        return snapshot_id

    def save_plan(self, when, plan_json, interval_minutes=DEFAULT_INTERVAL_MINUTES):
        """
        Archive the live plan at most once per interval_minutes slot of the day.

        The first plan made in a slot is the one kept, so a snapshot shows the plan as it stood when
        that slot began rather than whichever re-plan happened to come last. Returns the id saved, or
        None when this slot already has one or the write failed.
        """
        try:
            interval = max(1, int(interval_minutes))
        except (ValueError, TypeError):
            interval = DEFAULT_INTERVAL_MINUTES
        slot = (when.date(), (when.hour * 60 + when.minute) // interval)
        if slot == self.last_plan_slot:
            return None
        snapshot_id = self.save("plan", when, plan_json)
        if snapshot_id:
            self.last_plan_slot = slot
        return snapshot_id

    def list(self, kind):
        """Snapshot ids of one kind, newest first"""
        try:
            names = os.listdir(self.directory)
        except OSError:
            return []
        ids = []
        for name in names:
            match = NAME_PATTERN.match(name)
            if match and match.group(1) == kind:
                ids.append(match.group(2))
        return sorted(ids, reverse=True)

    def load(self, kind, snapshot_id):
        """
        One snapshot as {"time": iso string, "data": ...}, or None when it does not exist or cannot be read.

        The id comes from a web request, so only the exact id shape is accepted - it can never name a
        file outside the archive directory.
        """
        if kind not in KINDS or not isinstance(snapshot_id, str) or not ID_PATTERN.match(snapshot_id):
            return None
        try:
            with gzip.open(self._path(kind, snapshot_id), "rb") as handle:
                return json.loads(handle.read().decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, EOFError, ValueError) as e:
            self.log("Warn: Plan archive: could not read {} snapshot {}: {}".format(kind, snapshot_id, e))
            return None

    def prune(self, now, days):
        """Delete snapshots taken more than days before now (local, timezone-aware), and stray temporary files"""
        cutoff = now.replace(tzinfo=None) - timedelta(days=days)
        try:
            names = os.listdir(self.directory)
        except OSError:
            return 0
        removed = 0
        for name in names:
            match = NAME_PATTERN.match(name)
            expired = False
            if match:
                try:
                    expired = datetime.strptime(match.group(2), TIME_FORMAT_ID) < cutoff
                except ValueError:
                    expired = False
            elif name.endswith(".json.gz.tmp"):
                # Left behind only if Predbat stopped mid-write
                try:
                    expired = datetime.fromtimestamp(os.path.getmtime(os.path.join(self.directory, name))) < cutoff
                except OSError:
                    expired = False
            if expired:
                try:
                    os.remove(os.path.join(self.directory, name))
                    removed += 1
                except OSError as e:
                    self.log("Warn: Plan archive: could not remove {}: {}".format(name, e))
        return removed
