"""
Anomaly detection rules — pure functions, no DB calls.

All functions return a (flagged: bool, detail: str) tuple.
detail is the human-readable string shown in the dashboard's "Detail" column.
"""

from datetime import date, datetime, time as time_type
from typing import Optional
import pytz

from config import (
    IST,
    WORK_WINDOW_START_HOUR,
    WORK_WINDOW_START_MINUTE,
    OLD_WORK_WINDOW_START_HOUR,
    OLD_WORK_WINDOW_START_MINUTE,
    SHIFT_CHANGE_DATE,
    EARLY_DEPARTURE_HOUR,
    MAX_BREAK_HOURS,
    EXCESSIVE_HOURS_THRESHOLD,
    EXCESSIVE_SESSIONS_THRESHOLD,
    AT_RISK_BUFFER_HRS,
    WEEKLY_DEFICIT_MIN_SHORTFALL_HRS,
    CHRONIC_LATE_THRESHOLD,
)


def _late_cutoff_for(snapshot_date: date) -> time_type:
    """Date-aware late-start cutoff (handles the 2026-05-25 shift change)."""
    if snapshot_date < SHIFT_CHANGE_DATE:
        return time_type(OLD_WORK_WINDOW_START_HOUR, OLD_WORK_WINDOW_START_MINUTE)
    return time_type(WORK_WINDOW_START_HOUR, WORK_WINDOW_START_MINUTE)


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
    snapshot_date: date,
    has_full_day_leave: bool,
    has_am_half_day_leave: bool,
    is_full_time: bool,
) -> tuple[bool, str]:
    """
    Flag if first clock-in is at or after the day's late-start cutoff and no approved leave.
    Cutoff is date-aware:
      - snapshot_date < SHIFT_CHANGE_DATE → 11:10 AM IST
      - snapshot_date >= SHIFT_CHANGE_DATE → 10:10 AM IST
    """
    if not is_full_time:
        return False, ""
    if has_full_day_leave or has_am_half_day_leave:
        return False, ""
    if hours_logged == 0:
        return False, ""
    if first_clock_in is None:
        return True, "No clock-in recorded"

    cutoff = _late_cutoff_for(snapshot_date)
    clock_in_ist = first_clock_in.astimezone(IST)
    if clock_in_ist.time() >= cutoff:
        return True, f"First clock-in at {_fmt(first_clock_in)} IST (cutoff {cutoff.strftime('%I:%M %p')})"
    return False, ""


def check_early_departure(
    last_clock_out: Optional[datetime],
    hours_logged: float,
    daily_target_hrs: float,
    has_full_day_leave: bool,
    is_full_time: bool,
) -> tuple[bool, str]:
    """Flag if clocked out before 6 PM IST and didn't log enough hours."""
    if not is_full_time or has_full_day_leave or hours_logged == 0:
        return False, ""
    if last_clock_out is None:
        return False, ""
    checkout_ist = last_clock_out.astimezone(IST)
    if checkout_ist.hour < EARLY_DEPARTURE_HOUR and hours_logged < daily_target_hrs - 0.5:
        return True, f"Clocked out at {_fmt(last_clock_out)} IST with {hours_logged:.1f} hrs logged"
    return False, ""


def check_long_breaks(
    first_clock_in: Optional[datetime],
    last_clock_out: Optional[datetime],
    hours_logged: float,
    is_full_time: bool,
) -> tuple[bool, str]:
    """Flag if total break time (span − worked) exceeds MAX_BREAK_HOURS."""
    if not is_full_time or hours_logged == 0:
        return False, ""
    if first_clock_in is None or last_clock_out is None:
        return False, ""
    span = (last_clock_out - first_clock_in).total_seconds() / 3600
    break_hours = span - hours_logged
    if break_hours > MAX_BREAK_HOURS:
        return True, f"{break_hours:.1f} hrs in breaks (span {span:.1f} hrs, worked {hours_logged:.1f} hrs)"
    return False, ""


def check_consecutive_absence(
    is_absent_today: bool,
    was_absent_yesterday: bool,
) -> tuple[bool, str]:
    """Flag if employee had zero hours with no leave for 2 consecutive working days."""
    if is_absent_today and was_absent_yesterday:
        return True, "Zero hours logged for 2 consecutive working days"
    return False, ""


# ---------------------------------------------------------------------------
# Weekly anomalies
# ---------------------------------------------------------------------------

def check_weekly_deficit(
    total_hours: float,
    effective_target: float,
) -> tuple[bool, str]:
    """Flag only meaningful shortfalls — a critical anomaly for a few minutes
    short creates alert fatigue and contradicts the dashboard's At-Risk buffer."""
    deficit = effective_target - total_hours
    if deficit > WEEKLY_DEFICIT_MIN_SHORTFALL_HRS:
        return True, f"{total_hours:.1f} hrs logged, {deficit:.1f} hrs short"
    return False, ""


def check_chronic_late(late_count: int) -> tuple[bool, str]:
    """Flag if employee had 3+ Late/No Start anomalies in a single week."""
    if late_count >= CHRONIC_LATE_THRESHOLD:
        return True, f"Late / No Start flagged {late_count} times this week"
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
