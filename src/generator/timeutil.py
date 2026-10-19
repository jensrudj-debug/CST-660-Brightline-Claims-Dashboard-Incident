"""Simulation clock: integer seconds of local (America/Chicago) time since burn-in start."""

from datetime import date, datetime, timedelta

from . import config as C

DAY = 86_400
HOUR = 3_600
T0 = datetime.combine(C.BURN_IN_START, datetime.min.time())


def to_s(dt: datetime) -> int:
    return int((dt - T0).total_seconds())


def day_s(d: date) -> int:
    return (d - C.BURN_IN_START).days * DAY


def hhmm_s(d: date, hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return day_s(d) + h * HOUR + m * 60


def iso(s: int) -> str:
    dt = T0 + timedelta(seconds=int(s))
    assert dt.date() >= C.WINDOW_START - timedelta(days=1), "only CDT timestamps are written"
    return f"{dt:%Y-%m-%dT%H:%M:%S}{C.UTC_OFFSET}"


def run_dates() -> list[date]:
    return [C.WINDOW_START + timedelta(days=i) for i in range(C.WINDOW_DAYS)]


def is_full_refresh(job: str, run_date: date) -> bool:
    """The first run after the dbt deploy fully rebuilds the changed model."""
    return job == C.DBT_DEPLOY_MODEL and run_date == C.DBT_DEPLOY_AT.date() + timedelta(days=1)
