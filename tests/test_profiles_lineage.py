"""Answer-key properties of column_profiles.csv and lineage.json / lineage.png."""

import json

import pandas as pd
import pytest

from generator.__main__ import main

INCIDENT = "2026-10-19"


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    path = tmp_path_factory.mktemp("telemetry")
    main(["--out", str(path)])
    return path


@pytest.fixture(scope="module")
def cp(out):
    return pd.read_csv(out / "column_profiles.csv")


def profile(cp, table, column):
    rows = cp[(cp.table == table) & (cp.column == column)]
    prior = rows[(rows.run_date < INCIDENT) & (pd.to_datetime(rows.run_date).dt.dayofweek == 0)]
    return prior, rows[rows.run_date == INCIDENT].iloc[0]


def test_reruns_are_byte_identical(out, tmp_path):
    main(["--out", str(tmp_path)])
    for name in ("column_profiles.csv", "lineage.json", "lineage.png"):
        assert (tmp_path / name).read_bytes() == (out / name).read_bytes()


def test_policy_status_nulls_appear_only_after_release(cp):
    for table in ("raw_policies", "stg_policies"):
        prior, today = profile(cp, table, "policy_status")
        assert (prior.null_rate == 0).all()
        assert today.null_rate > 0


def test_pol_status_cd_profiled_only_from_incident_run(cp):
    runs = cp[(cp.table == "raw_policies") & (cp.column == "pol_status_cd")].run_date
    assert runs.tolist() == [INCIDENT]


def test_closed_mean_severity_moves_less_than_2_percent(cp):
    prior, today = profile(cp, "fct_claims_closed", "incurred_amount")
    assert abs(today["mean"] / prior["mean"].median() - 1) < 0.02


def test_closed_lob_mix_stable(cp):
    prior, today = profile(cp, "fct_claims_closed", "lob")
    base = pd.DataFrame([json.loads(m) for m in prior.category_mix]).median()
    shift = (pd.Series(json.loads(today.category_mix)) - base).abs()
    assert shift.max() < 0.03


def test_lineage_hr_feed_reaches_only_staffing_tile(out):
    lineage = json.loads((out / "lineage.json").read_text(encoding="utf-8"))
    edges = {(e["source"], e["target"]) for e in lineage["edges"]}
    reach, frontier = set(), {"HR.ADJUSTER_ROSTER"}
    while frontier:
        frontier = {t for s, t in edges if s in frontier} - reach
        reach |= frontier
    tiles = {n["id"] for n in lineage["nodes"] if n["layer"] == "tile"}
    assert reach & tiles == {"tile_adjuster_staffing"}
