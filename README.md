# MyMcdAPI

An unofficial Python wrapper for the [MyMcd](https://mymcd.eu) employee portal used by McDonald's CZ/SK. It provides programmatic access to shift schedules, employee data, training verifications, and more — all through clean, role-aware method calls.

## Features

- **Session-based authentication** — logs in via the MyMcd web portal and manages cookies automatically
- **Role-based access control** — endpoints are guarded by a `@requires_role` decorator (`CREW`, `CT`, `MANAGER`) that mirrors the portal's permission model
- **Automatic context setup** — after login, your user ID, restaurant ID, and role are resolved automatically from `/api/user/me`
- **Shift Manager module** — includes `McdShiftManager`, a higher-level utility that syncs shifts to a local SQLite database for offline queries

### Available Endpoints

| Method | Min. Role | Description |
|--------|-----------|-------------|
| `get_me()` | CREW | Get your own profile & access data |
| `get_default_codes()` | CREW | Fetch global definitions (positions, verifications, etc.) |
| `get_events(from, to)` | CREW | Fetch events like holidays / 2× pay days |
| `get_employee_details(id)` | CREW | Get details for a specific employee |
| `get_employees_data_list()` | CREW | Paginated employee list for your restaurant |
| `get_employee_shifts(from, to)` | CREW* | Fetch shift plans (MANAGER required for other employees) |
| `get_profile_verifications()` | CREW* | Fetch training verifications (CT required for other employees) |
| `get_restaurant_shifts(from, to)` | MANAGER | All shifts for the entire restaurant |
| `get_restaurant_floorplan(date)` | MANAGER | Floor plan for a specific date |
| `get_restaurant_availability(from, to)` | MANAGER | Employee availability overview |
| `get_employee_shift_stats(year, month, ids)` | MANAGER | Shift statistics for specific employees |
| `get_expiring_verifications(from, to, ids)` | MANAGER | Expiring training verifications |

## Prerequisites

- **Python 3.9+**
- A valid **MyMcd account** (employee login at [mymcd.eu](https://mymcd.eu))

### Python Dependencies

```
requests
beautifulsoup4
python-dotenv
```

Install them with:

```bash
pip install requests beautifulsoup4 python-dotenv
```

## Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/Milos-Opletal/MyMcdAPI.git
   cd MyMcdAPI
   ```

2. **Create a `.env` file** in the project root with your MyMcd credentials:

   ```env
   MYMCD_EMAIL=your_email@example.com
   MYMCD_PASSWORD=your_password
   ```

3. **Install dependencies**

   ```bash
   pip install requests beautifulsoup4 python-dotenv
   ```

## Usage

### Basic API Usage

```python
from MyMcdAPI import MyMcdAPI

api = MyMcdAPI("your_email@example.com", "your_password")
api.login()

# Your profile
me = api.get_me()
print(f"Logged in as: {me['fullName']} (Role: {api.role.name})")

# Your shifts for a date range
shifts = api.get_employee_shifts("2026-04-01", "2026-04-30")
```

### Using Environment Variables (Recommended)

```python
import os
from dotenv import load_dotenv
from MyMcdAPI import MyMcdAPI

load_dotenv()

api = MyMcdAPI(os.environ["MYMCD_EMAIL"], os.environ["MYMCD_PASSWORD"])
api.login()
```

### Shift Manager

The `McdShiftManager` syncs restaurant-wide shifts to a local SQLite database and provides helper queries:

```python
from McdShiftManager import McdShiftManager

manager = McdShiftManager(api)

# Sync all shifts for March 2026
manager.sync_shifts("2026-03-01", "2026-03-31")

# Who is RS / OS / NS today?
roles = manager.get_special_roles("2026-03-29")
print(roles)  # {'RS': 'John Doe', 'OS': 'Jane Doe', 'NS': None}

# Who else is working at the same time as you?
coworkers = manager.get_coworker_shift_times(user_id=api.user_id, date="2026-03-29")
for c in coworkers:
    print(f"{c['full_name']}: {c['start_time']} - {c['end_time']}")
```

## Disclaimer

This project is **not affiliated with, endorsed by, or connected to McDonald's Corporation** or any of its subsidiaries. It is an independent, unofficial tool created for personal use. Use at your own risk and in compliance with the MyMcd platform's terms of service.
