"""
Supabase database operations.
All writes use upsert so re-running the engine on the same date is safe.
"""

import os
import logging
from datetime import date, datetime
from typing import Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _client


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------

def upsert_employees(employees: list[dict]):
    get_client().table("employees").upsert(employees, on_conflict="member_id").execute()
    logger.info(f"Upserted {len(employees)} employees")


def get_active_employees() -> list[dict]:
    res = (
        get_client()
        .table("employees")
        .select("*")
        .eq("is_excluded", False)
        .eq("is_active", True)
        .execute()
    )
    return res.data


# ---------------------------------------------------------------------------
# Daily snapshots
# ---------------------------------------------------------------------------

def upsert_snapshot(snapshot: dict):
    res = get_client().table("daily_snapshots").upsert(
        snapshot, on_conflict="member_id,snapshot_date"
    ).execute()
    if res.data:
        stored = res.data[0]
        logger.info(
            f"    DB stored → last_clock_out={stored.get('last_clock_out')!r} "
            f"pulled_at={stored.get('pulled_at')!r}"
        )


def get_snapshots_for_week(member_id: str, week_start: date, week_end: date) -> list[dict]:
    res = (
        get_client()
        .table("daily_snapshots")
        .select("*")
        .eq("member_id", member_id)
        .gte("snapshot_date", week_start.isoformat())
        .lte("snapshot_date", week_end.isoformat())
        .execute()
    )
    return res.data


# ---------------------------------------------------------------------------
# Weekly summaries
# ---------------------------------------------------------------------------

def upsert_weekly_summary(summary: dict):
    get_client().table("weekly_summaries").upsert(
        summary, on_conflict="member_id,week_start"
    ).execute()


def get_weekly_summaries(member_id: str, limit: int = 4) -> list[dict]:
    res = (
        get_client()
        .table("weekly_summaries")
        .select("*")
        .eq("member_id", member_id)
        .order("week_start", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------

def insert_anomaly(anomaly: dict):
    get_client().table("anomalies").insert(anomaly).execute()


def anomaly_exists(member_id: str, anomaly_date: date, anomaly_type: str) -> bool:
    res = (
        get_client()
        .table("anomalies")
        .select("id")
        .eq("member_id", member_id)
        .eq("anomaly_date", anomaly_date.isoformat())
        .eq("anomaly_type", anomaly_type)
        .limit(1)
        .execute()
    )
    return len(res.data) > 0


def delete_anomaly(member_id: str, anomaly_date: date, anomaly_type: str):
    """Remove a previously-flagged anomaly that has since been resolved."""
    get_client().table("anomalies").delete().eq("member_id", member_id).eq(
        "anomaly_date", anomaly_date.isoformat()
    ).eq("anomaly_type", anomaly_type).execute()


def get_snapshot(member_id: str, snapshot_date: date) -> dict:
    res = (
        get_client()
        .table("daily_snapshots")
        .select("hours_logged, leave_type")
        .eq("member_id", member_id)
        .eq("snapshot_date", snapshot_date.isoformat())
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else {}


def get_week_anomaly_types(member_id: str, week_start: date, week_end: date) -> list[str]:
    res = (
        get_client()
        .table("anomalies")
        .select("anomaly_type")
        .eq("member_id", member_id)
        .gte("anomaly_date", week_start.isoformat())
        .lte("anomaly_date", week_end.isoformat())
        .execute()
    )
    return [r["anomaly_type"] for r in res.data]


# ---------------------------------------------------------------------------
# Admin edits
# ---------------------------------------------------------------------------

def upsert_admin_edit(edit: dict):
    get_client().table("admin_edits").insert(edit).execute()
