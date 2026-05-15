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


def _secret(key: str) -> str:
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.environ[key]


st.set_page_config(page_title="Attendance Dashboard", page_icon="📋", layout="wide")

# ---------------------------------------------------------------------------
# Global CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
/* App background */
.stApp { background: #f1f5f9; }
.main .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
footer { visibility: hidden; }

/* Metric cards */
[data-testid="metric-container"] {
    background: white;
    border-radius: 10px;
    border: 1px solid #e2e8f0;
    padding: 1rem 1.25rem;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
[data-testid="stMetricValue"] { color: #0f172a !important; font-weight: 700 !important; }
[data-testid="stMetricLabel"] {
    color: #64748b !important;
    font-size: 0.78rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: .05em;
}

/* Sidebar */
section[data-testid="stSidebar"] > div:first-child { background: #0f172a; padding: 1.5rem 1rem; }
section[data-testid="stSidebar"] .stMarkdown p,
section[data-testid="stSidebar"] .stMarkdown h1,
section[data-testid="stSidebar"] .stMarkdown h2,
section[data-testid="stSidebar"] .stMarkdown h3,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stCaption span { color: #f1f5f9 !important; }
section[data-testid="stSidebar"] .stButton > button {
    background: #1e293b !important;
    color: #cbd5e1 !important;
    border: 1px solid #334155 !important;
    border-radius: 6px;
    width: 100%;
    font-size: 0.82rem;
    padding: 0.35rem 0.5rem;
    margin-bottom: 4px;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background: #334155 !important;
    color: #f1f5f9 !important;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    background: transparent;
    border-bottom: 2px solid #e2e8f0;
    gap: 0;
    padding-bottom: 0;
}
.stTabs [data-baseweb="tab"] {
    padding: 10px 24px;
    color: #64748b;
    font-size: 0.88rem;
    font-weight: 500;
    border-bottom: 3px solid transparent;
    margin-bottom: -2px;
    background: transparent !important;
}
.stTabs [aria-selected="true"] {
    color: #0f172a !important;
    border-bottom: 3px solid #0f172a !important;
    font-weight: 700 !important;
}
.stTabs [data-baseweb="tab-highlight"] { display: none !important; }

/* Alert banners */
.alert { border-radius: 8px; padding: 10px 16px; margin-bottom: 10px; font-size: 0.88rem; font-weight: 500; line-height: 1.4; }
.alert-red    { background: #fef2f2; border-left: 4px solid #ef4444; color: #7f1d1d; }
.alert-yellow { background: #fffbeb; border-left: 4px solid #f59e0b; color: #78350f; }
.alert-green  { background: #f0fdf4; border-left: 4px solid #22c55e; color: #14532d; }

/* Section labels */
.section-label {
    font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .08em; color: #94a3b8; margin-bottom: 6px;
}

/* Dataframe container */
[data-testid="stDataFrame"] { background: white; border-radius: 10px; border: 1px solid #e2e8f0; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

@st.cache_resource
def get_supabase():
    return create_client(_secret("SUPABASE_URL"), _secret("SUPABASE_SERVICE_KEY"))

sb = get_supabase()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANOMALY_SEVERITY = {
    "Unexcused Absence":    1,
    "Consecutive Absence":  2,
    "Weekly Deficit":       3,
    "Chronic Late Starter": 4,
    "Missing Clock-Out":    5,
    "Late / No Start":      6,
    "Early Departure":      7,
    "Long Breaks":          8,
    "Excessive Breaks":     9,
    "Excessive Hours":      10,
}

_CRITICAL = {"Unexcused Absence", "Consecutive Absence", "Weekly Deficit"}
_WARNING  = {"Chronic Late Starter", "Missing Clock-Out", "Late / No Start", "Early Departure"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten_employees(df: pd.DataFrame):
    emp = pd.json_normalize(df["employees"])
    df  = df.drop(columns=["employees"])
    df["Employee"] = emp["display_name"]
    df["Type"]     = emp["employment_type"].str.replace("_", " ").str.title()
    return df, emp


def _working_days_in_month(year: int, month: int) -> int:
    _, days_in_month = calendar.monthrange(year, month)
    return sum(1 for d in range(1, days_in_month + 1) if date(year, month, d).weekday() < 5)


def _fmt_hrs(h) -> str:
    try:
        h = float(h)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(h):
        return "—"
    total_min = round(abs(h) * 60)
    return f"{total_min // 60}h {total_min % 60}m"

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

    first_dt = pd.to_datetime(df["first_clock_in"], utc=True, errors="coerce")
    last_dt  = pd.to_datetime(df["last_clock_out"],  utc=True, errors="coerce")
    span_hrs = (last_dt - first_dt).dt.total_seconds() / 3600
    df["Break Hrs"] = (span_hrs - df["hours_logged"]).clip(lower=0).round(2)

    df["first_clock_in"] = first_dt.dt.tz_convert("Asia/Kolkata").dt.strftime("%I:%M %p")
    df["last_clock_out"] = last_dt.dt.tz_convert("Asia/Kolkata").dt.strftime("%I:%M %p")
    df["leave_type"]     = df["leave_type"].fillna("")

    df = df.rename(columns={
        "hours_logged":   "Hours",
        "session_count":  "Sessions",
        "first_clock_in": "Clock In",
        "last_clock_out": "Clock Out",
        "leave_type":     "Leave",
    })
    # Problems at top: sort by hours ascending, alphabetically within same hours
    df = df.sort_values(["Hours", "Employee"]).drop(columns=["snapshot_date"])
    return df[["Employee", "Type", "Hours", "Break Hrs", "Sessions", "Clock In", "Clock Out", "Leave"]].reset_index(drop=True)


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
        if row["total_hours"] >= row["effective_target"]:     return "Met"
        if row["total_hours"] >= row["effective_target"] - 4: return "At Risk"
        return "Deficit"

    df["Status"] = df.apply(_status, axis=1)
    order = {"Deficit": 0, "At Risk": 1, "Met": 2}
    df["_sort"] = df["Status"].map(order)
    df = df.sort_values("_sort").drop(columns=["_sort"])

    df = df.rename(columns={
        "week_start": "Week Start", "week_end": "Week End",
        "total_hours": "Hours", "effective_target": "Target",
        "deficit": "Deficit", "leave_days": "Leave Days",
    })
    return df[["Employee", "Type", "Hours", "Target", "Deficit", "Leave Days", "Status"]].reset_index(drop=True)


@st.cache_data(ttl=60)
def load_current_week() -> pd.DataFrame:
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
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
    df["Employee"]      = emp_cols["display_name"]
    df["weekly_target"] = emp_cols["weekly_target_hrs"].astype(float)
    df["daily_target"]  = emp_cols["daily_target_hrs"].astype(float)
    df["leave_type"]    = df["leave_type"].fillna("")

    agg = (
        df.groupby("Employee")
        .agg(
            Hours        = ("hours_logged", "sum"),
            Leave_Days   = ("leave_type",   lambda x: (x != "").sum()),
            weekly_target= ("weekly_target", "first"),
            daily_target = ("daily_target",  "first"),
        )
        .reset_index()
    )

    days_elapsed = max(date.today().weekday(), 1)
    agg["Target"]    = (agg["weekly_target"] - agg["Leave_Days"] * agg["daily_target"]).clip(lower=0)
    agg["Projected"] = (agg["Hours"] / days_elapsed * 5).round(2)

    def _status(row):
        if row["Projected"] >= row["Target"]:     return "On Track"
        if row["Projected"] >= row["Target"] - 4: return "At Risk"
        return "Deficit"

    agg["Status"]    = agg.apply(_status, axis=1)
    agg["Days Left"] = 5 - days_elapsed
    order = {"Deficit": 0, "At Risk": 1, "On Track": 2}
    agg["_sort"] = agg["Status"].map(order)
    agg = agg.sort_values("_sort").drop(columns=["_sort"])
    agg = agg.rename(columns={"Leave_Days": "Leave Days"})
    return agg[["Employee", "Hours", "Target", "Projected", "Leave Days", "Days Left", "Status"]].reset_index(drop=True)


@st.cache_data(ttl=300)
def load_monthly_summary(year: int, month: int) -> pd.DataFrame:
    start    = date(year, month, 1).isoformat()
    last_day = calendar.monthrange(year, month)[1]
    end      = min(date(year, month, last_day), date.today() - timedelta(days=1)).isoformat()

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
    df["Employee"]     = emp_cols["display_name"]
    df["daily_target"] = emp_cols["daily_target_hrs"].astype(float)
    df["leave_type"]   = df["leave_type"].fillna("")
    df["hours_logged"] = df["hours_logged"].astype(float)

    days_tracked = len({r["snapshot_date"] for r in rows.data})
    agg = (
        df.groupby("Employee")
        .agg(
            Total_Hours  = ("hours_logged", "sum"),
            Days_Present = ("hours_logged", lambda x: (x > 0).sum()),
            Days_Absent  = ("hours_logged", lambda x: ((x == 0) & (df.loc[x.index, "leave_type"] == "")).sum()),
            Leave_Days   = ("leave_type",   lambda x: (x != "").sum()),
            daily_target = ("daily_target", "first"),
        )
        .reset_index()
    )
    agg["Expected Hrs"] = ((days_tracked - agg["Leave_Days"]) * agg["daily_target"]).clip(lower=0).round(2)
    agg["Deficit"]      = (agg["Expected Hrs"] - agg["Total_Hours"]).clip(lower=0).round(2)
    agg = agg.sort_values("Deficit", ascending=False)
    agg = agg.rename(columns={
        "Total_Hours": "Hours", "Days_Present": "Days Present",
        "Days_Absent": "Days Absent", "Leave_Days": "Leave Days",
    })
    return agg[["Employee", "Hours", "Expected Hrs", "Deficit", "Days Present", "Days Absent", "Leave Days"]].reset_index(drop=True)


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
    df["_sev"] = df["Type"].map(ANOMALY_SEVERITY).fillna(99)
    df = df.sort_values(["_sev", "Date"], ascending=[True, False]).drop(columns=["_sev"])
    return df[["Date", "Employee", "Type", "Detail"]].reset_index(drop=True)


@st.cache_data(ttl=300)
def load_anomaly_summary(start: str, end: str) -> pd.DataFrame:
    df = load_anomalies(start, end)
    if df.empty:
        return df

    type_cols = sorted(ANOMALY_SEVERITY, key=ANOMALY_SEVERITY.get)
    records = []
    for emp, grp in df.groupby("Employee"):
        row = {"Employee": emp, "Total": len(grp)}
        for t in type_cols:
            row[t] = int((grp["Type"] == t).sum())
        records.append(row)
    return pd.DataFrame(records).sort_values("Total", ascending=False).reset_index(drop=True)

# ---------------------------------------------------------------------------
# Styler helpers
# ---------------------------------------------------------------------------

def _color_status(val):
    return {
        "Met":      "color:#15803d; font-weight:700",
        "On Track": "color:#15803d; font-weight:700",
        "At Risk":  "color:#b45309; font-weight:700",
        "Deficit":  "color:#dc2626; font-weight:700",
    }.get(val, "")

def _color_anomaly(val):
    if val in _CRITICAL: return "color:#dc2626; font-weight:700"
    if val in _WARNING:  return "color:#b45309; font-weight:600"
    return "color:#1d4ed8; font-weight:600"

def _color_hours(val):
    try:
        h = float(val)
    except (TypeError, ValueError):
        return ""
    if h == 0:  return "color:#dc2626; font-weight:700"
    if h < 4:   return "color:#b45309; font-weight:600"
    if h >= 10: return "color:#1d4ed8; font-weight:600"
    return "color:#0f172a"

def _color_deficit(val):
    try:
        h = float(val)
    except (TypeError, ValueError):
        return ""
    if h > 4:  return "color:#dc2626; font-weight:700"
    if h > 0:  return "color:#b45309; font-weight:600"
    return "color:#15803d; font-weight:600"

def _color_absent(val):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return ""
    if v == 0: return "color:#15803d"
    if v <= 2: return "color:#b45309; font-weight:600"
    return "color:#dc2626; font-weight:700"

def _heat_count(val):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return ""
    if v == 0: return "color:#cbd5e1"
    if v == 1: return "color:#b45309; font-weight:600"
    return "color:#dc2626; font-weight:700"

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

today = date.today()

if "di_start" not in st.session_state:
    st.session_state["di_start"] = today - timedelta(days=30)
if "di_end" not in st.session_state:
    st.session_state["di_end"] = today - timedelta(days=1)

with st.sidebar:
    st.markdown("## 📋 Attendance")
    st.markdown("---")
    st.markdown('<p class="section-label" style="color:#475569">Quick Range</p>', unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Today", use_container_width=True):
            st.session_state["di_start"] = today
            st.session_state["di_end"]   = today
        if st.button("Last 7d", use_container_width=True):
            st.session_state["di_start"] = today - timedelta(days=7)
            st.session_state["di_end"]   = today - timedelta(days=1)
        if st.button("This Month", use_container_width=True):
            st.session_state["di_start"] = date(today.year, today.month, 1)
            st.session_state["di_end"]   = today
    with c2:
        if st.button("This Week", use_container_width=True):
            st.session_state["di_start"] = today - timedelta(days=today.weekday())
            st.session_state["di_end"]   = today
        if st.button("Last 30d", use_container_width=True):
            st.session_state["di_start"] = today - timedelta(days=30)
            st.session_state["di_end"]   = today - timedelta(days=1)
        if st.button("Last Month", use_container_width=True):
            first = date(today.year, today.month, 1)
            prev  = first - timedelta(days=1)
            st.session_state["di_start"] = date(prev.year, prev.month, 1)
            st.session_state["di_end"]   = prev

    st.markdown("---")
    st.markdown('<p class="section-label" style="color:#475569">Custom</p>', unsafe_allow_html=True)
    range_start = st.date_input("From", key="di_start", max_value=today)
    range_end   = st.date_input("To",   key="di_end",   max_value=today)

    if range_start > range_end:
        st.error("Start must be before end.")
        st.stop()

    st.markdown("---")
    st.caption(f"Engine runs 5× daily · Refreshes every 5 min")

start_str = range_start.isoformat()
end_str   = range_end.isoformat()
days_span = (range_end - range_start).days + 1

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.markdown(f"## Attendance Dashboard")
st.caption(f"{start_str}  →  {end_str}  ·  {days_span} day(s)")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Daily", "Weekly", "Monthly", "Anomalies", "Anomaly Breakdown",
])

# ── Tab 1: Daily ─────────────────────────────────────────────────────────────

with tab1:
    snap_dates  = available_snapshot_dates()
    valid_dates = [d for d in snap_dates if start_str <= d <= end_str]

    if not valid_dates:
        st.info("No snapshot data in the selected date range.")
    else:
        col_pick, col_prev, col_next, _ = st.columns([4, 1, 1, 4])
        with col_pick:
            selected_date = st.selectbox(
                "Date", valid_dates,
                format_func=lambda d: pd.to_datetime(d).strftime("%A, %d %b %Y"),
                label_visibility="collapsed",
            )
        idx = valid_dates.index(selected_date)
        with col_prev:
            st.write("")
            if st.button("◀ Prev", use_container_width=True) and idx < len(valid_dates) - 1:
                selected_date = valid_dates[idx + 1]
        with col_next:
            st.write("")
            if st.button("Next ▶", use_container_width=True) and idx > 0:
                selected_date = valid_dates[idx - 1]

        df = load_daily_snapshot(selected_date)
        if df.empty:
            st.info("No data for this date.")
        else:
            absent   = df[(df["Hours"] == 0) & (df["Leave"] == "")]
            low_hrs  = df[(df["Hours"] > 0) & (df["Hours"] < 4) & (df["Leave"] == "")]
            on_leave = df[df["Leave"] != ""]

            if not absent.empty:
                names = ", ".join(absent["Employee"].tolist())
                st.markdown(f'<div class="alert alert-red">🔴 <b>Absent, no leave ({len(absent)}):</b> {names}</div>', unsafe_allow_html=True)
            if not low_hrs.empty:
                names = ", ".join(low_hrs["Employee"].tolist())
                st.markdown(f'<div class="alert alert-yellow">⚠️ <b>Under 4h logged ({len(low_hrs)}):</b> {names}</div>', unsafe_allow_html=True)
            if absent.empty and low_hrs.empty:
                st.markdown('<div class="alert alert-green">✅ No attendance issues today.</div>', unsafe_allow_html=True)

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Present",    int((df["Hours"] > 0).sum()))
            c2.metric("Absent",     len(absent))
            c3.metric("On Leave",   len(on_leave))
            c4.metric("Avg Hours",  _fmt_hrs(df[df["Hours"] > 0]["Hours"].mean() if (df["Hours"] > 0).any() else 0))
            c5.metric("Avg Breaks", _fmt_hrs(df["Break Hrs"].mean()))

            st.markdown("&nbsp;", unsafe_allow_html=True)
            styled = (
                df.style
                .map(_color_hours, subset=["Hours"])
                .map(_color_deficit, subset=["Break Hrs"])
                .format({"Hours": _fmt_hrs, "Break Hrs": _fmt_hrs})
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)

# ── Tab 2: Weekly ─────────────────────────────────────────────────────────────

with tab2:
    monday   = today - timedelta(days=today.weekday())
    view_mode = st.radio("", ["Current Week (live)", "Past Weeks"], horizontal=True, label_visibility="collapsed")

    if view_mode == "Current Week (live)":
        df = load_current_week()
        if df.empty:
            st.info("No data yet for this week.")
        else:
            days_elapsed = max(today.weekday(), 1)
            st.markdown(f"**Week of {monday.strftime('%d %b %Y')}**  ·  {days_elapsed} working day(s) elapsed")

            deficit_emp = df[df["Status"] == "Deficit"]
            at_risk_emp = df[df["Status"] == "At Risk"]
            if not deficit_emp.empty:
                names = ", ".join(deficit_emp["Employee"].tolist())
                st.markdown(f'<div class="alert alert-red">🔴 <b>Projected deficit ({len(deficit_emp)}):</b> {names}</div>', unsafe_allow_html=True)
            if not at_risk_emp.empty:
                names = ", ".join(at_risk_emp["Employee"].tolist())
                st.markdown(f'<div class="alert alert-yellow">⚠️ <b>At risk ({len(at_risk_emp)}):</b> {names}</div>', unsafe_allow_html=True)
            if deficit_emp.empty and at_risk_emp.empty:
                st.markdown('<div class="alert alert-green">✅ Everyone is on track this week.</div>', unsafe_allow_html=True)

            c1, c2, c3 = st.columns(3)
            c1.metric("On Track", int((df["Status"] == "On Track").sum()))
            c2.metric("At Risk",  int((df["Status"] == "At Risk").sum()))
            c3.metric("Deficit",  int((df["Status"] == "Deficit").sum()))

            st.markdown("&nbsp;", unsafe_allow_html=True)
            styled = (
                df.style
                .map(_color_status, subset=["Status"])
                .format({"Hours": _fmt_hrs, "Target": _fmt_hrs, "Projected": _fmt_hrs})
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        all_weeks   = available_weeks()
        valid_weeks = [w for w in all_weeks if start_str <= w <= end_str]
        if not valid_weeks:
            st.info("No weekly summaries in the selected date range.")
        else:
            col_w, _ = st.columns([3, 5])
            with col_w:
                selected_week = st.selectbox(
                    "Week", valid_weeks,
                    format_func=lambda w: f"Week of {pd.to_datetime(w).strftime('%d %b %Y')}",
                    label_visibility="collapsed",
                )
            df = load_weekly_summary(selected_week)
            if df.empty:
                st.info("No data for this week.")
            else:
                deficit_emp = df[df["Status"] == "Deficit"]
                if not deficit_emp.empty:
                    names = ", ".join(deficit_emp["Employee"].tolist())
                    st.markdown(f'<div class="alert alert-red">🔴 <b>Deficit ({len(deficit_emp)}):</b> {names}</div>', unsafe_allow_html=True)

                c1, c2, c3 = st.columns(3)
                c1.metric("Met",     int((df["Status"] == "Met").sum()))
                c2.metric("At Risk", int((df["Status"] == "At Risk").sum()))
                c3.metric("Deficit", int((df["Status"] == "Deficit").sum()))

                st.markdown("&nbsp;", unsafe_allow_html=True)
                styled = (
                    df.style
                    .map(_color_status, subset=["Status"])
                    .map(_color_deficit, subset=["Deficit"])
                    .format({"Hours": _fmt_hrs, "Target": _fmt_hrs, "Deficit": _fmt_hrs})
                )
                st.dataframe(styled, use_container_width=True, hide_index=True)

# ── Tab 3: Monthly ────────────────────────────────────────────────────────────

with tab3:
    col_m, col_y, _ = st.columns([2, 2, 4])
    with col_m:
        month_sel = st.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                                 format_func=lambda m: date(2000, m, 1).strftime("%B"))
    with col_y:
        year_sel = st.selectbox("Year", list(range(2026, today.year + 1)), index=today.year - 2026)

    df = load_monthly_summary(year_sel, month_sel)
    if df.empty:
        st.info("No data for this month.")
    else:
        month_label  = date(year_sel, month_sel, 1).strftime("%B %Y")
        working_days = _working_days_in_month(year_sel, month_sel)
        st.markdown(f"**{month_label}**  ·  {working_days} working days")

        high_def = df[df["Deficit"] > 8]
        if not high_def.empty:
            names = ", ".join(high_def["Employee"].tolist())
            st.markdown(f'<div class="alert alert-red">🔴 <b>High deficit &gt;8h ({len(high_def)}):</b> {names}</div>', unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("Total hours",      _fmt_hrs(df["Hours"].sum()))
        c2.metric("Avg per employee", _fmt_hrs(df["Hours"].mean()))
        c3.metric("Total deficit",    _fmt_hrs(df["Deficit"].sum()))

        st.markdown("&nbsp;", unsafe_allow_html=True)
        styled = (
            df.style
            .map(_color_deficit, subset=["Deficit"])
            .map(_color_absent,  subset=["Days Absent"])
            .format({"Hours": _fmt_hrs, "Expected Hrs": _fmt_hrs, "Deficit": _fmt_hrs})
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)

# ── Tab 4: Anomalies ──────────────────────────────────────────────────────────

with tab4:
    df = load_anomalies(start_str, end_str)
    if df.empty:
        st.info("No anomalies in the selected date range.")
    else:
        col_a, col_b, _ = st.columns([2, 2, 4])
        with col_a:
            types    = ["All"] + sorted(df["Type"].unique().tolist())
            sel_type = st.selectbox("Type", types, label_visibility="collapsed")
        with col_b:
            emps    = ["All"] + sorted(df["Employee"].unique().tolist())
            sel_emp = st.selectbox("Employee", emps, label_visibility="collapsed")

        if sel_type != "All": df = df[df["Type"] == sel_type]
        if sel_emp  != "All": df = df[df["Employee"] == sel_emp]

        n_critical = int(df["Type"].isin(_CRITICAL).sum())
        n_warning  = int(df["Type"].isin(_WARNING).sum())
        n_info     = len(df) - n_critical - n_warning

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total",       len(df))
        c2.metric("Critical",    n_critical)
        c3.metric("Warning",     n_warning)
        c4.metric("Info",        n_info)

        st.markdown("&nbsp;", unsafe_allow_html=True)
        st.caption("Sorted by severity · Critical (red) → Warning (amber) → Info (blue)")
        styled = df.style.map(_color_anomaly, subset=["Type"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

# ── Tab 5: Anomaly Breakdown ──────────────────────────────────────────────────

with tab5:
    df = load_anomaly_summary(start_str, end_str)
    if df.empty:
        st.info("No anomaly data in the selected date range.")
    else:
        st.markdown(f"**Breakdown by employee**  ·  {start_str} → {end_str}")
        st.caption("0 = grey · 1 = amber · 2+ = red")

        num_cols = [c for c in df.columns if c != "Employee"]
        styled = df.style.map(_heat_count, subset=num_cols)
        st.dataframe(styled, use_container_width=True, hide_index=True)
