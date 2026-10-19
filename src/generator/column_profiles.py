"""column_profiles.csv: per run, per table, per profiled column: nulls, distinct values,
category mix, and mean (severity).

raw_policies is profiled on the CDC batch the run loaded; every other table on
its full contents after the run (fct_fnol on the day's partition). A column is
profiled only once the schema-change log shows it reached that table.
"""

import json
from datetime import date, datetime

import numpy as np
import pandas as pd

from . import config as C
from .pipeline import NULL_STATUS, run_state
from .timeutil import run_dates, to_s
from .world import World

LOB_NAMES = tuple(lob[0] for lob in C.LOBS)
STATUS_CODES = ("A", "L", "C")  # pol_status_cd, per the PAS 4.12 value mapping
CLAIM_STATUSES = ("OPEN", "CLOSED")


def _categorical(column, codes, labels) -> dict:
    valid = codes[codes != NULL_STATUS]
    shares = np.bincount(valid, minlength=len(labels)) / max(valid.size, 1)
    mix = {labels[i]: round(float(s), 4) for i, s in enumerate(shares) if s > 0}
    return dict(column=column, row_count=codes.size, null_count=int(codes.size - valid.size),
                distinct_count=len(mix), mean=np.nan,
                category_mix=json.dumps(mix, sort_keys=True, separators=(",", ":")))


def _numeric(column, values) -> dict:
    return dict(column=column, row_count=values.size, null_count=0,
                distinct_count=int(np.unique(values).size),
                mean=round(float(values.mean()), 2) if values.size else np.nan, category_mix="")


def _claims(world, mask, with_status=None) -> list[dict]:
    profiles = [_categorical("lob", world.claim_lob[mask], LOB_NAMES),
                _numeric("incurred_amount", world.claim_severity[mask])]
    if with_status is not None:
        profiles.append(_categorical("claim_status", (~with_status[mask]).astype(int),
                                     CLAIM_STATUSES))
    return profiles


def _first_run_with_column(events, table, column) -> date | None:
    """Run date of the ingest that first loaded the column (its detected_at)."""
    for e in events:
        if (e["warehouse_object"], e["column"], e["warehouse_change"]) == (table, column,
                                                                           "column_added"):
            return datetime.fromisoformat(e["detected_at"]).date()
    return None


def build(world: World, row_volume: pd.DataFrame, schema_events: list[dict]) -> pd.DataFrame:
    run_ids = row_volume.set_index(["run_date", "table"])["run_id"]
    release_s = to_s(C.PAS_RELEASE_AT)
    pol_status_cd_from = _first_run_with_column(schema_events, "raw_policies", "pol_status_cd")

    rows = []
    for d in run_dates():
        st = run_state(world, d)
        tables = {}

        # raw_policies: this run's CDC batch. Rows written by PAS 4.12 carry
        # pol_status_cd and leave policy_status NULL; earlier rows lack pol_status_cd.
        lo, hi = np.searchsorted(world.touch_s, [st.prev, st.ingest], side="right")
        pid, t = world.touch_policy[lo:hi], world.touch_s[lo:hi]
        post = t > release_s
        status = world.policy_status[pid]
        tables["raw_policies"] = [
            _categorical("policy_status", np.where(post, NULL_STATUS, status), C.POLICY_STATUSES),
            _categorical("lob", world.policy_lob[pid], LOB_NAMES),
        ]
        if pol_status_cd_from is not None and d >= pol_status_cd_from:
            tables["raw_policies"].insert(1, _categorical(
                "pol_status_cd", np.where(post, status, NULL_STATUS), STATUS_CODES))

        # stg_policies selects policy_status explicitly; it does not carry pol_status_cd.
        tables["stg_policies"] = [
            _categorical("policy_status", st.policy_status[:st.n_policies], C.POLICY_STATUSES),
            _categorical("lob", world.policy_lob[:st.n_policies], LOB_NAMES),
        ]
        tables["stg_claims"] = _claims(world, st.reported, st.is_open)
        tables["int_claims_enriched"] = [
            _categorical("policy_status", st.policy_status[world.claim_policy[st.in_int]],
                         C.POLICY_STATUSES),
            *_claims(world, st.in_int, st.is_open),
        ]
        tables["fct_claims_closed"] = _claims(world, st.closed_window & st.active)
        tables["fct_claims_open"] = _claims(world, st.is_open & st.active)
        tables["fct_fnol"] = [_categorical("lob", world.claim_lob[st.fnol_day], LOB_NAMES)]

        for table, profiles in tables.items():
            for p in profiles:
                rows.append(dict(run_id=run_ids[(d.isoformat(), table)], run_date=d.isoformat(),
                                 table=table, **p))

    df = pd.DataFrame(rows)
    df["null_rate"] = (df.null_count / df.row_count.where(df.row_count > 0)).round(4)
    return df[["run_id", "run_date", "table", "column", "row_count", "null_count", "null_rate",
               "distinct_count", "mean", "category_mix"]]
