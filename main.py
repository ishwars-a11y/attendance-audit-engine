"""
Attendance Audit Engine — entry point.

Usage:
  python main.py                          # run for yesterday (cron default)
  python main.py --date 2024-06-10        # run for a specific date
  python main.py --probe                  # discover Jibble endpoints + verify field names
  python main.py --sync-employees         # sync employee list from Jibble to Supabase
  python main.py --backfill --from 2024-05-01 --to 2024-05-31
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _parse_utc(ts_str: str | None):
    """Parse a UTC ISO timestamp string into a timezone-aware datetime. Returns None on failure."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(val) -> date | None:
    """Parse a date value (str 'YYYY-MM-DD' or date or None) to a date object."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except (ValueError, TypeError):
        return None


def _was_active_on(emp: dict, target_date: date) -> bool:
    """True if an employee is within their [joined_date, departed_date] range on target_date."""
    joined   = _parse_date(emp.get("joined_date"))
    departed = _parse_date(emp.get("departed_date"))
    if joined and target_date < joined:
        return False
    if departed and target_date > departed:
        return False
    return True


def run_for_date(target_date: date):
    from engine.jibble import JibbleClient
    from engine import rules, db

    if target_date.weekday() >= 5:
        logger.info(f"Skipping {target_date} (weekend)")
        return

    logger.info(f"Running engine for {target_date}")
    client = JibbleClient()

    # get_timesheets returns one normalized dict per person with embedded leave/holiday:
    #   { member_id, hours_logged, session_count, first_clock_in (UTC str),
    #     last_clock_out (UTC str), has_missing_clockout, leave_type, is_holiday }
    timesheets  = client.get_timesheets(target_date)
    admin_edits = client.get_admin_edits(target_date)

    ts_by_member: dict[str, dict] = {t["member_id"]: t for t in timesheets}
    employees = db.get_active_employees()

    for emp in employees:
        mid = emp["member_id"]

        # Skip employees who hadn't joined yet or have already departed on target_date.
        if not _was_active_on(emp, target_date):
            logger.debug(f"  Skipping {emp.get('jibble_name', mid)} — outside active range on {target_date}")
            continue

        ts  = ts_by_member.get(mid, {})

        hours_logged         = ts.get("hours_logged", 0.0)
        session_count        = ts.get("session_count", 0)
        first_clock_in_str   = ts.get("first_clock_in")
        last_clock_out_str   = ts.get("last_clock_out")
        has_missing_clockout = ts.get("has_missing_clockout", False)
        leave_type           = ts.get("leave_type")
        is_holiday           = ts.get("is_holiday", False)
        is_am_half_day       = ts.get("is_am_half_day", False)
        is_partial_leave     = ts.get("is_partial_leave", False)

        first_clock_in_dt  = _parse_utc(first_clock_in_str)
        last_clock_out_dt  = _parse_utc(last_clock_out_str)

        logger.info(
            f"  [{emp.get('jibble_name', mid)}] "
            f"hours={hours_logged:.2f} sessions={session_count} "
            f"first_in={first_clock_in_str!r} last_out={last_clock_out_str!r} "
            f"missing_co={has_missing_clockout}"
        )

        db.upsert_snapshot({
            "member_id":      mid,
            "snapshot_date":  target_date.isoformat(),
            "hours_logged":   round(hours_logged, 2),
            "session_count":  session_count,
            "first_clock_in": first_clock_in_str,
            "last_clock_out": last_clock_out_str,
            "leave_type":     leave_type,
            "pulled_at":      datetime.now(timezone.utc).isoformat(),
        })

        is_full_time       = emp["employment_type"] == "full_time"
        daily_target_hrs   = emp.get("daily_target_hrs", 8)
        has_leave          = leave_type is not None and not is_holiday
        has_full_day_leave = has_leave and not is_partial_leave
        has_am_half_day    = is_am_half_day

        # Consecutive absence: look up the previous working day's snapshot
        prev_day = target_date - timedelta(days=1)
        while prev_day.weekday() >= 5:
            prev_day -= timedelta(days=1)
        prev_snap        = db.get_snapshot(mid, prev_day)
        is_absent_today  = hours_logged == 0 and not has_leave and not is_holiday
        was_absent_prev  = (
            float(prev_snap.get("hours_logged", 0)) == 0
            and prev_snap.get("leave_type") is None
        )

        _flag(mid, target_date, "Missing Clock-Out",
              *rules.check_missing_clockout(has_missing_clockout, first_clock_in_dt))
        _flag(mid, target_date, "Unexcused Absence",
              *rules.check_unexcused_absence(hours_logged, has_leave, is_holiday))
        _flag(mid, target_date, "Consecutive Absence",
              *rules.check_consecutive_absence(is_absent_today, was_absent_prev))
        _flag(mid, target_date, "Excessive Hours",
              *rules.check_excessive_hours(hours_logged, is_full_time))
        _flag(mid, target_date, "Late / No Start",
              *rules.check_late_no_start(
                  first_clock_in_dt, hours_logged, target_date,
                  has_full_day_leave, has_am_half_day, is_full_time))
        _flag(mid, target_date, "Long Breaks",
              *rules.check_long_breaks(
                  first_clock_in_dt, last_clock_out_dt, hours_logged, is_full_time))
        # "Excessive Breaks" and "Early Departure" checks removed as low-signal /
        # high-noise.  Historical anomalies of those types remain in the DB but
        # are filtered out of the dashboard.

    # --- Weekly summary on Fridays ---
    if target_date.weekday() == 4:
        _run_weekly_summary(target_date, employees)

    # --- Admin edits ---
    for edit in admin_edits:
        db.upsert_admin_edit({
            "member_id":      edit.get("personId"),
            "edit_date":      target_date.isoformat(),
            "original_hours": edit.get("originalHours"),
            "edited_hours":   edit.get("editedHours"),
            "edited_at":      edit.get("editedAt"),
        })

    logger.info(f"Done for {target_date}")


def _flag(member_id: str, target_date: date, anomaly_type: str, flagged: bool, detail: str):
    from engine import db
    if flagged:
        if not db.anomaly_exists(member_id, target_date, anomaly_type):
            db.insert_anomaly({
                "member_id":    member_id,
                "anomaly_date": target_date.isoformat(),
                "anomaly_type": anomaly_type,
                "detail":       detail,
            })
            logger.info(f"  Anomaly flagged: {anomaly_type} — {member_id} — {detail}")
    else:
        # Resolve: if a previous run flagged this and it's no longer true, remove it.
        # This is important for transient states like Missing Clock-Out — the 5 PM run
        # may flag it while someone is still working; the 9 PM run should clear it once
        # they've clocked out.
        if db.anomaly_exists(member_id, target_date, anomaly_type):
            db.delete_anomaly(member_id, target_date, anomaly_type)
            logger.info(f"  Anomaly resolved: {anomaly_type} — {member_id}")


def _run_weekly_summary(friday: date, employees: list[dict]):
    from engine import rules, db
    monday = friday - timedelta(days=4)
    logger.info(f"Weekly summary: {monday} to {friday}")

    for emp in employees:
        mid       = emp["member_id"]

        # Pro-rate the weekly target by the working days in [monday, friday] that
        # fall within the employee's [joined_date, departed_date] active range.
        # Employees with no overlap at all are skipped entirely for this week.
        active_days = sum(
            1 for i in range(5)
            if _was_active_on(emp, monday + timedelta(days=i))
        )
        if active_days == 0:
            continue

        snapshots = db.get_snapshots_for_week(mid, monday, friday)

        total_hours      = sum(float(s["hours_logged"]) for s in snapshots)
        leave_days       = sum(1 for s in snapshots if s.get("leave_type") is not None)
        # Effective target = active_days × daily_target − leave_days × daily_target
        effective_target = max(
            0.0,
            (active_days - leave_days) * emp["daily_target_hrs"]
        )
        deficit = max(0.0, effective_target - total_hours)

        db.upsert_weekly_summary({
            "member_id":        mid,
            "week_start":       monday.isoformat(),
            "week_end":         friday.isoformat(),
            "total_hours":      round(total_hours, 2),
            "leave_days":       leave_days,
            "effective_target": round(effective_target, 2),
            "deficit":          round(deficit, 2),
        })

        _flag(mid, friday, "Weekly Deficit",
              *rules.check_weekly_deficit(total_hours, effective_target))

        late_count = sum(
            1 for t in db.get_week_anomaly_types(mid, monday, friday)
            if t == "Late / No Start"
        )
        _flag(mid, friday, "Chronic Late Starter",
              *rules.check_chronic_late(late_count))


def sync_employees():
    from engine.jibble import JibbleClient
    from engine import db
    from config import EMPLOYEES

    client  = JibbleClient()
    members = client.get_members()

    rows = []
    for member in members:
        jibble_name = member.get("full_name", "")
        member_id   = member.get("member_id", "")

        if not member_id or not jibble_name:
            logger.warning(f"Skipping member with missing id/name: {member}")
            continue

        config = EMPLOYEES.get(jibble_name)
        if config is None:
            logger.warning(f"Unknown Jibble member: '{jibble_name}' (id={member_id}) — add to config.py")
            continue

        rows.append({
            "member_id":        member_id,
            "jibble_name":      jibble_name,
            "display_name":     config["display_name"],
            "employment_type":  config["employment_type"],
            "weekly_target_hrs": config["weekly_target_hrs"],
            "daily_target_hrs":  config["daily_target_hrs"],
            "timezone":          config["timezone"],
            "is_excluded":       config.get("is_excluded", False),
            "is_active":         config.get("is_active", True),
            "joined_date":       config.get("joined_date"),     # None = joined before tracking
            "departed_date":     config.get("departed_date"),   # None = still active
        })

    db.upsert_employees(rows)
    logger.info(f"Synced {len(rows)} employees to Supabase")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Attendance Audit Engine")
    parser.add_argument("--probe", action="store_true",
                        help="Discover Jibble endpoint paths + verify field names")
    parser.add_argument("--probe-member", type=str, default=None, metavar="NAME",
                        help="Dump raw Jibble timeOff data for a specific employee over "
                             "--from / --to range.  Use to diagnose missing leave days.")
    parser.add_argument("--sync-employees", action="store_true",
                        help="Sync employee list from Jibble to Supabase")
    parser.add_argument("--date", type=str, default=None,
                        help="Run for a specific date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--lookback", type=int, default=0, metavar="N",
                        help="Also re-sync the N most recent working days before the "
                             "target date. Picks up retroactive leave approvals that "
                             "were entered in Jibble after the original sync.")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill a date range. Requires --from and --to.")
    parser.add_argument("--from", dest="from_date", type=str)
    parser.add_argument("--to",   dest="to_date",   type=str)
    args = parser.parse_args()

    if args.probe:
        from engine.jibble import JibbleClient
        JibbleClient().probe()
        return

    if args.probe_member:
        from engine.jibble import JibbleClient
        if not args.from_date or not args.to_date:
            print("--probe-member requires --from and --to")
            sys.exit(1)
        JibbleClient().probe_member_leave(
            args.probe_member,
            date.fromisoformat(args.from_date),
            date.fromisoformat(args.to_date),
        )
        return

    if args.sync_employees:
        sync_employees()
        return

    if args.backfill:
        if not args.from_date or not args.to_date:
            print("--backfill requires --from and --to")
            sys.exit(1)
        current = date.fromisoformat(args.from_date)
        end     = date.fromisoformat(args.to_date)
        while current <= end:
            run_for_date(current)
            current += timedelta(days=1)
        return

    target = date.fromisoformat(args.date) if args.date else date.today()

    if args.lookback > 0:
        # Collect the N most-recent working days before target (oldest first)
        # so weekly summaries are (re)computed in chronological order.
        past_days: list[date] = []
        d = target - timedelta(days=1)
        while len(past_days) < args.lookback:
            if d.weekday() < 5:
                past_days.append(d)
            d -= timedelta(days=1)
        for d in reversed(past_days):
            logger.info(f"Lookback re-sync: {d}")
            run_for_date(d)

    run_for_date(target)


if __name__ == "__main__":
    main()
