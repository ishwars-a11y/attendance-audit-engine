from datetime import date

import pytz

# All Jibble timestamps are treated as IST — the Jibble account is configured in IST.
# If probe output shows UTC timestamps, set JIBBLE_TIMESTAMPS_UTC = True in .env.
IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Shift timings — date-aware so past days remain evaluated under their
# correct rule and we don't retroactively flag people as "late".
#   - Up to 2026-05-24: 11 AM → 8 PM (11:10 AM grace late cutoff)
#   - From 2026-05-25:  10 AM → 7 PM (10:10 AM grace late cutoff)
# ---------------------------------------------------------------------------
SHIFT_CHANGE_DATE = date(2026, 5, 25)

# Current (post-change) shift
WORK_WINDOW_START_HOUR   = 10
WORK_WINDOW_START_MINUTE = 10   # 10-min grace → flags at 10:10 AM

# Legacy (pre-change) shift — applied automatically for snapshot_date < SHIFT_CHANGE_DATE
OLD_WORK_WINDOW_START_HOUR   = 11
OLD_WORK_WINDOW_START_MINUTE = 10   # flags at 11:10 AM

# EARLY_DEPARTURE_HOUR / EXCESSIVE_SESSIONS_THRESHOLD retained for backward
# compatibility with historical anomalies but the corresponding checks are
# no longer run (see main.py).
EARLY_DEPARTURE_HOUR         = 18   # deprecated
EXCESSIVE_SESSIONS_THRESHOLD = 4    # deprecated

MAX_BREAK_HOURS           = 2.0  # total break time (span − worked) before flagging
EXCESSIVE_HOURS_THRESHOLD = 10   # hours/day before flagging
AT_RISK_BUFFER_HRS        = 4    # hours below effective target → "At Risk" (not "Deficit")
CHRONIC_LATE_THRESHOLD    = 3    # Late/No Start flags in one week → Chronic Late Starter

# Employee roster — keyed by Jibble name (exact string from /v1/people response).
# member_id: fill in the values after running: python main.py --probe
# The engine uses memberId as primary key once synced to Supabase.
EMPLOYEES = {
    "Bhat Aasim": {
        "display_name": "Aasim",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Abdul Muksith A": {
        "display_name": "Muksith",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Abhishek Kumar": {
        "display_name": "Abhishek",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Abhishek Bhardwaj": {
        "display_name": "Abhishek B",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Abhishek Kalivela": {
        "display_name": "Abhishek (Frontend)",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
        "departed_date": "2026-05-19",   # Last working day
    },
    "Arfath Tade": {
        "display_name": "Arfath",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Gopi S": {
        "display_name": "Gopi",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Ishwar Singh": {
        "display_name": "Ishwar",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Kannan Ravindran": {
        "display_name": "Kannan",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Prathmesh Gaidhane": {
        "display_name": "Prathmesh",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Rajat Chugh": {
        "display_name": "Rajat",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
        "joined_date": "2026-05-05",   # First working day
        "departed_date": "2026-06-04", # Last working day
    },
    "Samiksha Patil": {
        "display_name": "Samiksha",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Shaik Shahbaaz Alam": {
        "display_name": "Shahbaaz",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Shuvam Jaswal": {
        "display_name": "Shuvam",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 40,
        "daily_target_hrs": 8,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Anum Basit": {
        "display_name": "Anum",
        "member_id": None,
        "employment_type": "part_time",
        "weekly_target_hrs": 20,
        "daily_target_hrs": 4,
        "timezone": "Asia/Kolkata",  # Jibble account shows Dubai employee in IST
        "is_excluded": False,
    },
    "Ubaid Syed": {
        "display_name": "Ubaid",
        "member_id": None,
        "employment_type": "part_time",
        "weekly_target_hrs": 20,
        "daily_target_hrs": 4,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
    },
    "Richa Jindal": {
        "display_name": "Richa",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 0,
        "daily_target_hrs": 0,
        "timezone": "Asia/Kolkata",
        "is_excluded": False,
        "is_active": False,
    },
    "Faizan Shaikh": {
        "display_name": "Faizan",
        "member_id": None,
        "employment_type": "full_time",
        "weekly_target_hrs": 0,
        "daily_target_hrs": 0,
        "timezone": "Asia/Kolkata",
        "is_excluded": True,
    },
}
