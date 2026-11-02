# MyMcdAPI

An unofficial Python client and utility library for the [MyMcd](https://mymcd.eu) employee portal used by McDonald's Czech Republic & Slovakia. Provides programmatic access to shift plans, employee profiles, training verifications, restaurant floorplans, and Google Calendar / SQLite shift syncing.

---

## Features

- **Automated Session & Cookie Authentication**: Handles authentication across `mymcd.eu` and `next.mymcd.eu`, managing dual `PHPSESSID` and `mymcd2_session` tokens.
- **Role-Based Access Control**: Enforces permissions via `@requires_role` decorators (`CREW`, `CT`, `MANAGER`) matching portal security rules.
- **Dynamic Context Resolution**: Automatically populates `user_id`, `role`, `restaurant_id`, and `restaurant_code` upon login.
- **Rich Verification Engine**: Extracts training status, dates, and attempt IDs (points, evaluator names) with concurrent worker support.
- **Shift Manager Module**: Includes `McdShiftManager` to synchronize restaurant floorplans and shifts to SQLite for fast offline queries and coworker overlap detection.

---

## Prerequisites

- **Python 3.10+**

---

## Installation

```bash
git clone https://github.com/Milos-Opletal/MyMcdAPI.git
cd MyMcdAPI
pip install -r requirements.txt
```

---

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

```env
# Required: McDonald's API credentials
MYMCD_EMAIL=your_email@example.com
MYMCD_PASSWORD=your_password_here
```

---

## Quickstart

### 1. Basic API Usage

```python
import os
from dotenv import load_dotenv
from MyMcdAPI import MyMcdAPI

load_dotenv()

api = MyMcdAPI(os.environ["MYMCD_EMAIL"], os.environ["MYMCD_PASSWORD"])
api.login()

# Current user details
me = api.get_me()
print(f"Logged in as: {me.get('fullName')} | Role: {api.role.name}")

# Fetch shifts for a date range
shifts = api.get_employee_shifts("2026-04-01", "2026-04-30")
print(f"Found {len(shifts.get('shiftPlans', []))} shifts.")
```

### 2. Shift Manager (SQLite Sync & Coworker Queries)

```python
import os
from dotenv import load_dotenv
from MyMcdAPI import MyMcdAPI
from McdShiftManager import McdShiftManager

load_dotenv()

api = MyMcdAPI(os.environ["MYMCD_EMAIL"], os.environ["MYMCD_PASSWORD"])
api.login()

manager = McdShiftManager(api, db_path="mymcd_shifts.sqlite")

# Sync all restaurant shifts for a date range (Manager access required)
manager.sync_shifts("2026-04-01", "2026-04-30")

# Query special supervisor roles (RS, OS, NS) for a specific date
roles = manager.get_special_roles("2026-04-15")
print(roles)  # {'RS': 'Jan Novak', 'OS': 'Petra Svobodova', 'NS': None}

# Find coworkers on shift at the same time as you
coworkers = manager.get_coworker_shift_times(user_id=api.user_id, date="2026-04-15")
for c in coworkers:
    print(f"{c['full_name']}: {c['start_time']} - {c['end_time']}")
```

---

## API Endpoints Reference

| Method | Minimum Role | Description |
|--------|--------------|-------------|
| `get_me()` | `CREW` | Current authenticated user profile and restaurant context |
| `get_default_codes()` | `CREW` | Global definitions (positions, verifications, stations) |
| `get_events(from_date, to_date)` | `CREW` | Restaurant events and double-pay public holidays |
| `get_employee_details(employee_id)` | `CREW` | Basic employee card info |
| `get_employees_data_list(...)` | `CREW` | Paginated employee list for your restaurant |
| `get_employee_shifts(from, to, [id])` | `CREW`* | Shift plans (`MANAGER` required to inspect other employees) |
| `get_profile_verifications([id])` | `CREW`* | Station training verifications (`CT` required for other employees) |
| `get_restaurant_shifts(from, to)` | `MANAGER` | Full shift roster across the entire restaurant |
| `get_restaurant_floorplan(date)` | `MANAGER` | Station floorplan and shift allocations for a specific day |
| `get_restaurant_availability(from, to)` | `MANAGER` | Employee availability matrix |
| `get_employee_shift_stats(year, month, ids)` | `MANAGER` | Hours worked and shift statistics for employee IDs |
| `get_expiring_verifications(from, to, ids)` | `MANAGER` | Verifications expiring in the specified date range |
| `get_verification_attempt(attempt_id)` | `MANAGER` | Evaluator, points scored, and completion timestamp |
| `get_all_employees_verification_summaries(...)` | `MANAGER` | Multi-threaded compilation of all staff verification statuses |

---

## Disclaimer

This project is an independent tool developed for shift synchronization and operational analytics. It is not affiliated with, endorsed by, or sponsored by McDonald's Corporation.
