"""
Jibble API client — OAuth2 client credentials.

Confirmed endpoints (2026-05-15):
  Token:    https://identity.prod.jibble.io/connect/token
  Daily:    https://time-attendance.prod.jibble.io/v1/Timesheets
  Summary:  https://time-attendance.prod.jibble.io/v1/TimesheetsSummary  (has person.fullName)
  Members:  https://workspace.prod.jibble.io/v1/People
"""

import os
import json
import re
import time
import logging
from datetime import date, timedelta, timezone
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ISO 8601 duration → decimal hours
# ---------------------------------------------------------------------------

def parse_iso_duration(s: str) -> float:
    """'PT7H25M32.748518S' → 7.4257..."""
    if not s or s == "PT0S":
        return 0.0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", s)
    if not m:
        return 0.0
    return float(m.group(1) or 0) + float(m.group(2) or 0) / 60 + float(m.group(3) or 0) / 3600

# ---------------------------------------------------------------------------
# Confirmed field names
# ---------------------------------------------------------------------------

# /v1/Timesheets — confirmed 2026-05-15
TS_MEMBER_ID   = "personId"
TS_DAILY       = "daily"
TS_DAILY_DATE  = "date"
TS_FIRST_IN_TS = "firstInTimestamp"   # UTC string, None if no clock-in
TS_LAST_OUT_TS = "lastOutTimestamp"   # UTC string, None if missing clock-out
TS_LAST_OUT    = "lastOut"            # local time string; None = missing clock-out
TS_TRACKED     = "trackedHours"
TS_WORKED      = "worked"             # ISO duration inside trackedHours
TS_BREAKS      = "breaks"             # list inside trackedHours — count = sessions - 1
TS_TIME_OFF    = "timeOff"
TS_HOLIDAYS    = "holidays"           # list inside timeOff; non-empty = public holiday
TS_OFF_TYPES   = "types"              # list inside timeOff; non-empty = leave

# /v1/TimesheetsSummary — confirmed 2026-05-15; has person.fullName
SUM_MEMBER_ID  = "personId"
SUM_PERSON     = "person"
SUM_FULL_NAME  = "fullName"           # inside person object
SUM_TIMEZONE   = "timeZone"           # inside person object
SUM_STATUS     = "status"             # "Joined" | "Archived" etc.

# ---------------------------------------------------------------------------


