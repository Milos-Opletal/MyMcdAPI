import json
import logging
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import IntEnum
from functools import wraps
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class Role(IntEnum):
    CREW = 1
    CT = 2
    MANAGER = 3


class MyMcdAPIError(Exception):
    """Base exception for all MyMcdAPI errors."""
    pass


class AuthenticationError(MyMcdAPIError):
    """Raised when authentication with MyMcd fails."""
    pass


class PermissionDeniedError(MyMcdAPIError):
    """Raised when a user tries to access an endpoint above their permission level."""
    pass


def requires_role(minimum_role: Role):
    """Decorator to enforce base permission levels on API methods."""
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.role < minimum_role:
                raise PermissionDeniedError(
                    f"Access denied. '{func.__name__}' requires at least {minimum_role.name} level. "
                    f"Your current level is {self.role.name}."
                )
            return func(self, *args, **kwargs)
        return wrapper
    return decorator


class MyMcdAPI:
    """
    Unofficial Python wrapper for the McDonald's internal portal (mymcd.eu / next.mymcd.eu).
    Handles authentication, session cookies, role detection, and endpoint queries.
    """
    MANAGER_POSITION_IDS = {8, 9, 10, 11, 13}
    CT_POSITION_IDS = {5, 6}
    DEFAULT_TIMEOUT = 30  # seconds

    def __init__(self, email: str, password: str, timeout: int = DEFAULT_TIMEOUT):
        self.email = email
        self.password = password
        self.timeout = timeout
        self.mymcd2_session: Optional[str] = None
        self.phpsessid: Optional[str] = None
        self.req_session = requests.Session()

        # Context established after login
        self.role: Role = Role.CREW
        self.user_id: Optional[int] = None
        self.restaurant_id: Optional[int] = None
        self.restaurant_code: Optional[str] = None

    def login(self) -> None:
        """
        Authenticate against mymcd.eu and establish session cookies.
        """
        login_page_url = "https://mymcd.eu/login/"
        login_post_url = "https://mymcd.eu/user/login-check/"
        dashboard_url = "https://next.mymcd.eu/app/shifts/"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
            "Referer": login_page_url,
            "Origin": "https://mymcd.eu"
        }

        try:
            self.req_session.get(login_page_url, headers=headers, timeout=self.timeout)

            payload = {
                "_username": self.email,
                "_password": self.password,
                "redir": ""
            }

            response = self.req_session.post(
                login_post_url, data=payload, headers=headers, allow_redirects=True, timeout=self.timeout
            )
            if "skip-wrapper" in response.text:
                raise AuthenticationError("Please complete E-learning on mymcd.eu before proceeding.")

            self.phpsessid = self.req_session.cookies.get("PHPSESSID")
            self.mymcd2_session = self.req_session.cookies.get("mymcd2_session")

            if not self.mymcd2_session:
                headers["Referer"] = response.url
                self.req_session.get(dashboard_url, headers=headers, timeout=self.timeout)
                self.mymcd2_session = self.req_session.cookies.get("mymcd2_session")

            if not self.phpsessid or not self.mymcd2_session:
                raise AuthenticationError("Failed to obtain required session cookies. Please check your credentials.")

            self._establish_context()

        except Exception as e:
            logger.error(f"Error during MyMcdAPI login: {e}")
            raise

    def _get_headers_json(self) -> Dict[str, str]:
        return {
            "Cookie": f"PHPSESSID={self.phpsessid}; mymcd2_session={self.mymcd2_session}",
            "User-Agent": "Mozilla/5.0 (MyMcd-Wrapper/2.0)",
            "Accept": "application/json"
        }

    def _get_headers_html(self) -> Dict[str, str]:
        return {
            "Cookie": f"PHPSESSID={self.phpsessid}; mymcd2_session={self.mymcd2_session}",
            "User-Agent": "Mozilla/5.0 (MyMcd-Wrapper/2.0)",
            "Accept": "text/html"
        }

    def _request_json(self, method: str, url: str, **kwargs) -> Any:
        headers = self._get_headers_json()
        if 'headers' in kwargs:
            headers.update(kwargs.pop('headers'))
        kwargs.setdefault('timeout', self.timeout)
        response = self.req_session.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()

    def _request_html(self, method: str, url: str, **kwargs) -> str:
        headers = self._get_headers_html()
        if 'headers' in kwargs:
            headers.update(kwargs.pop('headers'))
        kwargs.setdefault('timeout', self.timeout)
        response = self.req_session.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.text

    def _establish_context(self) -> None:
        """Fetches /api/user/me to set the user_id, restaurant_id, and dynamically resolve role."""
        me_data = self.get_me()
        self.user_id = me_data.get("id")

        primary_restaurant = me_data.get("primaryRestaurant", {})
        self.restaurant_id = primary_restaurant.get("id")
        self.restaurant_code = primary_restaurant.get("code")

        position_id = me_data.get("position", {}).get("id")
        if position_id in self.MANAGER_POSITION_IDS:
            self.role = Role.MANAGER
        elif position_id in self.CT_POSITION_IDS:
            self.role = Role.CT
        else:
            self.role = Role.CREW

        logger.info(f"Authenticated as User ID: {self.user_id} | Role: {self.role.name} | Restaurant ID: {self.restaurant_id}")

    # ==========================================
    # CREW LEVEL ENDPOINTS (Base Access)
    # ==========================================

    @requires_role(Role.CREW)
    def get_me(self) -> Dict[str, Any]:
        """Fetch current authenticated user profile and restaurant context."""
        return self._request_json("GET", "https://next.mymcd.eu/api/user/me")

    @requires_role(Role.CREW)
    def get_default_codes(self) -> Dict[str, Any]:
        """Fetch global application definitions (contract types, positions, restaurants, verifications, etc.)."""
        return self._request_json("GET", "https://next.mymcd.eu/api/default-codes")

    @requires_role(Role.CREW)
    def get_events(self, from_date: str, to_date: str, restaurant_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch events (e.g. public holidays, 2x pay days). Dates in YYYY-MM-DD format."""
        target = restaurant_id or self.restaurant_id
        url = f"https://next.mymcd.eu/api/events/{target}?from={from_date}&to={to_date}"
        return self._request_json("GET", url)

    @requires_role(Role.CREW)
    def get_employee_details(self, employee_id: int) -> Dict[str, Any]:
        """Fetch basic details for an employee. (Phone/Email redacted unless caller is Manager)."""
        url = f"https://next.mymcd.eu/api/employees/single/{employee_id}?simple=false"
        return self._request_json("GET", url)

    @requires_role(Role.CREW)
    def get_employees_data_list(
        self,
        limit: int = 100,
        skip: int = 0,
        order_by: str = "surname",
        order_dir: str = "asc",
        search: str = ""
    ) -> Dict[str, Any]:
        """Fetch paginated employee list for the restaurant."""
        params_dict = {
            "orderBy": order_by,
            "orderByDir": order_dir,
            "skip": skip,
            "limit": limit,
            "search": search,
            "filter": '{"state":null,"positionId":null}'
        }
        encoded_params = urllib.parse.quote(json.dumps(params_dict))
        url = f"https://mymcd.eu/api/employees/{self.restaurant_id}/getData?params={encoded_params}"
        return self._request_json("GET", url)

    # ==========================================
    # MIXED PERMISSION ENDPOINTS (Self vs Others)
    # ==========================================

    @requires_role(Role.CREW)
    def get_employee_shifts(self, from_date: str, to_date: str, employee_id: Optional[int] = None) -> Dict[str, Any]:
        """Fetch shift plans. Requires MANAGER role if querying an employee other than self."""
        target = employee_id or self.user_id
        if target != self.user_id and self.role < Role.MANAGER:
            raise PermissionDeniedError("MANAGER role required to view shifts of other employees.")
        url = f"https://next.mymcd.eu/api/shifts-employee/{target}?from={from_date}&to={to_date}"
        return self._request_json("GET", url)

    @requires_role(Role.CREW)
    def get_profile_verifications(self, employee_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Fetch training plans and all verifications for an employee.
        Requires CT role if querying an employee other than self.
        Extracts verification attempt IDs from HTML links and data attributes for detail lookups.
        """
        target = employee_id or self.user_id
        if target != self.user_id and self.role < Role.CT:
            raise PermissionDeniedError("CT role required to view verifications of other employees.")

        default_codes = self.get_default_codes()
        master_verifications = default_codes.get("verifications", [])

        verifications_map: Dict[Any, Dict[str, Any]] = {}
        name_to_id_lookup: Dict[str, int] = {}

        for v in master_verifications:
            v_id = v["id"]
            name_cs = v.get("name", {}).get("cs", "").strip()
            name_sk = v.get("name", {}).get("sk", "").strip()
            display_name = name_cs or name_sk

            verifications_map[v_id] = {
                "id": v_id,
                "name": display_name,
                "plan": None,
                "last_verification_date": None,
                "next_verification_date": None,
                "status": "Nepřiřazeno",
                "is_verified": False,
                "attempt_id": None
            }

            if name_cs:
                name_to_id_lookup[name_cs.lower()] = v_id
            if name_sk:
                name_to_id_lookup[name_sk.lower()] = v_id

        url = f"https://mymcd.eu/api/profile/refresh/{target}/{self.restaurant_code}/verification/"
        html_content = self._request_html("GET", url)
        soup = BeautifulSoup(html_content, "html.parser")

        tables = soup.find_all("table", class_="courses-table")
        training_plans = []

        # 1. Parse Training Plans (Table 1)
        if len(tables) > 0:
            tp_tbody = tables[0].find("tbody")
            if tp_tbody:
                for row in tp_tbody.find_all("tr"):
                    cols = row.find_all("td")
                    if len(cols) >= 4:
                        name = cols[0].get_text(strip=True)
                        period_str = cols[1].get_text(strip=True)

                        start_date, end_date = None, None
                        if "-" in period_str:
                            parts = period_str.split("-")
                            start_date = parts[0].strip()
                            end_date = parts[1].strip()
                        else:
                            start_date = period_str

                        progress_bar = cols[2].find("div", class_="progress-bar")
                        progress = progress_bar.text.strip() if progress_bar else "0%"
                        status = cols[3].get_text(strip=True)

                        training_plans.append({
                            "name": name,
                            "start_date": start_date,
                            "end_date": end_date,
                            "progress": progress,
                            "status": status
                        })

        # 2. Parse Verifications (Table 2) and extract attempt IDs
        if len(tables) > 1:
            verif_tbody = tables[1].find("tbody")
            if verif_tbody:
                for row in verif_tbody.find_all("tr"):
                    cols = row.find_all("td")
                    if len(cols) >= 5:
                        raw_name = cols[0].get_text(strip=True)
                        plan = cols[1].get_text(strip=True)

                        last_date = cols[2].get_text(strip=True)
                        if last_date == "N/A":
                            last_date = None

                        next_date = cols[3].get_text(strip=True)
                        status = cols[4].get_text(strip=True)

                        is_verified = "Verifikován" in status or "Verifikovaný" in status

                        attempt_id = None
                        for link in row.find_all("a", href=True):
                            href = link.get("href", "")
                            id_match = re.search(r'/verification/(?:attempt/)?(\d+)', href)
                            if id_match:
                                attempt_id = int(id_match.group(1))
                                break

                        if not attempt_id:
                            for element in row.find_all(True):
                                for attr_name, attr_val in element.attrs.items():
                                    if attr_name.startswith("data-") and isinstance(attr_val, str):
                                        id_match = re.search(r'^(\d{4,})$', attr_val)
                                        if id_match:
                                            attempt_id = int(id_match.group(1))
                                            break
                                if attempt_id:
                                    break

                        match_id = name_to_id_lookup.get(raw_name.lower())

                        update_data = {
                            "plan": plan,
                            "last_verification_date": last_date,
                            "next_verification_date": next_date,
                            "status": status,
                            "is_verified": is_verified,
                            "attempt_id": attempt_id
                        }

                        if match_id and match_id in verifications_map:
                            verifications_map[match_id].update(update_data)
                        else:
                            verifications_map[f"unknown_{raw_name}"] = {
                                "id": None,
                                "name": raw_name,
                                **update_data
                            }

        return {
            "training_plans": training_plans,
            "verifications": list(verifications_map.values())
        }

    # ==========================================
    # MANAGER LEVEL ENDPOINTS (Strict Access)
    # ==========================================

    @requires_role(Role.MANAGER)
    def get_restaurant_shifts(self, from_date: str, to_date: str) -> Dict[str, Any]:
        """Fetch all shifts across the restaurant for a date range."""
        url = f"https://next.mymcd.eu/api/shifts-restaurant/{self.restaurant_id}?from={from_date}&to={to_date}"
        return self._request_json("GET", url)

    @requires_role(Role.MANAGER)
    def get_restaurant_floorplan(self, date: str) -> Dict[str, Any]:
        """Fetch full restaurant floorplan for a specific date (YYYY-MM-DD)."""
        url = f"https://next.mymcd.eu/api/shifts-restaurant/{self.restaurant_id}/floorplan/{date}"
        return self._request_json("GET", url)

    @requires_role(Role.MANAGER)
    def get_restaurant_availability(self, from_date: str, to_date: str) -> List[Dict[str, Any]]:
        """Fetch employee availability over a date range."""
        url = f"https://next.mymcd.eu/api/availability-restaurant/{self.restaurant_id}?from={from_date}&to={to_date}"
        return self._request_json("GET", url)

    @requires_role(Role.MANAGER)
    def get_employee_shift_stats(self, year: int, month: int, employee_ids: List[int]) -> List[Dict[str, Any]]:
        """Fetch worked hours and shift stats for specific employees."""
        url = f"https://next.mymcd.eu/api/shifts-employees-stats?year={year}&month={month}"
        for emp_id in employee_ids:
            url += f"&employees%5B%5D={emp_id}"
        return self._request_json("GET", url)

    @requires_role(Role.MANAGER)
    def get_expiring_verifications(self, from_date: str, to_date: str, employee_ids: List[int]) -> Dict[str, Any]:
        """Fetch expiring verifications for a list of employees in a date range."""
        url = f"https://next.mymcd.eu/api/training/expiring-verifications/{self.restaurant_id}?from={from_date}&to={to_date}"
        for emp_id in employee_ids:
            url += f"&employees%5B%5D={emp_id}"
        return self._request_json("GET", url)

    @requires_role(Role.MANAGER)
    def get_verification_attempt(self, attempt_id: int) -> Dict[str, Any]:
        """Fetch details for a single verification attempt (verifier name, score, date)."""
        url = f"https://next.mymcd.eu/api/training/verification/attempt/{attempt_id}"
        return self._request_json("GET", url)

    @requires_role(Role.MANAGER)
    def _get_all_employees(self) -> List[Dict[str, Any]]:
        """Helper to paginate through all employees in the restaurant."""
        all_employees = []
        skip = 0
        limit = 100
        while True:
            data = self.get_employees_data_list(limit=limit, skip=skip)
            employees = data.get("data", [])
            if not employees:
                break
            all_employees.extend(employees)
            if len(employees) < limit:
                break
            skip += limit
        return all_employees

    @requires_role(Role.MANAGER)
    def get_all_employees_verification_summaries(
        self,
        include_unverified: bool = False,
        rate_limit: float = 0.15,
        max_workers: int = 12
    ) -> List[Dict[str, Any]]:
        """
        Iterate all restaurant employees and compile their verification records.
        For completed verifications with attempt IDs, fetches the verifier name and score.
        """
        all_employees = self._get_all_employees()
        default_codes = self.get_default_codes()

        position_map = {}
        for p in default_codes.get("positions", []):
            position_map[p.get("id")] = p.get("name", {}).get("cs") or p.get("name", {}).get("sk") or "Unknown"

        results = []
        total = len(all_employees)
        excluded_positions = {"Shift Leader", "Department Leader", "Restaurant manager", "Supervisor"}

        def process_employee(idx: int, emp: Dict[str, Any]) -> List[Dict[str, Any]]:
            emp_results = []
            emp_id = emp.get("id")
            emp_name = f"{emp.get('name', '')} {emp.get('surname', '')}".strip()
            pos_id = emp.get("positionId")
            pos_name = position_map.get(pos_id, "Unknown Position")

            if pos_name in excluded_positions:
                return emp_results

            logger.info(f"[{idx + 1}/{total}] Processing {emp_name} (ID: {emp_id}, Position: {pos_name})...")

            try:
                profile = self.get_profile_verifications(emp_id)
            except Exception as e:
                logger.warning(f"Error fetching verifications for {emp_name}: {e}")
                return emp_results

            for v in profile.get("verifications", []):
                if not include_unverified and not v.get("is_verified"):
                    continue

                entry = {
                    "employee_id": emp_id,
                    "employee_name": emp_name,
                    "position_id": pos_id,
                    "position_name": pos_name,
                    "verification_name": v.get("name", "Unknown"),
                    "verification_date": v.get("last_verification_date"),
                    "verified_by": None,
                    "total_points": None,
                    "max_points": None,
                    "status": v.get("status", ""),
                    "is_verified": v.get("is_verified", False)
                }

                attempt_id = v.get("attempt_id")
                if attempt_id:
                    try:
                        attempt = self.get_verification_attempt(attempt_id)
                        entry["verified_by"] = attempt.get("createdBy")
                        entry["total_points"] = attempt.get("totalPoints")
                        entry["max_points"] = attempt.get("maxPoints")
                        entry["verification_date"] = attempt.get("createdAt")
                        if rate_limit > 0:
                            time.sleep(rate_limit)
                    except Exception as e:
                        logger.warning(f"Error fetching attempt {attempt_id}: {e}")

                emp_results.append(entry)
            return emp_results

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_employee, idx, emp) for idx, emp in enumerate(all_employees)]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    results.extend(res)

        return results
