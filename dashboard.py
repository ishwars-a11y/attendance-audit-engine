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


st.set_page_config(
    page_title="Attendance Dashboard",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
footer { visibility: hidden; }

/* Hide sidebar entirely */
section[data-testid="stSidebar"]          { display: none !important; }
button[data-testid="collapsedControl"]    { display: none !important; }
button[data-testid="baseButton-headerNoPadding"] { display: none !important; }

/* Metric cards */
[data-testid="metric-container"] {
    background: rgba(255,255,255,0.06);
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.12);
    padding: 1rem 1.25rem;
}
[data-testid="stMetricLabel"] {
    font-size: 0.78rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: .05em;
    opacity: 0.7;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    background: transparent;
    border-bottom: 2px solid rgba(255,255,255,0.12);
    gap: 0;
    padding-bottom: 0;
}
.stTabs [data-baseweb="tab"] {
    padding: 10px 28px;
    font-size: 0.9rem;
    font-weight: 500;
    border-bottom: 3px solid transparent;
    margin-bottom: -2px;
    background: transparent !important;
}
.stTabs [aria-selected="true"] {
    border-bottom: 3px solid #60a5fa !important;
    font-weight: 700 !important;
}
.stTabs [data-baseweb="tab-highlight"] { display: none !important; }

/* Alert banners */
.alert {
    border-radius: 8px;
    padding: 10px 16px;
    margin-bottom: 8px;
    font-size: 0.88rem;
    font-weight: 500;
    line-height: 1.5;
}
.alert-red    { background: rgba(239,68,68,0.15);  border-left: 4px solid #f87171; color: #fca5a5; }
.alert-yellow { background: rgba(245,158,11,0.15); border-left: 4px solid #fbbf24; color: #fcd34d; }
.alert-blue   { background: rgba(96,165,250,0.15); border-left: 4px solid #60a5fa; color: #93c5fd; }
.alert-green  { background: rgba(34,197,94,0.15);  border-left: 4px solid #4ade80; color: #86efac; }

/* Date badge */
.date-badge {
    display: inline-block;
    background: rgba(96,165,250,0.15);
    border: 1px solid rgba(96,165,250,0.3);
    border-radius: 6px;
    padding: 4px 12px;
    font-size: 0.82rem;
    color: #93c5fd;
    font-weight: 600;
}

/* Prev / Next nav buttons — compact */
.nav-row button {
    padding: 4px 14px !important;
    font-size: 0.82rem !important;
}

/* Segmented control full width */
[data-testid="stSegmentedControl"] { width: 100%; }
[data-testid="stSegmentedControl"] > div { flex-wrap: wrap; gap: 4px; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Supabase client
# ---------------------------------------------------------------------------

@st.cache_resource
def get_supabase():
    return create_client(_secret("SUPABASE_URL"), _secret("SUPABASE_SERVICE_KEY"))

sb = get_supabase()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRESETS = ["Today", "Yesterday", "This Week", "Last Week", "This Month", "Last Month", "Custom"]

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
# Range helpers
# ---------------------------------------------------------------------------

def _compute_range(option: str, offset: int, today: date) -> tuple[date, date, str]:
    """Return (start, end, granularity) for a preset + integer offset."""
    if option in ("Today", "Yesterday"):
        base = today if option == "Today" else today - timedelta(days=1)
        d = min(base + timedelta(days=offset), today)
        return d, d, "day"

    if option in ("This Week", "Last Week"):
        this_monday = today - timedelta(days=today.weekday())
        base_monday = this_monday if option == "This Week" else this_monday - timedelta(days=7)
        monday = base_monday + timedelta(weeks=offset)
        friday = monday + timedelta(days=4)
        return monday, min(friday, today), "week"

    if option in ("This Month", "Last Month"):
        if option == "Last Month":
            first_this = date(today.year, today.month, 1)
            prev       = first_this - timedelta(days=1)
            base_year, base_month = prev.year, prev.month
        else:
            base_year, base_month = today.year, today.month
        total = base_year * 12 + (base_month - 1) + offset
        year  = total // 12
        month = total % 12 + 1
        start = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        return start, min(date(year, month, last_day), today), "month"

    return today, today, "day"


def _range_label(start: date, end: date, gran: str) -> str:
    if gran == "day":
        return start.strftime("%A, %d %b %Y")
    if gran == "week":
        return f"Week of {start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    if start.month == end.month and start.year == end.year:
        return start.strftime("%B %Y")
    return f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"


def _date_badge(start: str, end: str) -> str:
    return f'<span class="date-badge">📅 {start} → {end}</span>'


# ---------------------------------------------------------------------------
# Range picker widget  (one instance per tab, keyed by prefix)
# ---------------------------------------------------------------------------

def range_picker(prefix: str) -> tuple[str, str, str]:
    """
    Renders: segmented control → [◀  date badge  ▶] or custom date pickers.
    Returns (start_str, end_str, granularity) where granularity ∈ {day, week, month}.
    """
    today = date.today()

    seg_key  = f"{prefix}_seg"
    off_key  = f"{prefix}_off"
    prev_key = f"{prefix}_prev_opt"

    # Initialise session state on first render
    if seg_key not in st.session_state:
        st.session_state[seg_key]  = "This Week"
        st.session_state[off_key]  = 0
        st.session_state[prev_key] = "This Week"

    # Segmented control — value lives in st.session_state[seg_key]
    st.segmented_control(
        "Date range",
        PRESETS,
        key=seg_key,
        label_visibility="collapsed",
    )
    option = st.session_state[seg_key]

    # Reset offset whenever user switches to a different preset
    if option != st.session_state[prev_key]:
        st.session_state[off_key]  = 0
        st.session_state[prev_key] = option

    # ── Custom ───────────────────────────────────────────────────────────────
    if option == "Custom":
        c1, c2 = st.columns(2)
        cs = c1.date_input("From", value=today - timedelta(days=30), max_value=today, key=f"{prefix}_cs")
        ce = c2.date_input("To",   value=today - timedelta(days=1),  max_value=today, key=f"{prefix}_ce")
        if cs > ce:
            st.error("'From' must be before 'To'.")
            st.stop()
        gran = "day" if cs == ce else "month"
        return cs.isoformat(), ce.isoformat(), gran

    # ── Preset with Prev / Next ───────────────────────────────────────────────
    offset          = st.session_state[off_key]
    start, end, gran = _compute_range(option, offset, today)
    can_next        = end < today

    prev_col, badge_col, next_col = st.columns([1, 10, 1])

    with prev_col:
        if st.button("◀", key=f"{prefix}_prev", use_container_width=True):
            st.session_state[off_key] -= 1
            st.rerun()

    with badge_col:
        label = _range_label(start, end, gran)
        st.markdown(
            f'<div style="text-align:center;padding:6px 0 2px;">'
            f'{_date_badge(start.isoformat(), end.isoformat())}'
            f'<br><span style="font-size:0.75rem;opacity:0.55;">{label}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with next_col:
        if st.button("▶", key=f"{prefix}_next", use_container_width=True, disabled=not can_next):
            st.session_state[off_key] += 1
            st.rerun()

    return start.isoformat(), end.isoformat(), gran


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten_employees(df: pd.DataFrame):
    emp = pd.json_normalize(df["employees"])
    df  = df.drop(columns=["employees"])
    df["Employee"] = emp["jibble_name"]
    df["Type"]     = emp["employment_type"].str.replace("_", " ").str.title()
    return df, emp


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

@st.cache_data(ttl=60)
def load_daily_snapshot(snap_date: str) -> tuple[pd.DataFrame, str | None]:
    """Returns (df, last_synced_ist_str). last_synced is None when no data exists."""
    rows = (
        sb.table("daily_snapshots")
        .select("snapshot_date, hours_logged, session_count, first_clock_in, last_clock_out, leave_type, pulled_at, employees(jibble_name, employment_type)")
        .eq("snapshot_date", snap_date)
        .execute()
    )
    df = pd.DataFrame(rows.data)
    if df.empty:
        return df, None

    # Compute last-synced from the most-recent pulled_at across all rows
    pulled_series = pd.to_datetime(df["pulled_at"], utc=True, errors="coerce")
    last_pulled   = pulled_series.max()
    last_synced   = (
        last_pulled.tz_convert("Asia/Kolkata").strftime("%-I:%M %p")
        if pd.notna(last_pulled) else None
    )

    df, _ = _flatten_employees(df)

    first_dt = pd.to_datetime(df["first_clock_in"], utc=True, errors="coerce")
    last_dt  = pd.to_datetime(df["last_clock_out"],  utc=True, errors="coerce")
    span_hrs = (last_dt - first_dt).dt.total_seconds() / 3600
    df["Break Hrs"] = (span_hrs - df["hours_logged"]).clip(lower=0).round(2)

    # fillna("—") prevents NaT from rendering as the string "None" in the table
    df["first_clock_in"] = first_dt.dt.tz_convert("Asia/Kolkata").dt.strftime("%I:%M %p").fillna("—")
    df["last_clock_out"] = last_dt.dt.tz_convert("Asia/Kolkata").dt.strftime("%I:%M %p").fillna("—")
    df["leave_type"]     = df["leave_type"].fillna("")

    df = df.rename(columns={
        "hours_logged":   "Total Hours",
        "session_count":  "Sessions",
        "first_clock_in": "Clock In",
        "last_clock_out": "Clock Out",
        "leave_type":     "Leave",
    })
    df = df.sort_values(["Total Hours", "Employee"]).drop(columns=["snapshot_date", "pulled_at"])
    # Leave moved to col 3 so it's always visible
    return df[["Employee", "Type", "Leave", "Total Hours", "Break Hrs", "Sessions", "Clock In", "Clock Out"]].reset_index(drop=True), last_synced


@st.cache_data(ttl=300)
def load_weekly_summary(week_start: str) -> pd.DataFrame:
    rows = (
        sb.table("weekly_summaries")
        .select("week_start, week_end, total_hours, effective_target, deficit, leave_days, employees(jibble_name, employment_type)")
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
    df = df.sort_values("Status", key=lambda s: s.map(order))

    df = df.rename(columns={
        "total_hours": "Hours", "effective_target": "Target",
        "deficit": "Deficit", "leave_days": "Leave Days",
    })
    # Status first so it's never hidden off-screen
    return df[["Employee", "Status", "Hours", "Target", "Deficit", "Leave Days"]].reset_index(drop=True)


@st.cache_data(ttl=60)
def load_current_week(monday: str, through: str) -> pd.DataFrame:
    if monday > through:
        return pd.DataFrame()

    rows = (
        sb.table("daily_snapshots")
        .select("snapshot_date, hours_logged, leave_type, employees(jibble_name, employment_type, weekly_target_hrs, daily_target_hrs)")
        .gte("snapshot_date", monday)
        .lte("snapshot_date", through)
        .execute()
    )
    df = pd.DataFrame(rows.data)
    if df.empty:
        return df

    emp_cols = pd.json_normalize(df["employees"])
    df = df.drop(columns=["employees"])
    df["Employee"]      = emp_cols["jibble_name"]
    df["weekly_target"] = emp_cols["weekly_target_hrs"].astype(float)
    df["daily_target"]  = emp_cols["daily_target_hrs"].astype(float)
    df["leave_type"]    = df["leave_type"].fillna("")

    monday_date   = date.fromisoformat(monday)
    today         = date.today()
    days_elapsed  = sum(
        1 for i in range((today - monday_date).days + 1)
        if (monday_date + timedelta(days=i)).weekday() < 5
    )
    days_elapsed = max(days_elapsed, 1)

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

    agg["Target"]    = (agg["weekly_target"] - agg["Leave_Days"] * agg["daily_target"]).clip(lower=0)
    agg["Projected"] = (agg["Hours"] / days_elapsed * 5).round(2)
    agg["Days Left"] = max(5 - days_elapsed, 0)

    def _status(row):
        if row["Projected"] >= row["Target"]:     return "On Track"
        if row["Projected"] >= row["Target"] - 4: return "At Risk"
        return "Deficit"

    agg["Status"] = agg.apply(_status, axis=1)
    order = {"Deficit": 0, "At Risk": 1, "On Track": 2}
    agg = agg.sort_values("Status", key=lambda s: s.map(order))
    agg = agg.rename(columns={"Leave_Days": "Leave Days"})
    # Status first
    return agg[["Employee", "Status", "Hours", "Target", "Projected", "Leave Days", "Days Left"]].reset_index(drop=True)


@st.cache_data(ttl=300)
def load_monthly_summary(start: str, end: str) -> pd.DataFrame:
    rows = (
        sb.table("daily_snapshots")
        .select("snapshot_date, hours_logged, leave_type, employees(jibble_name, employment_type, weekly_target_hrs, daily_target_hrs)")
        .gte("snapshot_date", start)
        .lte("snapshot_date", end)
        .execute()
    )
    df = pd.DataFrame(rows.data)
    if df.empty:
        return df

    emp_cols = pd.json_normalize(df["employees"])
    df = df.drop(columns=["employees"])
    df["Employee"]     = emp_cols["jibble_name"]
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
    return agg[["Employee", "Deficit", "Hours", "Expected Hrs", "Days Present", "Days Absent", "Leave Days"]].reset_index(drop=True)


@st.cache_data(ttl=300)
def load_anomalies(start: str, end: str) -> pd.DataFrame:
    rows = (
        sb.table("anomalies")
        .select("anomaly_date, anomaly_type, detail, employees(jibble_name)")
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
    df["Employee"] = emp["jibble_name"]
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
# Stylers
# ---------------------------------------------------------------------------

def _color_status(val):
    return {
        "Met":      "color:#4ade80; font-weight:700",
        "On Track": "color:#4ade80; font-weight:700",
        "At Risk":  "color:#fbbf24; font-weight:700",
        "Deficit":  "color:#f87171; font-weight:700",
    }.get(val, "")

def _color_anomaly(val):
    if val in _CRITICAL: return "color:#f87171; font-weight:700"
    if val in _WARNING:  return "color:#fbbf24; font-weight:600"
    return "color:#60a5fa; font-weight:600"

def _color_hours(val):
    try:
        h = float(val)
    except (TypeError, ValueError):
        return ""
    if h == 0:  return "color:#f87171; font-weight:700"
    if h < 4:   return "color:#fbbf24; font-weight:600"
    if h >= 10: return "color:#60a5fa; font-weight:600"
    return ""  # normal range — use theme default so it's readable on both light and dark

def _color_deficit(val):
    try:
        h = float(val)
    except (TypeError, ValueError):
        return ""
    if h > 4: return "color:#f87171; font-weight:700"
    if h > 0: return "color:#fbbf24; font-weight:600"
    return "color:#4ade80; font-weight:600"

def _color_absent(val):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return ""
    if v == 0: return "color:#4ade80"
    if v <= 2: return "color:#fbbf24; font-weight:600"
    return "color:#f87171; font-weight:700"

def _heat_count(val):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return ""
    if v == 0: return "color:#475569"
    if v == 1: return "color:#fbbf24; font-weight:600"
    return "color:#f87171; font-weight:700"


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.markdown("## 📋 Attendance Dashboard")
st.markdown("---")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab1, tab2, tab3 = st.tabs(["Attendance", "Anomalies", "Anomaly Breakdown"])


# ── Tab 1: Attendance ─────────────────────────────────────────────────────────

with tab1:
    today = date.today()
    start_str, end_str, gran = range_picker("att")
    start = date.fromisoformat(start_str)
    end   = date.fromisoformat(end_str)

    st.markdown("---")

    # ── Day view ─────────────────────────────────────────────────────────────
    if gran == "day":
        if st.button("🔄 Refresh", key="att_refresh"):
            load_daily_snapshot.clear()
            st.rerun()

        # ── DEBUG: trace full pipeline ────────────────────────────────────────
        with st.expander("🔍 Debug: Clock-Out Pipeline", expanded=True):
            # Step 1 — scalar processing (row-by-row, no pandas)
            _raw = sb.table("daily_snapshots").select(
                "member_id, hours_logged, first_clock_in, last_clock_out, "
                "employees(jibble_name, employment_type)"
            ).eq("snapshot_date", start_str).execute()

            st.caption(f"**Step 1 — Scalar processing** (rows: {len(_raw.data)})")
            _rows_info = []
            for _i, _r in enumerate(_raw.data):
                _emp_field = _r.get("employees")
                _emp       = (_emp_field[0] if isinstance(_emp_field, list) else _emp_field) or {}
                _name      = _emp.get("jibble_name", "—null—")
                _co_raw    = _r.get("last_clock_out")
                try:
                    _dt  = pd.to_datetime(_co_raw, utc=True, errors="coerce")
                    _fmt = _dt.tz_convert("Asia/Kolkata").strftime("%I:%M %p") if pd.notna(_dt) else "NaT→—"
                except Exception as _e:
                    _fmt = f"ERR:{_e}"
                _rows_info.append({
                    "idx": _i, "employee": _name,
                    "emp_type": type(_emp_field).__name__,
                    "co_raw": str(_co_raw), "co_fmt": _fmt,
                })
            st.dataframe(pd.DataFrame(_rows_info), use_container_width=True, hide_index=True)

            # Step 2 — exact pandas vectorised pipeline (same as load_daily_snapshot, no cache)
            st.caption("**Step 2 — Vectorised pandas pipeline** (same logic as load_daily_snapshot, no cache)")
            _rows2 = sb.table("daily_snapshots").select(
                "snapshot_date, hours_logged, session_count, first_clock_in, "
                "last_clock_out, leave_type, pulled_at, "
                "employees(jibble_name, employment_type)"
            ).eq("snapshot_date", start_str).execute()
            _df2 = pd.DataFrame(_rows2.data)
            if not _df2.empty:
                _df2, _emp2 = _flatten_employees(_df2)
                _last_dt2 = pd.to_datetime(_df2["last_clock_out"], utc=True, errors="coerce")
                _df2["co_pandas"] = (
                    _last_dt2.dt.tz_convert("Asia/Kolkata")
                              .dt.strftime("%I:%M %p")
                              .fillna("—")
                )
                st.dataframe(
                    _df2[["Employee", "last_clock_out", "co_pandas"]],
                    use_container_width=True, hide_index=True,
                )
        # ─────────────────────────────────────────────────────────────────────

        # Always clear cache so the table below always reflects latest DB state
        load_daily_snapshot.clear()
        df, last_synced = load_daily_snapshot(start_str)
        if df.empty:
            st.info(f"No snapshot data for {start.strftime('%A, %d %b %Y')}. The engine may not have run yet.")
        else:
            absent   = df[(df["Total Hours"] == 0) & (df["Leave"] == "")]
            low_hrs  = df[(df["Total Hours"] > 0) & (df["Total Hours"] < 4) & (df["Leave"] == "")]
            on_leave = df[df["Leave"] != ""]

            # Show last-synced time and a note when anyone is missing a clock-out
            is_today = (start == today)
            missing_co = df[df["Clock Out"] == "—"]
            sync_note = f"Last synced: **{last_synced} IST**" if last_synced else ""
            if is_today and not missing_co.empty:
                names_co = ", ".join(missing_co["Employee"].tolist())
                st.caption(
                    f"⏱ {sync_note} · Clock-out not yet recorded for: {names_co}. "
                    "Will update at the next sync or when they clock out in Jibble."
                )
            elif sync_note:
                st.caption(f"⏱ {sync_note}")

            if not absent.empty:
                names = ", ".join(absent["Employee"].tolist())
                st.markdown(f'<div class="alert alert-red">🔴 <b>Absent, no leave ({len(absent)}):</b> {names}</div>', unsafe_allow_html=True)
            if not low_hrs.empty:
                names = ", ".join(low_hrs["Employee"].tolist())
                st.markdown(f'<div class="alert alert-yellow">⚠️ <b>Under 4h logged ({len(low_hrs)}):</b> {names}</div>', unsafe_allow_html=True)
            if not on_leave.empty:
                names = ", ".join(on_leave["Employee"].tolist())
                st.markdown(f'<div class="alert alert-blue">🔵 <b>On leave ({len(on_leave)}):</b> {names}</div>', unsafe_allow_html=True)
            if absent.empty and low_hrs.empty:
                st.markdown('<div class="alert alert-green">✅ No attendance issues.</div>', unsafe_allow_html=True)

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Present",    int((df["Total Hours"] > 0).sum()))
            c2.metric("Absent",     len(absent))
            c3.metric("On Leave",   len(on_leave))
            present_hrs = df[df["Total Hours"] > 0]["Total Hours"]
            c4.metric("Avg Hours",  _fmt_hrs(present_hrs.mean() if not present_hrs.empty else 0))
            c5.metric("Avg Breaks", _fmt_hrs(df["Break Hrs"].mean()))

            st.markdown("&nbsp;", unsafe_allow_html=True)
            styled = (
                df.style
                .map(_color_hours, subset=["Total Hours"])
                .map(_color_deficit, subset=["Break Hrs"])
                .format({"Total Hours": _fmt_hrs, "Break Hrs": _fmt_hrs})
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── Week view ─────────────────────────────────────────────────────────────
    elif gran == "week":
        this_monday      = today - timedelta(days=today.weekday())
        is_current_week  = (start == this_monday)

        if is_current_week:
            through = min(end, today).isoformat()
            df = load_current_week(start_str, through)
            if df.empty:
                st.info("No data yet for this week.")
            else:
                days_elapsed = sum(
                    1 for i in range((today - start).days + 1)
                    if (start + timedelta(days=i)).weekday() < 5
                )
                st.markdown(
                    f"**Week of {start.strftime('%d %b %Y')}** · "
                    f"{days_elapsed} working day(s) elapsed · Live projection"
                )

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
            df = load_weekly_summary(start_str)
            if df.empty:
                st.info(
                    f"No weekly summary for the week of {start.strftime('%d %b %Y')}. "
                    "This week may not have been processed yet — try running: "
                    "`python main.py --date " + start_str + "`"
                )
            else:
                deficit_emp = df[df["Status"] == "Deficit"]
                at_risk_emp = df[df["Status"] == "At Risk"]
                if not deficit_emp.empty:
                    names = ", ".join(deficit_emp["Employee"].tolist())
                    st.markdown(f'<div class="alert alert-red">🔴 <b>Deficit ({len(deficit_emp)}):</b> {names}</div>', unsafe_allow_html=True)
                if not at_risk_emp.empty:
                    names = ", ".join(at_risk_emp["Employee"].tolist())
                    st.markdown(f'<div class="alert alert-yellow">⚠️ <b>At risk ({len(at_risk_emp)}):</b> {names}</div>', unsafe_allow_html=True)
                if deficit_emp.empty and at_risk_emp.empty:
                    st.markdown('<div class="alert alert-green">✅ All targets met this week.</div>', unsafe_allow_html=True)

                c1, c2, c3 = st.columns(3)
                c1.metric("Met",     int((df["Status"] == "Met").sum()))
                c2.metric("At Risk", int((df["Status"] == "At Risk").sum()))
                c3.metric("Deficit", int((df["Status"] == "Deficit").sum()))

                st.markdown("&nbsp;", unsafe_allow_html=True)
                styled = (
                    df.style
                    .map(_color_status,  subset=["Status"])
                    .map(_color_deficit, subset=["Deficit"])
                    .format({"Hours": _fmt_hrs, "Target": _fmt_hrs, "Deficit": _fmt_hrs})
                )
                st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── Month / range view ────────────────────────────────────────────────────
    else:
        df = load_monthly_summary(start_str, end_str)
        if df.empty:
            st.info(f"No data for this period.")
        else:
            last_day_of_month = calendar.monthrange(end.year, end.month)[1]
            is_partial = (start.day == 1 and end < date(end.year, end.month, last_day_of_month))
            label      = _range_label(start, end, "month")
            partial_note = (
                f" · ⚠️ Partial — {end.day} of {last_day_of_month} days elapsed"
                if is_partial else ""
            )
            st.markdown(f"**{label}**{partial_note}")

            high_def = df[df["Deficit"] > 8]
            if not high_def.empty:
                names = ", ".join(high_def["Employee"].tolist())
                st.markdown(f'<div class="alert alert-red">🔴 <b>High deficit &gt;8h ({len(high_def)}):</b> {names}</div>', unsafe_allow_html=True)

            c1, c2, c3 = st.columns(3)
            c1.metric("Total Hours",      _fmt_hrs(df["Hours"].sum()))
            c2.metric("Avg per Employee", _fmt_hrs(df["Hours"].mean()))
            c3.metric("Total Deficit",    _fmt_hrs(df["Deficit"].sum()))

            st.markdown("&nbsp;", unsafe_allow_html=True)
            styled = (
                df.style
                .map(_color_deficit, subset=["Deficit"])
                .map(_color_absent,  subset=["Days Absent"])
                .format({"Hours": _fmt_hrs, "Expected Hrs": _fmt_hrs, "Deficit": _fmt_hrs})
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)


# ── Tab 2: Anomalies ──────────────────────────────────────────────────────────

with tab2:
    start_str, end_str, _ = range_picker("ano")

    df = load_anomalies(start_str, end_str)

    st.markdown("---")

    if df.empty:
        st.info("No anomalies in the selected date range.")
    else:
        col_a, col_b = st.columns(2)
        with col_a:
            types    = ["All"] + sorted(df["Type"].unique().tolist())
            sel_type = st.selectbox("Anomaly type", types)
        with col_b:
            emps    = ["All"] + sorted(df["Employee"].unique().tolist())
            sel_emp = st.selectbox("Employee", emps)

        if sel_type != "All": df = df[df["Type"] == sel_type]
        if sel_emp  != "All": df = df[df["Employee"] == sel_emp]

        n_critical = int(df["Type"].isin(_CRITICAL).sum())
        n_warning  = int(df["Type"].isin(_WARNING).sum())
        n_info     = len(df) - n_critical - n_warning

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total",    len(df))
        c2.metric("Critical", n_critical)
        c3.metric("Warning",  n_warning)
        c4.metric("Info",     n_info)

        st.markdown("&nbsp;", unsafe_allow_html=True)
        st.caption("Sorted by severity · Critical (red) → Warning (amber) → Info (blue)")
        styled = df.style.map(_color_anomaly, subset=["Type"])
        st.dataframe(styled, use_container_width=True, hide_index=True)


# ── Tab 3: Anomaly Breakdown ──────────────────────────────────────────────────

with tab3:
    start_str, end_str, _ = range_picker("brk")

    df = load_anomaly_summary(start_str, end_str)

    st.markdown("---")

    if df.empty:
        st.info("No anomaly data in the selected date range.")
    else:
        st.markdown("**Breakdown by employee** · sorted by total anomalies")
        st.markdown(
            '<div style="font-size:0.82rem;opacity:0.7;margin-bottom:12px;">'
            '🔵 0 = none &nbsp;·&nbsp; 🟡 1 = amber &nbsp;·&nbsp; 🔴 2+ = red'
            '</div>',
            unsafe_allow_html=True,
        )
        num_cols = [c for c in df.columns if c != "Employee"]
        styled = df.style.map(_heat_count, subset=num_cols)
        st.dataframe(styled, use_container_width=True, hide_index=True)
