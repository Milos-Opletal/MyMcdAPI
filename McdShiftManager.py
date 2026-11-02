import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from MyMcdAPI import MyMcdAPI

logger = logging.getLogger(__name__)


class McdShiftManager:
    """
    High-level manager for syncing and querying McDonald's shifts in a local SQLite database.
    Supports shift tracking, coworker overlap queries, and floorplan role lookups.
    """

    def __init__(self, api: Optional[MyMcdAPI] = None, db_path: str = "mymcd_shifts.sqlite"):
        self.api = api
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        """Create the SQLite shifts table if it does not already exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS shifts (
                    id INTEGER PRIMARY KEY,
                    employee_id INTEGER,
                    full_name TEXT,
                    date TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    note TEXT
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_shifts_date ON shifts (date)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_shifts_employee ON shifts (employee_id)')
            conn.commit()

    # ==========================================
    # Shift Syncing
    # ==========================================

    def sync_shifts(self, from_date: str, to_date: str) -> None:
        """
        Fetch all restaurant shifts from the MyMcd API for a date range and save them to SQLite.
        """
        if not self.api:
            raise ValueError("MyMcdAPI instance must be provided to sync shifts.")

        logger.info(f"Syncing shifts from {from_date} to {to_date}...")
        data = self.api.get_restaurant_shifts(from_date, to_date)
        internal_employees = data.get("internalEmployees", [])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for emp in internal_employees:
                e_id = emp.get("employeeId")
                info = emp.get("employeeInfo", {})
                name = info.get("fullName")

                for shift in emp.get("shiftPlans", []):
                    s_id = shift.get("id")
                    shift_date = shift.get("date")
                    note = shift.get("note")

                    for interval in shift.get("intervals", []):
                        start = interval.get("from")
                        end = interval.get("to")

                        cursor.execute('''
                            INSERT OR REPLACE INTO shifts (id, employee_id, full_name, date, start_time, end_time, note)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (s_id, e_id, name, shift_date, start, end, note))

            conn.commit()
        logger.info("Restaurant shifts sync complete.")

    def sync_floorplan(self, date: str) -> None:
        """
        Fetch the floorplan for a specific date and record all shifts (including unassigned/null employee ID shifts).
        """
        if not self.api:
            raise ValueError("MyMcdAPI instance must be provided to sync floorplan.")

        try:
            data = self.api.get_restaurant_floorplan(date)
            position_groups = data.get("positionGroups", [])

            with self._get_connection() as conn:
                cursor = conn.cursor()
                for pg in position_groups:
                    for row in pg.get("rows", []):
                        emp_id = row.get("employeeId")
                        emp_info = row.get("employeeInfo") or {}
                        name = emp_info.get("fullName")

                        shift_plan = row.get("shiftPlan")
                        if shift_plan:
                            s_id = shift_plan.get("id")
                            date_str = shift_plan.get("date")
                            note = shift_plan.get("note")

                            for interval in shift_plan.get("intervals", []):
                                start = interval.get("from")
                                end = interval.get("to")

                                cursor.execute('''
                                    INSERT OR REPLACE INTO shifts (id, employee_id, full_name, date, start_time, end_time, note)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                ''', (s_id, emp_id, name, date_str, start, end, note))
                conn.commit()
            logger.info(f"Daily floorplan synced for date: {date}")
        except Exception as e:
            logger.error(f"Failed to sync floorplan for {date}: {e}")
            raise

    # ==========================================
    # Role & Coworker Queries
    # ==========================================

    def get_special_roles(self, date: str) -> Dict[str, Optional[str]]:
        """
        Returns names of employees assigned as RS (Restaurant Supervisor), OS (Open Supervisor),
        or NS (Night Supervisor) for a specific date.
        Gracefully extracts names from shift notes if employee_id is unassigned.
        """
        roles: Dict[str, Optional[str]] = {"RS": None, "OS": None, "NS": None}

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for role in roles.keys():
                cursor.execute('''
                    SELECT full_name, note
                    FROM shifts
                    WHERE date = ?
                      AND note LIKE ?
                    LIMIT 1
                ''', (date, f"%{role}%"))
                result = cursor.fetchone()
                if result:
                    db_name, note = result
                    if db_name:
                        roles[role] = db_name
                    elif note:
                        cleaned = re.sub(rf'(?i)\b{role}\b', '', note).strip()
                        cleaned = re.sub(r'\s*\d+[-:]\d+\s*', '', cleaned).strip()
                        cleaned = cleaned.strip(" -:,;._")
                        roles[role] = cleaned or None

        return roles

    def get_coworker_shift_times(self, user_id: int, date: str) -> List[Dict[str, Any]]:
        """
        Find all coworkers working during any shift intervals that overlap with the target user on a given date.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Fetch user's shift intervals for that date
            cursor.execute('''
                SELECT start_time, end_time
                FROM shifts
                WHERE employee_id = ? AND date = ?
            ''', (user_id, date))
            user_shifts = cursor.fetchall()

            if not user_shifts:
                return []

            # 2. Find overlapping coworkers
            coworkers: List[Dict[str, Any]] = []
            for u_start, u_end in user_shifts:
                cursor.execute('''
                    SELECT employee_id, full_name, start_time, end_time, note
                    FROM shifts
                    WHERE date = ?
                      AND employee_id != ?
                      AND start_time < ?
                      AND end_time > ?
                ''', (date, user_id, u_end, u_start))

                for row in cursor.fetchall():
                    coworkers.append({
                        "employee_id": row[0],
                        "full_name": row[1],
                        "start_time": row[2],
                        "end_time": row[3],
                        "note": row[4]
                    })

        # Deduplicate results
        unique_coworkers: List[Dict[str, Any]] = []
        seen = set()
        for c in coworkers:
            key = (c["employee_id"], c["start_time"], c["end_time"])
            if key not in seen:
                seen.add(key)
                unique_coworkers.append(c)

        return unique_coworkers


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    email = os.environ.get("MYMCD_EMAIL")
    password = os.environ.get("MYMCD_PASSWORD")

    if email and password:
        api_instance = MyMcdAPI(email, password)
        api_instance.login()
        shift_mgr = McdShiftManager(api_instance)
        print("McdShiftManager initialized successfully with active session.")
    else:
        print("Please set MYMCD_EMAIL and MYMCD_PASSWORD in .env to test McdShiftManager.")
