# Attendance Audit Engine

Pulls attendance data from Jibble daily, detects anomalies, and stores results in Supabase for display in a Retool dashboard.

**Stack:** Python · Supabase (free) · Retool (free) · GitHub Actions (free)

---

## Setup — do these steps in order

### Step 1 — Clone the repo and install dependencies

```bash
git clone https://github.com/YOUR_USERNAME/attendance-audit-engine
cd attendance-audit-engine
pip install -r requirements.txt
```

### Step 2 — Create your `.env` file

```bash
cp .env.example .env
```

Fill in:
- `JIBBLE_API_KEY` — your Jibble API key
- `SUPABASE_URL` — from Supabase dashboard → Settings → API
- `SUPABASE_SERVICE_KEY` — the **service role** key (not anon key) from the same page

### Step 3 — Create Supabase tables

1. Go to [supabase.com](https://supabase.com) → create a free project
2. Open the SQL editor
3. Paste and run the contents of `schema.sql`

### Step 4 — Probe the Jibble API (required before first run)

```bash
python main.py --probe
```

This dumps raw JSON from Jibble. Check the output against the field name constants at the top of `engine/jibble.py`. Update any constants that don't match.

**Also verify:** does `/v1/attendance/edits` return data? If it returns 404, see **Admin Edit Log fallback** below.

### Step 5 — Sync employees

```bash
python main.py --sync-employees
```

This pulls all members from Jibble and writes them to Supabase. Check the output — any "Unknown member" warnings mean someone's Jibble name doesn't match `config.py`. Update `config.py` to match the exact Jibble name.

### Step 6 — Test a single day

```bash
python main.py --date 2024-06-10
```

Check Supabase → Table editor to see rows written to `daily_snapshots` and `anomalies`.

### Step 7 — Backfill historical data

```bash
python main.py --backfill --from 2024-05-01 --to 2024-05-31
```

### Step 8 — Set up GitHub Actions (daily cron)

1. Push the repo to GitHub
2. Go to repo → Settings → Secrets and variables → Actions
3. Add three secrets:
   - `JIBBLE_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`

The workflow runs Mon–Fri at 10 AM, 1 PM, 5 PM, 9 PM and 11 PM IST. You can also trigger it manually from the Actions tab.

- **10 AM** — deep re-sync of *yesterday + the previous 30 working days*. Deliberately does not touch today (nobody has clocked in yet — processing it would flag everyone absent). This is also what heals **retroactive leave**: employees who apply on Jibble after returning are picked up here, and any Unexcused-Absence / Weekly-Deficit anomalies for those days auto-resolve.
- **1 PM / 5 PM / 9 PM** — fast intra-day sync of today only.
- **11 PM** — closes out today and re-heals the last 5 working days, so same-week retroactive leave applications are reflected the same night.

### Step 9 — Set up Retool dashboard

1. Go to [retool.com](https://retool.com) → sign up free
2. Create a new app → Add a resource → Supabase (or PostgreSQL)
3. Connect using your Supabase URL + service key
4. Build 4 panels:
   - **Panel 1 — Weekly Hours Status:** Query `weekly_summaries` joined with `employees`. Show Met/At Risk/Deficit status.
   - **Panel 2 — Daily Anomaly Log:** Query `anomalies` with a date range filter. Join `employees` for name.
   - **Panel 3 — Admin Edit Log:** Query `admin_edits` joined with `employees`.
   - **Panel 4 — Weekly Trend:** Query last 4 `weekly_summaries` per employee. Show week-over-week hours.

---

## Admin Edit Log fallback

If `python main.py --probe` shows that `/v1/attendance/edits` returns 404, Panel 3 has no data source from Jibble directly.

**Fallback approach:** The engine stores a timestamped snapshot every time it pulls (`pulled_at` column on `daily_snapshots`). If you run the engine twice on the same day and an admin edited a timesheet in between, the `hours_logged` value will differ. You can detect edits by comparing consecutive `pulled_at` values for the same `member_id + snapshot_date`.

To enable this, run the engine twice daily (e.g. add a second cron at 1 PM IST) and query `daily_snapshots` for rows where `hours_logged` changed between pulls.

---

## Anomaly types

| Type | Who | Trigger |
|------|-----|---------|
| Missing Clock-Out | All | Clock-in exists, no clock-out |
| Unexcused Absence | All | Zero hours, no leave, no holiday |
| Excessive Hours | Full-timers | > 10 hours/day |
| Excessive Breaks | Full-timers | > 4 sessions (clock-in/clock-out pairs) |
| Late / No Start | Full-timers | No clock-in before 11 AM IST, no AM leave |
| Weekly Deficit | All | Total hours < effective weekly target |

---

## Notes

- **Friday missing clock-outs** are detected at 11 PM Friday. They appear on the dashboard Monday morning (next working day Ishwar checks).
- **Half-day AM leave** suppresses the "Late / No Start" flag. Jibble's `halfDay` field is checked — update `LEAVE_FIELD_HALF_DAY` in `engine/jibble.py` if the field name differs.
- **Faizan Shaikh** is excluded from all tracking via `is_excluded = true` in the `employees` table.
- All upserts are idempotent — re-running the engine on the same date is safe.
