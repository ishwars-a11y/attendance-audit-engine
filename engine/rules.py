"""
Anomaly detection rules — pure functions, no DB calls.

All functions return a (flagged: bool, detail: str) tuple.
detail is the human-readable string shown in the dashboard's "Detail" column.
"""

from datetime import datetime, time
from typing import Optional
import pytz

from config import (
    IST,
    WORK_WINDOW_START_HOUR,
    EXCESSIVE_HOURS_THRESHOLD,
    EXCESSIVE_SESSIONS_THRESHOLD,
    AT_RISK_BUFFER_HRS,
)


# ---------------------------------------------------------------------------
# Daily anomalies
# ---------------------------------------------------------------------------

def check_missing_clockout(
    has_missing_clockout: bool,
    first_clock_in: Optional[datetime] = None,
) -> tuple[bool, str]:
    if has_missing_clockout:
        time_str = _fmt(first_clock_in) if first_clock_in else "unknown time"
        return True, f"Clocked in at {time_str} IST, no clock-out recorded"
    return False, ""


def check_unexcused_absence(
    hours_logged: float,
    has_leave: bool,
    is_holiday: bool,
) -> tuple[bool, str]:
    if hours_logged == 0 and not has_leave and not is_holiday:
        return True, "Zero hours, no leave or holiday"
    return False, ""


def check_excessive_hours(
    hours_logged: float,
    is_full_time: bool,
) -> tuple[bool, str]:
    if is_full_time and hours_logged > EXCESSIVE_HOURS_THRESHOLD:
        return True, f"{hours_logged:.1f} hrs logged"
    return False, ""


def check_excessive_breaks(
    session_count: int,
    is_full_time: bool,
) -> tuple[bool, str]:
    if is_full_time and session_count > EXCESSIVE_SESSIONS_THRESHOLD:
        return True, f"{session_count} sessions"
    return False, ""


def check_late_no_start(
    first_clock_in: Optional[datetime],
    hours_logged: float,
    has_full_day_leave: bool,
    has_am_half_day_leave: bool,
    is_full_time: bool,
) -> tuple[bool, str]:
    """
    Flag if no clock-in before 11 AM and no approved leave.
    Half-day AM leave suppresses this flag (employee legitimately starts later).
    """
    if not is_full_time:
        return False, ""
    if has_full_day_leave or has_am_half_day_leave:
        return False, ""
    if hours_logged == 0:
        # Already flagged as Unexcused Absence — don't double-flag
        return False, ""

    if first_clock_in is None:
        return True, "No clock-in recorded"

    clock_in_ist = first_clock_in.astimezone(IST)
    if clock_in_ist.hour >= WORK_WINDOW_START_HOUR:
        return True, f"First clock-in at {_fmt(first_clock_in)} IST"
    return False, ""


# ---------------------------------------------------------------------------
# Weekly anomaly
# ---------------------------------------------------------------------------

def check_weekly_deficit(
    total_hours: float,
    effective_target: float,
) -> tuple[bool, str]:
    if total_hours < effective_target:
        deficit = effective_target - total_hours
        return True, f"{total_hours:.1f} hrs logged, {deficit:.1f} hrs short"
    return False, ""


# ---------------------------------------------------------------------------
# Panel 1 status logic
# ---------------------------------------------------------------------------

def weekly_status(total_hours: float, effective_target: float) -> str:
    """Returns 'Met', 'At Risk', or 'Deficit'."""
    if total_hours >= effective_target:
        return "Met"
    if total_hours >= effective_target - AT_RISK_BUFFER_HRS:
        return "At Risk"
    return "Deficit"


# ---------------------------------------------------------------------------
# Weekly trend (Panel 4)
# ---------------------------------------------------------------------------

def weekly_trend(hours_this_week: float, hours_last_week: float) -> str:
    """
    Up / Down / Stable.
    Threshold: more than 2 hours difference = Up or Down.
    Less than or equal to 2 hours = Stable.
    """
    delta = hours_this_week - hours_last_week
    if delta > 2:
        return "Up"
    if delta < -2:
        return "Down"
    return "Stable"


# ---------------------------------------------------------------------------
# Effective target calculation
# ---------------------------------------------------------------------------

def effective_weekly_target(
    base_target_hrs: int,
    daily_target_hrs: int,
    leave_days: int,
) -> float:
    return max(0.0, base_target_hrs - (leave_days * daily_target_hrs))


# ---------------------------------------------------------------------------

def _fmt(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%I:%M %p")
