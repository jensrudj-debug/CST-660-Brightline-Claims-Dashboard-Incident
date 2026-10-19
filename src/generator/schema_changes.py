"""schema_changes.json: routine source/dbt schema changes plus the two incident-window events.

Source-system changes are detected when the next ingest run picks them up
(started_at in freshness.csv); dbt changes are detected at deploy from the
manifest diff.
"""

from datetime import datetime, timedelta

import pandas as pd

from . import config as C
from .rng import child_rng
from .timeutil import HOUR, day_s, iso, to_s

# Routine changes stop before the incident weekend.
ROUTINE_END = C.DBT_DEPLOY_AT.date() - timedelta(days=2)
N_ROUTINE = (15, 25)

SOURCES = {
    "PAS": dict(source_object="PAS.POLICY_MASTER", warehouse_object="raw_policies",
                ingest_job="raw_policies"),
    "ClaimCenter": dict(source_object="CC.CLAIM", warehouse_object="raw_claims",
                        ingest_job="raw_claims"),
    "Workday HR": dict(source_object="HR.ADJUSTER_ROSTER", warehouse_object="raw_adjuster_hr",
                       ingest_job="raw_adjuster_hr"),
}
# PAS ships in the Sunday 22:00 maintenance window.
PAS_RELEASES = (("4.10", "2026-08-30"), ("4.11", "2026-09-20"), ("4.11.1", "2026-10-04"))

# source, object (dbt model or None), change_type, column, data_type, previous_data_type, description
ROUTINE_TEMPLATES = (
    ("PAS", None, "column_added", "agent_region_cd", "VARCHAR(4)", None,
     "Add agent region code for distribution reporting"),
    ("PAS", None, "column_added", "paperless_billing_flag", "BOOLEAN", None,
     "Add paperless billing enrollment flag"),
    ("PAS", None, "column_type_widened", "insured_name", "VARCHAR(120)", "VARCHAR(80)",
     "Widen insured_name for commercial named insureds"),
    ("PAS", None, "column_added", "telematics_enrolled_flag", "BOOLEAN", None,
     "Add telematics program enrollment flag"),
    ("PAS", None, "column_type_widened", "policy_form_cd", "VARCHAR(12)", "VARCHAR(8)",
     "Widen policy form code for new ISO forms"),
    ("PAS", None, "column_added", "last_inspection_dt", "DATE", None,
     "Add last property inspection date"),
    ("ClaimCenter", None, "column_added", "cat_event_code", "VARCHAR(10)", None,
     "Add catastrophe event code"),
    ("ClaimCenter", None, "column_added", "subrogation_flag", "BOOLEAN", None,
     "Add subrogation potential flag"),
    ("ClaimCenter", None, "column_type_widened", "loss_cause_detail", "VARCHAR(500)",
     "VARCHAR(255)", "Widen free-text loss cause detail"),
    ("ClaimCenter", None, "column_added", "litigation_flag", "BOOLEAN", None,
     "Add litigation indicator"),
    ("ClaimCenter", None, "column_type_widened", "reserve_amount", "NUMBER(14,2)",
     "NUMBER(12,2)", "Widen reserve amount precision for large commercial losses"),
    ("ClaimCenter", None, "column_added", "reopen_count", "INTEGER", None,
     "Add count of times a claim was reopened"),
    ("ClaimCenter", None, "column_added", "fnol_source_cd", "VARCHAR(8)", None,
     "Add FNOL intake source code (web, phone, agent, app)"),
    ("Workday HR", None, "column_added", "licensed_states", "VARCHAR(200)", None,
     "Add adjuster licensed-state list"),
    ("Workday HR", None, "column_added", "cat_deployable_flag", "BOOLEAN", None,
     "Add catastrophe-deployable flag"),
    ("Workday HR", None, "column_type_changed", "cost_center", "VARCHAR(10)", "INTEGER",
     "Cost center becomes alphanumeric after GL restructure"),
    ("Workday HR", None, "column_added", "supervisor_id", "VARCHAR(12)", None,
     "Add supervising adjuster id"),
    ("dbt", "stg_claims", "column_added", "claim_age_days", "INTEGER", None,
     "Derive claim age in days at load"),
    ("dbt", "stg_policies", "column_added", "policy_term_months", "INTEGER", None,
     "Derive policy term length"),
    ("dbt", "fct_claims_open", "column_added", "aging_bucket", "VARCHAR(10)", None,
     "Add open-claim aging bucket (0-30, 31-90, 90+)"),
    ("dbt", "fct_claims_closed", "column_added", "days_to_close", "INTEGER", None,
     "Add days from report to close"),
    ("dbt", "int_claims_enriched", "column_added", "policy_term_months", "INTEGER", None,
     "Carry policy term onto enriched claims"),
    ("dbt", "stg_claims", "test_added", "claim_id", None, None, "Add not_null and unique tests"),
    ("dbt", "fct_fnol", "test_added", "lob", None, None, "Add accepted_values test on LOB"),
    ("dbt", "fct_claims_closed", "test_added", "policy_id", None, None,
     "Add relationships test to stg_policies"),
    ("dbt", "int_claims_enriched", "docs_updated", None, None, None,
     "Document join keys and filters"),
    ("dbt", "fct_claims_open", "test_added", "claim_id", None, None,
     "Add unique test on claim_id"),
    ("dbt", "stg_policies", "test_added", "policy_id", None, None,
     "Add not_null and unique tests"),
)

