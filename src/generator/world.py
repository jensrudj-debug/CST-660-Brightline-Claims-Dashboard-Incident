"""Entity-level simulation: policies, claims, and policy write ("touch") events.

pipeline.py derives every table's rows from these entities using the
pipeline's own filter logic, so anomalies follow from the mechanism rather
than from scaled counts.
"""

from dataclasses import dataclass

import numpy as np

from . import config as C
from .rng import child_rng
from .timeutil import DAY, HOUR, hhmm_s

TOUCH_KINDS = ("endorsement", "nightly_batch", "writeback_adjuster", "writeback_tpa",
               "new_business")

_LOB_SEV_MED = np.array([lob[2] for lob in C.LOBS], dtype=float)
_LOB_SEV_SIGMA = np.array([lob[3] for lob in C.LOBS])
_LOB_CLOSE_MED = np.array([lob[4] for lob in C.LOBS], dtype=float)


@dataclass(frozen=True)
class World:
    policy_created_s: np.ndarray  # sorted; policy_id is the index
    policy_status: np.ndarray     # index into C.POLICY_STATUSES
    policy_lob: np.ndarray        # index into C.LOBS
    claim_policy: np.ndarray
    claim_lob: np.ndarray
    claim_reported_s: np.ndarray  # sorted; claim_id is the index
    claim_closed_s: np.ndarray
    claim_tpa: np.ndarray         # True: loss history posts in the weekly PAS batch
    claim_severity: np.ndarray
    touch_s: np.ndarray           # sorted; window only
    touch_policy: np.ndarray
    touch_kind: np.ndarray        # index into TOUCH_KINDS


def _n_days() -> int:
    return (C.WINDOW_END - C.BURN_IN_START).days + 1


def _dow(day_idx: np.ndarray) -> np.ndarray:
    return (C.BURN_IN_START.weekday() + day_idx) % 7


def _time_of_day(rng, n, mean_h, sd_h, lo_h, hi_h) -> np.ndarray:
    return (np.clip(rng.normal(mean_h, sd_h, n), lo_h, hi_h) * HOUR).astype(np.int64)


def _policies():
    rng = child_rng("world.policies")
    new_counts = rng.poisson(C.NEW_POLICIES_PER_DAY, _n_days())
    day_idx = np.repeat(np.arange(_n_days()), new_counts)
    new_created = np.sort(day_idx * DAY + _time_of_day(rng, day_idx.size, 12.5, 3.0, 7, 20))
    created = np.concatenate([np.full(C.N_POLICIES_START, -DAY, np.int64), new_created])
    lob = rng.choice(len(C.LOBS), created.size, p=[lob[1] for lob in C.LOBS])
    status = rng.choice(len(C.POLICY_STATUSES), created.size, p=C.POLICY_STATUS_MIX)
    status[C.N_POLICIES_START:] = 0  # new business starts ACTIVE
    return created, lob, status


def _claims(policy_created, policy_lob):
    rng = child_rng("world.claims")
    days = np.arange(_n_days())
    counts = rng.poisson(C.FNOL_PER_DAY * np.array(C.FNOL_DOW_FACTOR)[_dow(days)])
    day_idx = np.repeat(days, counts)
    n = day_idx.size

    in_hours = rng.random(n) < 0.85
    tod = np.where(in_hours, _time_of_day(rng, n, 12.5, 3.0, 6, 22), rng.integers(0, DAY, n))
    reported = np.sort(day_idx * DAY + tod)
    n_existing = np.searchsorted(policy_created, reported, side="right")
    policy = (rng.random(n) * n_existing).astype(np.int64)
    lob = policy_lob[policy]

    # Close after a LOB-dependent lognormal duration, during business hours;
    # most weekend closures slip to Monday.
    dur = rng.lognormal(np.log(_LOB_CLOSE_MED[lob]), C.CLOSE_SIGMA)
    close_day = (reported + (dur * DAY).astype(np.int64)) // DAY
    close_dow = _dow(close_day)
    defer = (close_dow >= 5) & (rng.random(n) < C.WEEKEND_CLOSE_DEFER)
    close_day = close_day + np.where(defer, 7 - close_dow, 0)
    closed = np.maximum(close_day * DAY + _time_of_day(rng, n, 13, 2.5, 8, 19), reported + HOUR)

    tpa = rng.random(n) < C.TPA_CLOSE_SHARE
    severity = rng.lognormal(np.log(_LOB_SEV_MED[lob]), _LOB_SEV_SIGMA[lob]).round(2)
    return policy, lob, reported, closed, tpa, severity


def _touches(policy_created, claim_policy, claim_closed, claim_tpa):
    """Every write to a policy record from the first run's previous ingest onward."""
    rng = child_rng("world.touches")
    t_min = hhmm_s(C.WINDOW_START, C.INGEST_AT) - DAY
    t_max = _n_days() * DAY
    days = np.arange(t_min // DAY, _n_days())
    weekday = _dow(days) < 5
    times, kinds = [], []

    n_end = rng.poisson(np.where(weekday, C.ENDORSEMENTS_WEEKDAY, C.ENDORSEMENTS_WEEKEND))
    d = np.repeat(days, n_end)
    times.append(d * DAY + _time_of_day(rng, d.size, 12.5, 3.0, 7, 20))
    kinds.append(np.full(d.size, 0))

    n_nb = rng.poisson(C.NIGHTLY_BATCH_POLICIES, days.size)
    d = np.repeat(days, n_nb)
    times.append(d * DAY + int(C.NIGHTLY_BATCH_AT_H * HOUR) + rng.integers(0, 20 * 60, d.size))
    kinds.append(np.full(d.size, 1))

    # Endorsements and batch updates hit policies that exist at that moment.
    t_random = np.concatenate(times)
    n_existing = np.searchsorted(policy_created, t_random, side="right")
    policies = [(rng.random(t_random.size) * n_existing).astype(np.int64)]

    adj = ~claim_tpa
    times.append(claim_closed[adj])
    kinds.append(np.full(adj.sum(), 2))
    policies.append(claim_policy[adj])

    # TPA closures post in the next weekly batch at or after closure.
    closed = claim_closed[claim_tpa]
    batch_day = closed // DAY + (C.TPA_BATCH_DOW - _dow(closed // DAY)) % 7
    batch_at = batch_day * DAY + C.TPA_BATCH_AT_H * HOUR
    batch_at = np.where(batch_at < closed, batch_at + 7 * DAY, batch_at)
    times.append(batch_at + rng.integers(0, 15 * 60, closed.size))
    kinds.append(np.full(closed.size, 3))
    policies.append(claim_policy[claim_tpa])

    new_ids = np.flatnonzero(policy_created >= t_min)
    times.append(policy_created[new_ids])
    kinds.append(np.full(new_ids.size, 4))
    policies.append(new_ids)

    t, p, k = np.concatenate(times), np.concatenate(policies), np.concatenate(kinds)
    keep = (t > t_min) & (t < t_max)
    t, p, k = t[keep], p[keep], k[keep]
    order = np.lexsort((k, p, t))
    return t[order], p[order], k[order]


def simulate() -> World:
    p_created, p_lob, p_status = _policies()
    c_policy, c_lob, c_reported, c_closed, c_tpa, c_sev = _claims(p_created, p_lob)
    t_s, t_policy, t_kind = _touches(p_created, c_policy, c_closed, c_tpa)
    return World(p_created, p_status, p_lob, c_policy, c_lob, c_reported, c_closed, c_tpa,
                 c_sev, t_s, t_policy, t_kind)
