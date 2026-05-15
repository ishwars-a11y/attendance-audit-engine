"""
Attendance Dashboard — Streamlit web app.
Run: streamlit run dashboard.py
"""

import calendar
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

st.set_page_config(
    page_title="Attendance Dashboard",
    page_icon="📋",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Supabase client
# ---------------------------------------------------------------------------

@st.cache_resource
def get_supabase():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )

sb = get_supabase()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten_employees(df: pd.DataFrame) -> pd.DataFrame:
    emp = pd.json_normalize(df["employees"])
    df  = df.drop(columns=["employees"])
    df["Employee"] = emp["display_name"]
    df["Type"]     = emp["employment_type"].str.replace("_", " ").str.title()
    return df, emp


def _working_days_in_month(year: int, month: int) -> int:
    _, days_in_month = calendar.monthrange(year, month)
    return sum(1 for d in range(1, days_in_month + 1)
               if date(year, month, d).weekday() < 5)


def _prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def available_snapshot_dates() -> list[str]:
    rows = sb.table("daily_snapshots").select("snapshot_date").order("snapshot_date", desc=True).execute()
    return sorted({r["snapshot_date"] for r in rows.data}, reverse=True)


@st.cache_data(ttl=300)
def available_weeks() -> list[str]:
    rows = sb.table("weekly_summaries").select("week_start").order("week_start", desc=True).execute()
    return sorted({r["week_start"] for r in rows.data}, reverse=True)


@st.cache_data(ttl=300)
def load_daily_snapshot(snap_date: str) -> pd.DataFrame:
    rows = (
        sb.table("daily_snapshots")
        .select("snapshot_date, hours_logged, session_count, first_clock_in, last_clock_out, leave_type, employees(display_name, employment_type)")
        .eq("snapshot_date", snap_date)
        .execute()
    )
    df = pd.DataFrame(rows.data)
    if df.empty:
        return df

    df, _ = _flatten_employees(df)

    # Break hours before formatting timestamps
    first_dt = pd.to_datetime(df["first_clock_in"], utc=True, errors="coerce")
    last_dt  = pd.to_datetime(df["last_clock_out"],  utc=True, errors="coerce")
    span_hrs = (last_dt - first_dt).dt.total_seconds() / 3600
    df["Break Hrs"] = (span_hrs - df["hours_logged"]).clip(lower=0).round(2)

    df["first_clock_in"] = first_dt.dt.tz_convert("Asia/Kolkata").dt.strftime("%I:%M %p")
    df["last_clock_out"] = last_dt.dt.tz_convert("Asia/Kolkata").dt.strftime("%I:%M %p")
    df["leave_type"]     = df["leave_type"].fillna("")

    df = df.rename(columns={
        "snapshot_date":  "Date",
        "hours_logged":   "Hours",
        "session_count":  "Sessions",
        "first_clock_in": "Clock In (IST)",
        "last_clock_out": "Clock Out (IST)",
        "leave_type":     "Leave",
    })
    cols = ["Employee", "Type", "Hours", "Break Hrs", "Sessions", "Clock In (IST)", "Clock Out (IST)", "Leave"]
    return df[cols].sort_values("Employee").reset_index(drop=True)


@st.cache_data(ttl=300)
def load_weekly_summary(week_start: str) -> pd.DataFrame:
    rows = (
        sb.table("weekly_summaries")
        .select("week_start, week_end, total_hours, effective_target, deficit, leave_days, employees(display_name, employment_type)")
        .eq("week_start", week_start)
        .execute()
    )
    df = pd.DataFrame(rows.data)
    if df.empty:
        return df

    df, _ = _flatten_employees(df)

    def _status(row):
        if row["total_hours"] >= row["effective_target"]:    return "Met"
        if row["total_hours"] >= row["effective_target"] - 4: return "At Risk"
        return "Deficit"

    df["Status"] = df.apply(_status, axis=1)
    df = df.rename(columns={
        "week_start":       "Week Start",
        "week_end":         "Week End",
        "total_hours":      "Hours",
        "effective_target": "Target",
        "deficit":          "Deficit",
        "leave_days":       "Leave Days",
    })
    cols = ["Employee", "Type", "Week Start", "Week End", "Hours", "Target", "Deficit", "Leave Days", "Status"]
    return df[cols].sort_values("Employee").reset_index(drop=True)