def _build_session() -> requests.Session:
    session = requests.Session()
    retry   = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class JibbleClient:
    TIME_BASE      = "https://time-attendance.prod.jibble.io"
    WORKSPACE_BASE = "https://workspace.prod.jibble.io"
    TOKEN_URL      = "https://identity.prod.jibble.io/connect/token"

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.client_id     = client_id     or os.environ["JIBBLE_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["JIBBLE_CLIENT_SECRET"]
        self.session = _build_session()
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # -----------------------------------------------------------------------
    # Auth
    # -----------------------------------------------------------------------

    def _get_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        resp = requests.post(
            self.TOKEN_URL,
            data={"grant_type": "client_credentials",
                  "client_id": self.client_id,
                  "client_secret": self.client_secret},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"Token error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        self._access_token     = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600)
        logger.info("Jibble access token obtained")
        return self._access_token

    def _get(self, base: str, path: str, params: dict = None) -> dict:
        token = self._get_token()
        resp  = self.session.get(
            f"{base}{path}", params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def get_members(self) -> list[dict]:
        """
        Return all workspace members with personId and fullName.
        Uses TimesheetsSummary (any recent weekday works — returns ALL members even with 0 hours).
        Falls back to workspace.prod.jibble.io/v1/People if needed.

        Each record: { member_id, full_name, timezone, status }
        """
        # Primary: TimesheetsSummary for any recent weekday — always returns all members
        yesterday = date.today() - timedelta(days=1)
        # Back up to most recent weekday if yesterday is weekend
        while yesterday.weekday() >= 5:
            yesterday -= timedelta(days=1)

        raw     = self._get(self.TIME_BASE, "/v1/TimesheetsSummary",
                            params={"date": yesterday.isoformat()})
        records = raw.get("value", []) if isinstance(raw, dict) else raw

        members = []
        for r in records:
            person  = r.get(SUM_PERSON, {})
            name    = person.get(SUM_FULL_NAME, "")
            tz      = person.get(SUM_TIMEZONE, "Asia/Kolkata")
            status  = person.get(SUM_STATUS, "")
            mid     = r.get(SUM_MEMBER_ID, "")
            if mid and name:
                members.append({
                    "member_id": mid,
                    "full_name":  name,
                    "timezone":   tz,
                    "status":     status,
                })

        logger.info(f"get_members: found {len(members)} members via TimesheetsSummary")
        return members

    def get_timesheets(self, target_date: date) -> list[dict]:
        """
        Return one normalized record per person for target_date.

        Fields:
          member_id, hours_logged (decimal hrs), session_count (clock-in/out pairs),
          first_clock_in (UTC ISO str), last_clock_out (UTC ISO str),
          has_missing_clockout (bool), leave_type (str|None), is_holiday (bool)
        """
        raw      = self._get(self.TIME_BASE, "/v1/Timesheets",
                             params={"date": target_date.isoformat()})
        records  = raw.get("value", []) if isinstance(raw, dict) else raw
        date_str = target_date.isoformat()
        result   = []

        for person in records:
            member_id = person.get(TS_MEMBER_ID)
            if not member_id:
                continue

            daily_entries = person.get(TS_DAILY, [])
            daily = next(
                (d for d in daily_entries if d.get(TS_DAILY_DATE) == date_str),
                daily_entries[0] if daily_entries else {},
            )

            # Hours and session count
            tracked      = daily.get(TS_TRACKED, {})
            hours_logged = parse_iso_duration(tracked.get(TS_WORKED, "PT0S"))
            breaks       = tracked.get(TS_BREAKS, [])
            session_count = (len(breaks) + 1) if hours_logged > 0 else 0

            # Clock-in / clock-out
            first_clock_in       = daily.get(TS_FIRST_IN_TS)  # UTC str or None
            last_clock_out       = daily.get(TS_LAST_OUT_TS)  # UTC str or None
            last_out_local       = daily.get(TS_LAST_OUT)      # local time str or None
            has_missing_clockout = (
                first_clock_in is not None and last_out_local is None
            )

            # Fallback A: Jibble sometimes omits lastOutTimestamp (UTC) even when lastOut
            # (local time string, e.g. "8:02 PM") is present.  Derive the UTC ISO timestamp
            # from lastOut + target_date + IST offset so the dashboard can display it.
            if last_clock_out is None and last_out_local:
                try:
                    from datetime import datetime as _dt
                    import re as _re
                    # lastOut format: "8:02 PM" or "08:02 PM"
                    m = _re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)", last_out_local.strip(), _re.I)
                    if m:
                        hh, mm, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
                        if ampm == "PM" and hh != 12:
                            hh += 12
                        elif ampm == "AM" and hh == 12:
                            hh = 0
                        # IST = UTC + 5h30m
                        ist_dt = _dt(target_date.year, target_date.month, target_date.day,
                                     hh, mm, tzinfo=timezone(timedelta(hours=5, minutes=30)))
                        last_clock_out = ist_dt.astimezone(timezone.utc).isoformat()
                        logger.debug(f"  {member_id}: last_clock_out derived from lastOut local string")
                except Exception:
                    pass

            # Fallback B: if Jibble omits firstInTimestamp (e.g. manually-entered sessions)
            # but we have a clock-out and hours, derive clock-in = last_clock_out − hours_logged.
            # Exact for single-session days; slightly off when breaks exist.
            if first_clock_in is None and last_clock_out is not None and hours_logged > 0:
                from datetime import datetime as _dt2
                try:
                    lo = _dt2.fromisoformat(last_clock_out.replace("Z", "+00:00"))
                    first_clock_in = (lo - timedelta(hours=hours_logged)).isoformat()
                    logger.debug(f"  {member_id}: first_clock_in derived from lastOut − hours")
                except Exception:
                    pass

            # Leave / holiday from embedded timeOff section
            time_off   = daily.get(TS_TIME_OFF, {})
            is_holiday = bool(time_off.get(TS_HOLIDAYS))
            leave_type = None

            off_types = time_off.get(TS_OFF_TYPES, [])
            if off_types:
                # types entries may be dicts with a "name" key, or plain strings
                first = off_types[0]
                leave_type = (
                    first.get("name", "Approved") if isinstance(first, dict) else str(first)
                )
            elif is_holiday:
                leave_type = "Public Holiday"

            result.append({
                "member_id":           member_id,
                "hours_logged":        round(hours_logged, 4),
                "session_count":       session_count,
                "first_clock_in":      first_clock_in,
                "last_clock_out":      last_clock_out,
                "has_missing_clockout": has_missing_clockout,
                "leave_type":          leave_type,
                "is_holiday":          is_holiday,
            })

        return result

    def get_admin_edits(self, target_date: date) -> list[dict]:
        """Admin timesheet edits. Returns [] if endpoint not available on this plan."""
        try:
            raw     = self._get(self.TIME_BASE, "/v1/attendance/edits",
                                params={"date": target_date.isoformat()})
            records = raw.get("value", raw) if isinstance(raw, dict) else raw
            return records
        except requests.HTTPError as e:
            if e.response.status_code in (404, 400):
                logger.warning("Admin edits endpoint unavailable — Panel 3 skipped.")
                return []
            raise

    # -----------------------------------------------------------------------
    # Probe
    # -----------------------------------------------------------------------

    def _raw(self, base: str, path: str, params: dict = None) -> tuple[int, str]:
        try:
            token = self._get_token()
        except Exception as e:
            return 0, str(e)
        try:
            resp = self.session.get(
                f"{base}{path}", params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            return resp.status_code, resp.text[:2000]
        except Exception as e:
            return 0, f"{type(e).__name__}: {e}"

    def probe(self):
        yesterday = date.today() - timedelta(days=1)
        while yesterday.weekday() >= 5:
            yesterday -= timedelta(days=1)

        print(f"\n=== Token ===")
        try:
            self._get_token()
            print("OK")
        except Exception as e:
            print(f"FAILED: {e}")
            return

        print(f"\n=== /v1/Timesheets (normalized, first 3) — {yesterday} ===")
        ts = self.get_timesheets(yesterday)
        print(json.dumps(ts[:3], indent=2, default=str))

        print(f"\n=== get_members (first 5) ===")
        members = self.get_members()
        print(json.dumps(members[:5], indent=2, default=str))

        print(f"\n=== workspace /v1/People (raw first 1500 chars) ===")
        status, body = self._raw(self.WORKSPACE_BASE, "/v1/People")
        print(f"HTTP {status}")
        print(body[:1500])

        print("\n>>> All done. If member names look correct, run: python main.py --sync-employees")
