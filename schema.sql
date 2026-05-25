-- Run this once in your Supabase SQL editor to create all tables.

create table if not exists employees (
    member_id        text primary key,
    jibble_name      text not null,
    display_name     text not null,
    employment_type  text not null check (employment_type in ('full_time', 'part_time')),
    weekly_target_hrs integer not null,
    daily_target_hrs  integer not null,
    time_window_rules boolean not null default false,   -- DEPRECATED (unused since 2026-05-25). Safe to drop: ALTER TABLE employees DROP COLUMN time_window_rules;
    timezone         text not null default 'Asia/Kolkata',
    is_excluded      boolean not null default false,
    is_active        boolean not null default true,
    joined_date      date,   -- NULL = joined before tracking started
    departed_date    date    -- NULL = still active
);

-- Migration for existing databases (idempotent):
-- alter table employees add column if not exists joined_date date;
-- alter table employees add column if not exists departed_date date;

create table if not exists daily_snapshots (
    id               bigserial primary key,
    member_id        text not null references employees(member_id),
    snapshot_date    date not null,
    hours_logged     numeric(5,2) not null default 0,
    session_count    integer not null default 0,
    first_clock_in   timestamptz,
    last_clock_out   timestamptz,
    leave_type       text,
    pulled_at        timestamptz not null default now(),
    unique (member_id, snapshot_date)
);

create table if not exists weekly_summaries (
    id               bigserial primary key,
    member_id        text not null references employees(member_id),
    week_start       date not null,
    week_end         date not null,
    total_hours      numeric(6,2) not null default 0,
    leave_days       integer not null default 0,
    effective_target numeric(6,2) not null,
    deficit          numeric(6,2) not null default 0,
    pulled_at        timestamptz not null default now(),
    unique (member_id, week_start)
);

create table if not exists anomalies (
    id               bigserial primary key,
    member_id        text not null references employees(member_id),
    anomaly_date     date not null,
    anomaly_type     text not null check (anomaly_type in (
                         'Missing Clock-Out',
                         'Unexcused Absence',
                         'Late / No Start',
                         'Excessive Hours',
                         'Excessive Breaks',
                         'Weekly Deficit',
                         'Early Departure',
                         'Long Breaks',
                         'Consecutive Absence',
                         'Chronic Late Starter'
                     )),
    detail           text,
    detected_at      timestamptz not null default now()
);

create table if not exists admin_edits (
    id               bigserial primary key,
    member_id        text not null references employees(member_id),
    edit_date        date not null,
    original_hours   numeric(5,2),
    edited_hours     numeric(5,2),
    edited_at        timestamptz
);

-- Indexes for common dashboard queries
create index if not exists idx_snapshots_date     on daily_snapshots(snapshot_date);
create index if not exists idx_anomalies_date     on anomalies(anomaly_date);
create index if not exists idx_summaries_week     on weekly_summaries(week_start);