@st.cache_data(ttl=60)
def load_current_week() -> pd.DataFrame:
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    # Snapshots exist up to yesterday
    through = (today - timedelta(days=1)).isoformat()

    rows = (
        sb.table("daily_snapshots")
        .select("snapshot_date, hours_logged, leave_type, employees(display_name, employment_type, weekly_target_hrs, daily_target_hrs)")
        .gte("snapshot_date", monday.isoformat())
        .lte("snapshot_date", through)
        .execute()
    )
    df = pd.DataFrame(rows.data)
    if df.empty:
        return df

    emp_cols = pd.json_normalize(df["employees"])
    df = df.drop(columns=["employees"])
    df["Employee"]         = emp_cols["display_name"]
    df["employment_type"]  = emp_cols["employment_type"]
    df["weekly_target"]    = emp_cols["weekly_target_hrs"].astype(float)
    df["daily_target"]     = emp_cols["daily_target_hrs"].astype(float)
    df["leave_type"]       = df["leave_type"].fillna("")

    # Aggregate per employee
    agg = (
        df.groupby("Employee")
        .agg(
            Hours     = ("hours_logged", "sum"),
            Leave_Days= ("leave_type",   lambda x: (x != "").sum()),
            weekly_target = ("weekly_target", "first"),
            daily_target  = ("daily_target",  "first"),
        )
        .reset_index()
    )

    # Days elapsed this week (Mon = day 1)
    days_elapsed = today.weekday()  # Mon=0, so elapsed working days = weekday (0-4)
    days_elapsed = max(days_elapsed, 1)
    days_left    = 5 - days_elapsed

    agg["Effective Target"] = (
        agg["weekly_target"] - agg["Leave_Days"] * agg["daily_target"]
    ).clip(lower=0)
    agg["Projected"] = (agg["Hours"] / days_elapsed * 5).round(1)

    def _status(row):
        if row["Projected"] >= row["Effective Target"]:    return "On Track"
        if row["Projected"] >= row["Effective Target"] - 4: return "At Risk"
        return "Deficit"

    agg["Status"]    = agg.apply(_status, axis=1)
    agg["Days Left"] = days_left
    agg = agg.rename(columns={"Effective Target": "Target", "Leave_Days": "Leave Days"})
    cols = ["Employee", "Hours", "Target", "Projected", "Leave Days", "Days Left", "Status"]
    return agg[cols].sort_values("Employee").reset_index(drop=True)


@st.cache_data(ttl=300)
def load_monthly_summary(year: int, month: int) -> pd.DataFrame:
    start = date(year, month, 1).isoformat()
    last_day = calendar.monthrange(year, month)[1]
    end = min(date(year, month, last_day), date.today() - timedelta(days=1)).isoformat()

    rows = (
        sb.table("daily_snapshots")
        .select("snapshot_date, hours_logged, leave_type, employees(display_name, employment_type, weekly_target_hrs, daily_target_hrs)")
        .gte("snapshot_date", start)
        .lte("snapshot_date", end)
        .execute()
    )
    df = pd.DataFrame(rows.data)
    if df.empty:
        return df

    emp_cols = pd.json_normalize(df["employees"])
    df = df.drop(columns=["employees"])
    df["Employee"]      = emp_cols["display_name"]
    df["daily_target"]  = emp_cols["daily_target_hrs"].astype(float)
    df["leave_type"]    = df["leave_type"].fillna("")
    df["hours_logged"]  = df["hours_logged"].astype(float)

    working_days = _working_days_in_month(year, month)
    # Cap working days to days that have passed
    days_tracked = len({r["snapshot_date"] for r in rows.data if r["snapshot_date"] >= start and r["snapshot_date"] <= end})

    agg = (
        df.groupby("Employee")
        .agg(
            Total_Hours  = ("hours_logged", "sum"),
            Days_Present = ("hours_logged", lambda x: (x > 0).sum()),
            Days_Absent  = ("hours_logged", lambda x: (
                ((x == 0) & (df.loc[x.index, "leave_type"] == "")).sum()
            )),
            Leave_Days   = ("leave_type",   lambda x: (x != "").sum()),
            daily_target = ("daily_target", "first"),
        )
        .reset_index()
    )

    agg["Expected Hrs"] = (
        (days_tracked - agg["Leave_Days"]) * agg["daily_target"]
    ).clip(lower=0).round(1)
    agg["Deficit"] = (agg["Expected Hrs"] - agg["Total_Hours"]).clip(lower=0).round(1)

    agg = agg.rename(columns={
        "Total_Hours":  "Hours",
        "Days_Present": "Days Present",
        "Days_Absent":  "Days Absent",
        "Leave_Days":   "Leave Days",
    })
    cols = ["Employee", "Hours", "Expected Hrs", "Deficit", "Days Present", "Days Absent", "Leave Days"]
    return agg[cols].sort_values("Employee").reset_index(drop=True)


@st.cache_data(ttl=300)
def load_anomalies(start: str, end: str) -> pd.DataFrame:
    rows = (
        sb.table("anomalies")
        .select("anomaly_date, anomaly_type, detail, employees(display_name)")
        .gte("anomaly_date", start)
        .lte("anomaly_date", end)
        .order("anomaly_date", desc=True)
        .limit(1000)
        .execute()
    )
    df = pd.DataFrame(rows.data)
    if df.empty:
        return df

    emp = pd.json_normalize(df["employees"])
    df  = df.drop(columns=["employees"])
    df["Employee"] = emp["display_name"]
    df = df.rename(columns={"anomaly_date": "Date", "anomaly_type": "Type", "detail": "Detail"})
    return df[["Date", "Employee", "Type", "Detail"]].reset_index(drop=True)


