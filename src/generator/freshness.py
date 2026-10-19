"""freshness.csv: one row per job attempt, with scheduling, timing, and compute usage."""

import numpy as np
import pandas as pd

from . import config as C
from .rng import child_rng
from .timeutil import HOUR, hhmm_s, is_full_refresh, iso, run_dates

TRANSIENT_ERRORS = ("warehouse statement timeout", "connection reset by peer")


def run_id(run_date, job: str, attempt: int) -> str:
    return f"{run_date:%Y%m%d}-{job}-{attempt}"


def _duration_s(spec, rows_scanned: int, rows_written: int, noise: float) -> int:
    base = (spec["base_s"] + spec["scan_s_per_k"] * rows_scanned / 1000
            + spec["write_s_per_k"] * rows_written / 1000)
    return max(1, int(round(base * noise)))


def _credits(warehouse: str, duration_s: int) -> float:
    rate = C.WAREHOUSE_CREDITS_PER_HOUR[warehouse]
    return round(max(duration_s, C.MIN_BILLED_S) / HOUR * rate, 4)


def build(counts: pd.DataFrame) -> pd.DataFrame:
    tc = counts.set_index(["run_date", "table"])
    rngs = {job: child_rng(f"freshness.{job}") for job in C.JOBS}
    rows = []
    for d in run_dates():
        finished = {}
        for job, spec in C.JOBS.items():
            rng = rngs[job]
            # Fixed draw count per job-run, whatever branch is taken.
            u_fail, u_frac, u_err = rng.random(3)
            waits = rng.exponential(C.QUEUE_WAIT_MEAN_S, 2)
            noises = rng.lognormal(0, C.DURATION_NOISE_SIGMA, 2)
            arrival_noise = rng.normal(0, C.HR_FILE_ARRIVAL_SD_MIN * 60)

            scheduled = hhmm_s(d, spec["schedule"])
            if job == "raw_adjuster_hr":
                # File sensor starts at the schedule; the load begins when the vendor file lands.
                queued = scheduled
                arrival = hhmm_s(d, C.HR_FILE_EXPECTED_AT) + int(arrival_noise)
                if d == C.HR_FEED_DELAY_DATE:
                    arrival = hhmm_s(d, C.HR_FILE_EXPECTED_AT) + C.HR_FEED_DELAY_MIN * 60
                ready = max(queued, arrival)
            else:
                queued = max([scheduled] + [finished[u] for u in spec["upstream"]])
                ready = queued

            scanned = int(sum(tc.loc[(d, t), field] for t, field in spec["scans"]))
            written = int(tc.loc[(d, spec["writes"]), "rows_written"]) if spec["writes"] else 0
            run_type = "full_refresh" if is_full_refresh(job, d) else "scheduled"
            common = dict(run_date=d.isoformat(), job=job, layer=spec["layer"],
                          scheduled_at=iso(scheduled), sla_deadline=iso(hhmm_s(d, spec["sla"])),
                          warehouse=spec["warehouse"], rows_scanned=scanned)

            attempt = 1
            started = ready + int(waits[0])
            if d != C.INCIDENT_DATE and u_fail < C.P_TRANSIENT_FAIL:
                dur = max(1, int(_duration_s(spec, scanned, written, noises[0]) * (0.2 + 0.7 * u_frac)))
                rows.append(dict(common, run_id=run_id(d, job, attempt), run_type=run_type,
                                 attempt=attempt, status="failed",
                                 error=TRANSIENT_ERRORS[int(u_err * len(TRANSIENT_ERRORS))],
                                 queued_at=iso(queued), started_at=iso(started),
                                 finished_at=iso(started + dur), duration_s=dur,
                                 credits_used=_credits(spec["warehouse"], dur)))
                attempt = 2
                run_type = "retry"
                queued = started + dur + C.RETRY_BACKOFF_S
                started = queued + int(waits[1])

            dur = _duration_s(spec, scanned, written, noises[attempt - 1])
            finished[job] = started + dur
            rows.append(dict(common, run_id=run_id(d, job, attempt), run_type=run_type,
                             attempt=attempt, status="success", error="",
                             queued_at=iso(queued), started_at=iso(started),
                             finished_at=iso(finished[job]), duration_s=dur,
                             credits_used=_credits(spec["warehouse"], dur)))

    cols = ["run_id", "run_date", "job", "layer", "run_type", "attempt", "status", "error",
            "scheduled_at", "queued_at", "started_at", "finished_at", "sla_deadline",
            "duration_s", "warehouse", "credits_used", "rows_scanned"]
    return pd.DataFrame(rows)[cols]
