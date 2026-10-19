"""Apply the pipeline's transformation logic to the simulated entities, run by run.

This is where the incident happens: from PAS 4.12 on, any policy written
after the release has policy_status NULL in the ingest snapshot, and
int_claims_enriched keeps only policy_status = 'ACTIVE'.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import config as C
from .rng import child_rng
from .timeutil import DAY, day_s, hhmm_s, is_full_refresh, run_dates, to_s
from .world import World

NULL_STATUS = -1
ACTIVE = C.POLICY_STATUSES.index("ACTIVE")


@dataclass(frozen=True)
class RunState:
    """Row-level state of one run, shared by row counts and column profiles."""
    ingest: int
    prev: int
    n_policies: int
    policy_status: np.ndarray  # per policy, as ingested (NULL_STATUS = NULL)
    reported: np.ndarray       # per claim masks from here on
    is_open: np.ndarray
    closed_window: np.ndarray
    active: np.ndarray         # passes int_claims_enriched's policy_status = 'ACTIVE'
    fnol_day: np.ndarray

    @property
    def in_int(self) -> np.ndarray:
        return (self.is_open | self.closed_window) & self.active


def run_state(world: World, d: date) -> RunState:
    ingest = hhmm_s(d, C.INGEST_AT)
    reported = world.claim_reported_s <= ingest
    status = policy_status_at(world, ingest)
    return RunState(
        ingest=ingest,
        prev=ingest - DAY,
        n_policies=int(np.searchsorted(world.policy_created_s, ingest, side="right")),
        policy_status=status,
        reported=reported,
        is_open=reported & (world.claim_closed_s > ingest),
        closed_window=((world.claim_closed_s >= day_s(d - timedelta(days=C.CLOSED_WINDOW_DAYS)))
                       & (world.claim_closed_s < day_s(d))),
        # int_claims_enriched: WHERE policy_status = 'ACTIVE' (NULL fails the predicate)
        active=status[world.claim_policy] == ACTIVE,
        fnol_day=((world.claim_reported_s >= day_s(d - timedelta(days=1)))
                  & (world.claim_reported_s < day_s(d))),
    )


def policy_status_at(world: World, ingest_s: int) -> np.ndarray:
    """policy_status as ingested: NULL for policies written since the release."""
    status = world.policy_status.copy()
    release_s = to_s(C.PAS_RELEASE_AT)
    if ingest_s > release_s:
        lo, hi = np.searchsorted(world.touch_s, [release_s, ingest_s], side="right")
        status[np.unique(world.touch_policy[lo:hi])] = NULL_STATUS
    return status


def table_counts(world: World) -> pd.DataFrame:
    """Rows written and resulting table size for every table on every run."""
    dates = run_dates()
    upd_rng = child_rng("pipeline.raw_claims_updates")
    hr_steps = child_rng("pipeline.adjuster_hr").choice([-1, 0, 0, 0, 1], len(dates))
    headcount = C.ADJUSTER_HEADCOUNT_START + np.cumsum(hr_steps)

    first_prev = hhmm_s(dates[0], C.INGEST_AT) - DAY
    # Raw tables start from a full snapshot load taken at the first run's previous ingest.
    raw_policies_rows = int(np.searchsorted(world.policy_created_s, first_prev, side="right"))
    raw_claims_rows = int(np.searchsorted(world.claim_reported_s, first_prev, side="right"))

    records = []
    for i, d in enumerate(dates):
        st = run_state(world, d)
        prev_weekday = (d - timedelta(days=1)).weekday() < 5

        lo, hi = np.searchsorted(world.touch_s, [st.prev, st.ingest], side="right")
        policy_cdc = int(hi - lo)

        new_fnol = (world.claim_reported_s > st.prev) & st.reported
        new_close = (world.claim_closed_s > st.prev) & (world.claim_closed_s <= st.ingest)
        update_rate = C.CLAIM_UPDATE_RATE * (1 if prev_weekday else C.CLAIM_UPDATE_WEEKEND_FACTOR)
        claim_cdc = int(new_fnol.sum() + new_close.sum()
                        + upd_rng.poisson(update_rate * st.is_open.sum()))

        n_int = int(st.in_int.sum())
        n_closed = int((st.closed_window & st.active).sum())
        n_open = int((st.is_open & st.active).sum())
        n_policies = st.n_policies
        reported = st.reported
        fnol_day = int(st.fnol_day.sum())
        fnol_total = int((world.claim_reported_s < day_s(d)).sum())

        raw_policies_rows += policy_cdc
        raw_claims_rows += claim_cdc
        fnol_full = is_full_refresh("fct_fnol", d)
        records += [
            (d, "raw_policies", "cdc_append", policy_cdc, raw_policies_rows),
            (d, "raw_claims", "cdc_append", claim_cdc, raw_claims_rows),
            (d, "raw_adjuster_hr", "full_replace", int(headcount[i]), int(headcount[i])),
            (d, "stg_policies", "merge", policy_cdc, n_policies),
            (d, "stg_claims", "merge", claim_cdc, int(reported.sum())),
            (d, "int_claims_enriched", "rebuild", n_int, n_int),
            (d, "fct_claims_closed", "rebuild", n_closed, n_closed),
            (d, "fct_claims_open", "rebuild", n_open, n_open),
            (d, "fct_fnol", "full_refresh" if fnol_full else "incremental",
             fnol_total if fnol_full else fnol_day, fnol_total),
        ]
    return pd.DataFrame(records, columns=["run_date", "table", "load_type", "rows_written",
                                          "table_row_count"])