@st.cache_data(ttl=300)
def load_anomaly_summary(start: str, end: str) -> pd.DataFrame:
    df = load_anomalies(start, end)
    if df.empty:
        return df

    type_cols = [
        "Late / No Start", "Unexcused Absence", "Consecutive Absence",
        "Excessive Breaks", "Long Breaks", "Early Departure",
        "Weekly Deficit", "Chronic Late Starter",
        "Missing Clock-Out", "Excessive Hours",
    ]
    records = []
    for emp, grp in df.groupby("Employee"):
        row = {"Employee": emp, "Total": len(grp)}
        for t in type_cols:
            row[t] = int((grp["Type"] == t).sum())
        records.append(row)

    return (
        pd.DataFrame(records)
        .sort_values("Total", ascending=False)
        .reset_index(drop=True)
    )

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

STATUS_COLORS = {
    "Met":      "background-color:#1a472a;color:#d4edda",
    "On Track": "background-color:#1a472a;color:#d4edda",
    "At Risk":  "background-color:#3d2e00;color:#fff3cd",
    "Deficit":  "background-color:#4a1520;color:#f8d7da",
}
ANOMALY_COLORS = {
    "Unexcused Absence":   "background-color:#4a1520;color:#f8d7da",
    "Consecutive Absence": "background-color:#4a1520;color:#f8d7da",
    "Weekly Deficit":      "background-color:#4a1520;color:#f8d7da",
    "Chronic Late Starter":"background-color:#3d2e00;color:#fff3cd",
    "Missing Clock-Out":   "background-color:#3d2e00;color:#fff3cd",
    "Late / No Start":     "background-color:#3d2e00;color:#fff3cd",
    "Early Departure":     "background-color:#3d2e00;color:#fff3cd",
    "Excessive Breaks":    "background-color:#0c2a30;color:#d1ecf1",
    "Long Breaks":         "background-color:#0c2a30;color:#d1ecf1",
    "Excessive Hours":     "background-color:#0c2a30;color:#d1ecf1",
}

def _color_status(val):  return STATUS_COLORS.get(val, "")
def _color_anomaly(val): return ANOMALY_COLORS.get(val, "")

# ---------------------------------------------------------------------------
# Sidebar — global date range
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("📋 Attendance")
    st.markdown("---")
    st.subheader("Date Range")
    today     = date.today()
    range_start = st.date_input("From", value=today - timedelta(days=30), max_value=today)
    range_end   = st.date_input("To",   value=today - timedelta(days=1),  max_value=today)

    if range_start > range_end:
        st.error("Start date must be before end date.")
        st.stop()

    st.markdown("---")
    st.caption("Data refreshes every 5 minutes.")

start_str = range_start.isoformat()
end_str   = range_end.isoformat()

# ---------------------------------------------------------------------------
# App layout
# ---------------------------------------------------------------------------

st.title("📋 Attendance Dashboard")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📅 Daily Snapshot",
    "📊 Weekly Summary",
    "🗓️ Monthly Summary",
    "⚠️ Anomalies",
    "📈 Anomaly Summary",
])

# ── Tab 1: Daily Snapshot ────────────────────────────────────────────────────

with tab1:
    snap_dates  = available_snapshot_dates()
    valid_dates = [d for d in snap_dates if start_str <= d <= end_str]

    if not valid_dates:
        st.info("No snapshot data in the selected date range.")
    else:
        col_pick, col_nav1, col_nav2, _ = st.columns([3, 1, 1, 4])
        with col_pick:
            selected_date = st.selectbox(
                "Select date", valid_dates,
                format_func=lambda d: pd.to_datetime(d).strftime("%a, %d %b %Y"),
            )
        idx = valid_dates.index(selected_date)
        with col_nav1:
            st.write(""); st.write("")
            if st.button("◀ Prev") and idx < len(valid_dates) - 1:
                selected_date = valid_dates[idx + 1]
        with col_nav2:
            st.write(""); st.write("")
            if st.button("Next ▶") and idx > 0:
                selected_date = valid_dates[idx - 1]

        df = load_daily_snapshot(selected_date)
        if df.empty:
            st.info("No data for this date.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Employees tracked", len(df))
            c2.metric("Avg hours",         f"{df['Hours'].mean():.1f}")
            c3.metric("Avg break hrs",     f"{df['Break Hrs'].mean():.1f}")
            c4.metric("On leave",          int((df["Leave"] != "").sum()))
            st.dataframe(
                df.style.format({"Hours": "{:.2f}", "Break Hrs": "{:.2f}"}),
                use_container_width=True, hide_index=True,
            )

