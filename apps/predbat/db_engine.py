# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# Database Engine for Predbat Home Battery System
# This module handles all SQL Lite database operations.
# -----------------------------------------------------------------------------


"""SQLite database engine for entity state persistence.

Provides the DatabaseEngine class that manages a local SQLite database
for storing entity states and history, with automatic pruning of old
data and deduplication of unchanged states.
"""

import sqlite3
import json
import os
import time
from datetime import datetime, timedelta

TIME_FORMAT_DB = "%Y-%m-%dT%H:%M:%S.%f"

# Number of times to retry opening/pruning the database before treating it as corrupt
DB_OPEN_RETRIES = 3
# Seconds to wait between open retries
DB_OPEN_RETRY_WAIT = 5
# Only vacuum when at least this much space would be reclaimed. VACUUM rewrites the
# whole file, evicting a database-sized amount of page cache, so it is not worth doing
# for a small saving on a memory-constrained machine.
VACUUM_MIN_FREE_BYTES = 256 * 1024 * 1024


class DatabaseEngine:
    """SQLite database engine for entity state persistence.

    Manages a local SQLite database for storing entity states and history
    with automatic pruning, deduplication, and keep-level classification
    (Intra-hour, Hourly, Daily).
    """

    def __init__(self, base, db_days, db_days_daily=365):
        self.base = base
        self.log = base.log
        self.db_days = db_days
        self.db_days_daily = db_days_daily
        self.db_path = self.base.config_root + "/predbat.db"
        self.db = None
        self.db_cursor = None
        self.entity_id_cache = {}
        self.last_maintenance_date = None

        self._connect()
        self._cleanup_db_retry()
        self.log("db_engine: Started")

    def _connect(self):
        """
        Open (or re-open) the database connection
        """
        self.db = sqlite3.connect(self.db_path)
        self.db_cursor = self.db.cursor()
        # The cache maps entity names to row indexes in the open connection, so it
        # must not survive a reconnect
        self.entity_id_cache = {}

    def _cleanup_db_retry(self):
        """
        Run _cleanup_db(), retrying on SQLite corruption errors before giving up.

        A "database disk image is malformed" error at startup is usually transient -
        it is normally the previous Predbat process still closing the database while
        the restarted one opens it. Letting that exception escape kills the database
        thread for the whole life of the process, and with db_primary set that leaves
        Predbat running with no history at all, so retry with a fresh connection first.

        If the image really is damaged the file is moved aside and a new one started,
        because looping on a permanently corrupt file would just restart Predbat for ever.
        """
        for attempt in range(1, DB_OPEN_RETRIES + 1):
            try:
                self._cleanup_db()
                if attempt > 1:
                    self.log("Info: db_engine: Database opened successfully on attempt {}".format(attempt))
                return
            except sqlite3.DatabaseError as e:
                self.log("Warn: db_engine: Database error on open attempt {} of {}: {}".format(attempt, DB_OPEN_RETRIES, e))
                try:
                    self.db.close()
                except sqlite3.Error:
                    pass
                if attempt < DB_OPEN_RETRIES:
                    time.sleep(DB_OPEN_RETRY_WAIT)
                    self._connect()

        # Out of retries - decide whether the file itself is actually damaged
        self._connect()
        corrupt = True
        try:
            result = self.db_cursor.execute("PRAGMA quick_check(10)").fetchone()
            corrupt = not (result and result[0] == "ok")
            if not corrupt:
                self.log("Warn: db_engine: quick_check reports the database is OK, retrying cleanup once more")
                self._cleanup_db()
                self.log("Info: db_engine: Database recovered after quick_check")
                return
        except sqlite3.DatabaseError as e:
            self.log("Warn: db_engine: quick_check failed: {}".format(e))

        if corrupt:
            self._quarantine_db()

    def _quarantine_db(self):
        """
        Move a corrupt database out of the way and start a fresh one.

        History is lost, but Predbat keeps running and rebuilds from new data rather
        than restart-looping on a file that will never open.
        """
        try:
            self.db.close()
        except sqlite3.Error:
            pass
        bad_path = "{}.corrupt-{}".format(self.db_path, datetime.now().strftime("%Y%m%d-%H%M%S"))
        try:
            os.rename(self.db_path, bad_path)
            self.log("Error: db_engine: Database is corrupt, moved to {} and starting a new empty database - history has been lost".format(bad_path))
            try:
                # Runs during startup, so notification config may not be loaded yet
                self.base.call_notify("Predbat: database was corrupt, moved to {} and started a new one. History has been lost.".format(bad_path))
            except Exception as e:
                self.log("Warn: db_engine: Unable to send corruption notification: {}".format(e))
        except OSError as e:
            self.log("Error: db_engine: Unable to move corrupt database aside: {}".format(e))
            raise
        self._connect()
        self._cleanup_db()

    def _close(self):
        """
        Close the database connection
        """
        if self.db:
            self._commit_db()
            self.db.close()
            self.log("db_engine: Closed")
            self.db = None

    def _cleanup_db(self):
        """
        This searches all tables for data older than N days and deletes it
        """
        self.db_cursor.execute("CREATE TABLE IF NOT EXISTS entities (entity_index INTEGER PRIMARY KEY AUTOINCREMENT, entity_name TEXT KEY UNIQUE)")
        self.db_cursor.execute("CREATE TABLE IF NOT EXISTS states (id INTEGER PRIMARY KEY AUTOINCREMENT, datetime TEXT KEY, entity_index INTEGER KEY, state TEXT, attributes TEXT, system TEXT, keep TEXT KEY)")
        self.db_cursor.execute("CREATE TABLE IF NOT EXISTS latest (entity_index INTEGER PRIMARY KEY, datetime TEXT KEY, state TEXT, attributes TEXT, system TEXT, keep TEXT KEY)")
        # Create index for fast history queries (critical for performance)
        self.db_cursor.execute("CREATE INDEX IF NOT EXISTS idx_states_entity_datetime ON states(entity_index, datetime)")
        # Delete old data from states table. The cut-off must be formatted the same way
        # as the stored values - binding a datetime gives SQLite a space separator
        # ("2026-07-13 07:00:00") which does not sort against the stored "T" form.
        cutoff = (self.base.now_utc_real - timedelta(days=self.db_days)).strftime(TIME_FORMAT_DB)
        self.db_cursor.execute(
            "DELETE FROM states WHERE datetime < ? AND keep != ?",
            (
                cutoff,
                "D",
            ),
        )
        deleted = self.db_cursor.rowcount

        # Daily-keep rows are exempt from the prune above, so they grow without limit
        # unless they get a (much longer) retention of their own
        cutoff_daily = (self.base.now_utc_real - timedelta(days=self.db_days_daily)).strftime(TIME_FORMAT_DB)
        self.db_cursor.execute(
            "DELETE FROM states WHERE datetime < ? AND keep = ?",
            (
                cutoff_daily,
                "D",
            ),
        )
        deleted_daily = self.db_cursor.rowcount
        self._commit_db()
        return deleted, deleted_daily

    def _maintenance_db(self):
        """
        Nightly housekeeping: prune expired rows and reclaim the free pages left behind.

        Deleting rows only moves pages onto SQLite's free list, so the file never shrinks
        on its own. VACUUM rebuilds it compactly, but it rewrites the whole file, which
        evicts a database-sized amount of kernel page cache - enough to push a small
        machine into swapping. So it only runs when there is enough free space to be
        worth the churn, which in steady state means rarely rather than every night.
        """
        start_time = time.time()
        size_before = self._db_size()
        deleted, deleted_daily = self._cleanup_db()

        free_bytes = self._freelist_bytes()
        if free_bytes < VACUUM_MIN_FREE_BYTES:
            self.log(
                "Info: db_engine: Nightly maintenance pruned {} rows ({} daily) in {} seconds, skipping vacuum ({} MB reclaimable, threshold {} MB)".format(
                    deleted, deleted_daily, round(time.time() - start_time, 1), round(free_bytes / (1024 * 1024), 1), round(VACUUM_MIN_FREE_BYTES / (1024 * 1024), 1)
                )
            )
            return

        # VACUUM cannot run inside a transaction
        self.db.commit()
        isolation_level = self.db.isolation_level
        self.db.isolation_level = None
        try:
            self.db_cursor.execute("VACUUM")
        finally:
            self.db.isolation_level = isolation_level

        size_after = self._db_size()
        self.log(
            "Info: db_engine: Nightly maintenance pruned {} rows ({} daily) and vacuumed, database {} MB -> {} MB in {} seconds".format(
                deleted, deleted_daily, round(size_before / (1024 * 1024), 1), round(size_after / (1024 * 1024), 1), round(time.time() - start_time, 1)
            )
        )

    def _freelist_bytes(self):
        """
        Bytes that a VACUUM would reclaim, or 0 if it cannot be determined
        """
        try:
            page_size = self.db_cursor.execute("PRAGMA page_size").fetchone()[0]
            free_pages = self.db_cursor.execute("PRAGMA freelist_count").fetchone()[0]
            return page_size * free_pages
        except (sqlite3.DatabaseError, TypeError, IndexError):
            return 0

    def _db_size(self):
        """
        Current size of the database file in bytes, or 0 if it cannot be read
        """
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0

    def _get_state_db(self, entity_id):
        """
        Get entity current state from the SQLLite database
        """
        entity_id = entity_id.lower()
        entity_index = self._get_entity_index_db(entity_id)
        if not entity_index:
            return None

        self.db_cursor.execute("SELECT datetime, state, attributes FROM latest WHERE entity_index=?", (entity_index,))
        res = self.db_cursor.fetchone()
        if res:
            state = res[1]
            attributes = {}
            try:
                attributes = json.loads(res[2])
            except json.JSONDecodeError:
                pass
            return {"last_updated": res[0] + "Z", "state": state, "attributes": attributes}
        else:
            return None

    def _get_entity_index_db(self, entity_id):
        """
        Get the entity index from the SQLLite database
        """
        if entity_id in self.entity_id_cache:
            return self.entity_id_cache[entity_id]

        self.db_cursor.execute("SELECT entity_index FROM entities WHERE entity_name=?", (entity_id,))
        res = self.db_cursor.fetchone()
        if res:
            self.entity_id_cache[entity_id] = res[0]
            return res[0]
        else:
            return None

    def _get_all_entities_db(self):
        """
        Get all entity names from the SQLLite database
        """
        self.db_cursor.execute("SELECT entity_name FROM entities")
        rows = self.db_cursor.fetchall()
        return [row[0] for row in rows]

    def _commit_db(self):
        """
        Commit changes to the database
        """
        self.db.commit()

    def _set_state_db(self, entity_id, state, attributes, timestamp):
        """
        Records the state of a predbat entity into the SQLLite database
        There is one table per Entity created with history recorded
        """
        state = str(state)

        # Put the entity_id into entities table if its not in already
        entity_index = self._get_entity_index_db(entity_id)
        if entity_index is None:
            self.db_cursor.execute("INSERT OR IGNORE INTO entities (entity_name) VALUES (?)", (entity_id,))
            self.db.commit()  # Commit to ensure the entity is added
            entity_index = self._get_entity_index_db(entity_id)

        # Convert time to GMT+0
        now_utc = timestamp
        now_utc_txt = now_utc.strftime(TIME_FORMAT_DB)

        system = {}

        attributes_record = {}
        attributes_record_full = {}
        for key in attributes:
            value = attributes[key]
            # Ignore large fields which will increase the database size
            if len(str(value)) < 128:
                attributes_record[key] = attributes[key]
            attributes_record_full[key] = attributes[key]
        attributes_record_json = json.dumps(attributes_record)
        attributes_record_full_json = json.dumps(attributes_record_full)
        system_json = json.dumps(system)

        # Record the state of the entity
        # If the entity value and attributes are unchanged then don't record the new state
        self.db_cursor.execute("SELECT datetime, state, attributes, system, keep FROM states WHERE entity_index=? ORDER BY datetime DESC LIMIT 1", (entity_index,))
        last_record = self.db_cursor.fetchone()
        keep = "D"
        if last_record:
            last_datetime = datetime.strptime(last_record[0], TIME_FORMAT_DB)
            last_state = last_record[1]
            last_attributes = last_record[2]
            last_system = last_record[3]
            last_keep = last_record[4]
            if last_state == state and last_attributes == attributes_record_json and last_system == system_json:
                return

            # Compute keep value
            last_datetime_datestr = last_datetime.strftime("%Y-%m-%d")
            now_datetime_datestr = now_utc.strftime("%Y-%m-%d")
            if last_datetime_datestr == now_datetime_datestr:
                if last_datetime.hour == now_utc.hour:
                    keep = "I"
                else:
                    keep = "H"

        # Insert the new state record
        try:
            self.db_cursor.execute("DELETE FROM states WHERE entity_index = ? AND datetime = ?", (entity_index, now_utc_txt))
            self.db_cursor.execute(
                "INSERT INTO states (datetime, entity_index, state, attributes, system, keep) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    now_utc_txt,
                    entity_index,
                    state,
                    attributes_record_json,
                    system_json,
                    keep,
                ),
            )
            # Upsert into the latest table (replace if exists, insert if not)
            self.db_cursor.execute(
                """
                INSERT INTO latest (entity_index, datetime, state, attributes, system, keep)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_index) DO UPDATE SET
                    datetime=excluded.datetime,
                    state=excluded.state,
                    attributes=excluded.attributes,
                    system=excluded.system,
                    keep=excluded.keep
                """,
                (
                    entity_index,
                    now_utc_txt,
                    state,
                    attributes_record_full_json,
                    system_json,
                    keep,
                ),
            )
        except sqlite3.IntegrityError:
            self.log("Warn: SQL Integrity error inserting data for {}".format(entity_id))

    def _get_history_db(self, sensor, now, days=30):
        """
        Get the history for a sensor from the SQLLite database.
        """
        start = now - timedelta(days=days)

        entity_index = self._get_entity_index_db(sensor)
        if not entity_index:
            self.log("Warn: Entity {} does not exist".format(sensor))
            return None

        # Get the history for the sensor, sorted by datetime
        self.db_cursor.execute(
            "SELECT datetime, state, attributes FROM states WHERE entity_index = ? AND datetime >= ? ORDER BY datetime",
            (
                entity_index,
                start.strftime(TIME_FORMAT_DB),
            ),
        )
        res = self.db_cursor.fetchall()

        # Rarely-changing entities (e.g. input_number sliders) may have no state
        # change inside the window; inject the most recent prior state clamped to
        # the window start, matching the HA history API behaviour
        if not res:
            self.db_cursor.execute(
                "SELECT datetime, state, attributes FROM states WHERE entity_index = ? AND datetime < ? ORDER BY datetime DESC LIMIT 1",
                (
                    entity_index,
                    start.strftime(TIME_FORMAT_DB),
                ),
            )
            prior = self.db_cursor.fetchone()
            if prior:
                res = [(start.strftime(TIME_FORMAT_DB), prior[1], prior[2])]

        history = []
        for item in res:
            state = item[1]
            attributes = {}
            try:
                attributes = json.loads(item[2])
            except json.JSONDecodeError:
                pass

            history.append({"last_updated": item[0] + "Z", "state": state, "attributes": attributes})
        return [history]