FIELDS = ("event_id", "occurred_at", "detected_at", "source_system", "change_ref",
          "source_object", "warehouse_object", "change_type", "column", "previous_column",
          "data_type", "previous_data_type", "value_mapping", "warehouse_change", "detected_by",
          "description")


def _event(occurred_s: int, detected_s: int, **kw) -> dict:
    return {f: kw.get(f) for f in FIELDS} | {"occurred_s": occurred_s, "detected_s": detected_s}


def _detected_s(freshness: pd.DataFrame, job: str, occurred_s: int) -> int:
    """Start of the first successful ingest run scheduled after the change."""
    runs = freshness[(freshness.job == job) & (freshness.status == "success")]
    for sched, started in zip(runs.scheduled_at, runs.started_at):
        if _parse(sched) > occurred_s:
            return _parse(started)
    raise ValueError(f"no {job} run after change at {iso(occurred_s)}")


def _parse(ts: str) -> int:
    return to_s(datetime.fromisoformat(ts).replace(tzinfo=None))


def _source_event(freshness, source, occurred_s, change_ref, change_type, column, data_type,
                  prev_type, description, **extra) -> dict:
    src = SOURCES[source]
    warehouse_change = extra.pop("warehouse_change", change_type)
    return _event(occurred_s=occurred_s, source_system=source, change_ref=change_ref,
                  source_object=src["source_object"], warehouse_object=src["warehouse_object"],
                  change_type=change_type, column=column, data_type=data_type,
                  previous_data_type=prev_type, warehouse_change=warehouse_change,
                  detected_by="ingest_schema_drift", description=description,
                  detected_s=_detected_s(freshness, src["ingest_job"], occurred_s), **extra)


def build(freshness: pd.DataFrame) -> list[dict]:
    rng = child_rng("schema_changes")
    n = int(rng.integers(N_ROUTINE[0], N_ROUTINE[1] + 1))
    picks = rng.choice(len(ROUTINE_TEMPLATES), n, replace=False)
    weekdays = [d for d in pd.date_range(C.WINDOW_START, ROUTINE_END).date if d.weekday() < 5]

    events = []
    for i in sorted(picks):
        source, model, change_type, column, dtype, prev_type, desc = ROUTINE_TEMPLATES[i]
        u_slot, u_min = rng.random(2)
        if source == "PAS":
            version, day = PAS_RELEASES[int(u_slot * len(PAS_RELEASES))]
            occurred = day_s(datetime.fromisoformat(day).date()) + 22 * HOUR
            events.append(_source_event(freshness, source, occurred, f"PAS-REL-{version}",
                                        change_type, column, dtype, prev_type, desc))
        elif source == "dbt":
            day = weekdays[int(u_slot * len(weekdays))]
            occurred = day_s(day) + 10 * HOUR + int(u_min * 6 * HOUR)
            events.append(_event(occurred_s=occurred, detected_s=occurred, source_system="dbt",
                                 warehouse_object=model, change_type=change_type, column=column,
                                 data_type=dtype, warehouse_change=change_type,
                                 detected_by="dbt_manifest_diff", description=desc))
        else:
            # ClaimCenter maintenance ~20:00; Workday schema notices take effect ~18:00.
            day = weekdays[int(u_slot * len(weekdays))]
            hour = 20 if source == "ClaimCenter" else 18
            occurred = day_s(day) + hour * HOUR + int(u_min * HOUR)
            events.append(_source_event(freshness, source, occurred, None, change_type, column,
                                        dtype, prev_type, desc))

    release_s = to_s(C.PAS_RELEASE_AT)
    events.append(_source_event(
        freshness, "PAS", release_s, "PAS-REL-4.12", "column_renamed", "pol_status_cd",
        "CHAR(1)", "VARCHAR(12)",
        "Policy status standardized to single-character code under pol_status_cd. "
        "Legacy policy_status retained for backward compatibility.",
        previous_column="policy_status",
        value_mapping={"ACTIVE": "A", "LAPSED": "L", "CANCELLED": "C"},
        warehouse_change="column_added"))
    deploy_s = to_s(C.DBT_DEPLOY_AT)
    events.append(_event(occurred_s=deploy_s, detected_s=deploy_s, source_system="dbt",
                         warehouse_object=C.DBT_DEPLOY_MODEL, change_type="column_added",
                         column="reporting_channel", data_type="VARCHAR(8)",
                         warehouse_change="column_added", detected_by="dbt_manifest_diff",
                         description="Add reporting_channel for intake-mix tile; "
                                     "requires full refresh"))

    events.sort(key=lambda e: (e["occurred_s"], e["source_system"], e["column"] or ""))
    _number_refs(rng, events)
    for i, e in enumerate(events, 1):
        e["event_id"] = f"SC-{i:03d}"
        e["occurred_at"] = iso(e.pop("occurred_s"))
        e["detected_at"] = iso(e.pop("detected_s"))
    return [{f: e[f] for f in FIELDS} for e in events]


def _number_refs(rng, events) -> None:
    """Change tickets and PR numbers increase with time, as real ones do."""
    chg, wd, pr = 40_812, 1_207, 371
    for e in events:
        if e["change_ref"]:
            continue
        step = int(rng.integers(1, 7))
        if e["source_system"] == "ClaimCenter":
            chg += step * 9
            e["change_ref"] = f"CHG00{chg}"
        elif e["source_system"] == "Workday HR":
            wd += step
            e["change_ref"] = f"WD-NOTICE-{wd}"
        else:
            pr += step
            e["change_ref"] = f"PR #{pr}"