# ── Tab 2: Weekly Summary ────────────────────────────────────────────────────

with tab2:
    today_wd = date.today().weekday()  # Mon=0
    monday   = date.today() - timedelta(days=today_wd)

    view_mode = st.radio("View", ["Current Week (live)", "Past Weeks"], horizontal=True)

    if view_mode == "Current Week (live)":
        df = load_current_week()
        if df.empty:
            st.info("No data yet for this week.")
        else:
            days_elapsed = max(today_wd, 1)
            st.subheader(f"Week of {monday.strftime('%d %b %Y')}  ·  {days_elapsed} day(s) elapsed")
            c1, c2, c3 = st.columns(3)
            c1.metric("On Track", int((df["Status"] == "On Track").sum()))
            c2.metric("At Risk",  int((df["Status"] == "At Risk").sum()))
            c3.metric("Deficit",  int((df["Status"] == "Deficit").sum()))
            styled = df.style.map(_color_status, subset=["Status"]).format(
                {"Hours": "{:.1f}", "Target": "{:.1f}", "Projected": "{:.1f}"}
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        all_weeks   = available_weeks()
        valid_weeks = [w for w in all_weeks if start_str <= w <= end_str]
        if not valid_weeks:
            st.info("No weekly summaries in the selected date range.")
        else:
            col_pick2, _ = st.columns([3, 5])
            with col_pick2:
                selected_week = st.selectbox(
                    "Select week", valid_weeks,
                    format_func=lambda w: f"Week of {pd.to_datetime(w).strftime('%d %b %Y')}",
                )
            df = load_weekly_summary(selected_week)
            if df.empty:
                st.info("No data for this week.")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Met target", int((df["Status"] == "Met").sum()))
                c2.metric("At risk",    int((df["Status"] == "At Risk").sum()))
                c3.metric("Deficit",    int((df["Status"] == "Deficit").sum()))
                styled = df.style.map(_color_status, subset=["Status"]).format(
                    {"Hours": "{:.1f}", "Target": "{:.1f}", "Deficit": "{:.1f}"}
                )
                st.dataframe(styled, use_container_width=True, hide_index=True)

# ── Tab 3: Monthly Summary ───────────────────────────────────────────────────

with tab3:
    col_m, col_y, _ = st.columns([2, 2, 4])
    with col_m:
        month_sel = st.selectbox("Month", list(range(1, 13)),
                                 index=today.month - 1,
                                 format_func=lambda m: date(2000, m, 1).strftime("%B"))
    with col_y:
        year_sel = st.selectbox("Year", list(range(2026, today.year + 1)),
                                index=today.year - 2026)

    df = load_monthly_summary(year_sel, month_sel)
    if df.empty:
        st.info("No data for this month.")
    else:
        month_label = date(year_sel, month_sel, 1).strftime("%B %Y")
        working_days = _working_days_in_month(year_sel, month_sel)
        st.subheader(f"{month_label}  ·  {working_days} working days")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total hours (all employees)", f"{df['Hours'].sum():.0f}")
        c2.metric("Avg hours per employee",      f"{df['Hours'].mean():.1f}")
        c3.metric("Total deficit hours",         f"{df['Deficit'].sum():.0f}")
        st.dataframe(
            df.style.format({
                "Hours": "{:.1f}", "Expected Hrs": "{:.1f}", "Deficit": "{:.1f}",
            }),
            use_container_width=True, hide_index=True,
        )

# ── Tab 4: Anomalies ─────────────────────────────────────────────────────────

with tab4:
    df = load_anomalies(start_str, end_str)
    if df.empty:
        st.info("No anomalies in the selected date range.")
    else:
        col_a, col_b = st.columns(2)
        with col_a:
            types    = ["All"] + sorted(df["Type"].unique().tolist())
            sel_type = st.selectbox("Filter by type", types)
        with col_b:
            emps    = ["All"] + sorted(df["Employee"].unique().tolist())
            sel_emp = st.selectbox("Filter by employee", emps)

        if sel_type != "All": df = df[df["Type"] == sel_type]
        if sel_emp  != "All": df = df[df["Employee"] == sel_emp]

        st.subheader(f"{len(df)} anomalies  ·  {start_str} → {end_str}")
        styled = df.style.map(_color_anomaly, subset=["Type"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

# ── Tab 5: Anomaly Summary ───────────────────────────────────────────────────

with tab5:
    df = load_anomaly_summary(start_str, end_str)
    if df.empty:
        st.info("No anomaly data in the selected date range.")
    else:
        st.subheader(f"Anomaly breakdown  ·  {start_str} → {end_str}")
        st.dataframe(df, use_container_width=True, hide_index=True)
