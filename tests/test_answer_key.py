"""The answer key in CLAUDE.md, checked against the generated telemetry."""

import json
from datetime import datetime

import pandas as pd
import pytest

from conftest import INCIDENT, mondays_before_incident
from generator.__main__ import main

OUTPUTS = ("freshness.csv", "row_volume.csv", "schema_changes.json", "column_profiles.csv",
           "lineage.json", "lineage.png")


def rows_written(rv, table):
    rows = rv[(rv.table == table) & (rv.load_type != "full_refresh")]
    return (mondays_before_incident(rows).rows_written,
            rows[rows.run_date == INCIDENT].rows_written.iloc[0])


def pas_rename(events):
    return next(e for e in events if e["previous_column"] == "policy_status")


def reachable(lineage, start):
    """Every node downstream of start."""
    edges = {(e["source"], e["target"]) for e in lineage["edges"]}
    reach, frontier = set(), {start}
    while frontier:
        frontier = {t for s, t in edges if s in frontier} - reach
        reach |= frontier
    return reach


# --- Reproducibility -----------------------------------------------------------

def test_reruns_are_byte_identical(out, tmp_path):
    main(["--out", str(tmp_path)])
    for name in OUTPUTS:
        assert (tmp_path / name).read_bytes() == (out / name).read_bytes(), name


# --- Symptom -------------------------------------------------------------------

def test_closed_claims_drop_between_35_and_40_percent(rv):
    prior, today = rows_written(rv, "fct_claims_closed")
    drop = 1 - today / prior.median()
    assert 0.35 <= drop <= 0.40, f"drop {drop:.1%}"


@pytest.mark.parametrize("table", ["raw_policies", "raw_claims", "raw_adjuster_hr"])
def test_raw_tables_within_2_sd_of_prior_mondays(rv, table):
    prior, today = rows_written(rv, table)
    z = (today - prior.mean()) / prior.std()
    assert abs(z) < 2, f"{table} z={z:.2f}"


# --- Root cause ----------------------------------------------------------------

def test_policy_status_null_spike_lines_up_with_rename(cp, events):
    rename = pas_rename(events)
    detected_run = datetime.fromisoformat(rename["detected_at"]).date().isoformat()
    for table in ("raw_policies", "stg_policies"):
        rows = cp[(cp.table == table) & (cp.column == "policy_status")]
        with_nulls = rows[rows.null_rate > 0].run_date
        assert with_nulls.min() == detected_run, table
        assert (rows[rows.run_date < detected_run].null_rate == 0).all(), table
    assert datetime.fromisoformat(rename["occurred_at"]) < datetime.fromisoformat(
        rename["detected_at"])


# --- Red herrings ----------------------------------------------------------------

def test_hr_feed_50_minutes_late_and_feeds_only_staffing_tile(fr, lineage):
    hr = fr[(fr.job == "raw_adjuster_hr") & (fr.status == "success")]
    start = pd.to_datetime(hr.started_at)
    minutes = start.dt.hour * 60 + start.dt.minute
    late = minutes[hr.run_date == INCIDENT].iloc[0] - minutes[hr.run_date < INCIDENT].median()
    assert 45 <= late <= 55

    tiles = {n["id"] for n in lineage["nodes"] if n["layer"] == "tile"}
    assert reachable(lineage, "HR.ADJUSTER_ROSTER") & tiles == {"tile_adjuster_staffing"}


def test_friday_dbt_deploy_logged_and_unrelated_to_closed_claims(events, fr, lineage):
    deploy = [e for e in events if e["source_system"] == "dbt"
              and datetime.fromisoformat(e["occurred_at"]).weekday() == 4
              and e["occurred_at"].startswith("2026-10-16")]
    assert len(deploy) == 1
    model = deploy[0]["warehouse_object"]
    refresh = fr[fr.run_type == "full_refresh"]
    assert refresh.job.tolist() == [model]
    assert "fct_claims_closed" not in reachable(lineage, model)


def test_weekend_fnol_dip_within_normal_range(rv):
    prior, sunday = rows_written(rv, "fct_fnol")  # Monday runs load Sunday's FNOL
    weekdays = rv[(rv.table == "fct_fnol") & (rv.load_type == "incremental")
                  & pd.to_datetime(rv.run_date).dt.dayofweek.between(1, 5)].rows_written
    assert sunday < weekdays.mean()                             # a visible dip...
    assert abs(sunday - prior.mean()) < 2 * prior.std()         # ...within normal range


def test_deploy_reaches_only_fnol_tile_through_column_lineage(lineage):
    tile_reads = {e["target"]: {c.split(".")[0] for c in e["columns"]}
                  for e in lineage["edges"] if e["target"].startswith("tile_")}
    assert {t for t, tables in tile_reads.items() if "fct_fnol" in tables} == {"tile_fnol_volume"}


def test_investigation_demo_runs_and_names_the_root_cause(out, capsys):
    from generator import demo

    demo.main(["--telemetry", str(out), "--no-pause"])
    text = capsys.readouterr().out
    assert "ROOT CAUSE" in text
    assert "int_claims_enriched applies `join on policy_id where policy_status = 'ACTIVE'`" in text
    assert "Only PAS.POLICY_MASTER reaches the Closed claims tile" in text
