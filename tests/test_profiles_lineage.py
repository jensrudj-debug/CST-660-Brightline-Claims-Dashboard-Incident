"""Structural properties of column_profiles.csv and lineage.json."""

import json

import pandas as pd

from conftest import INCIDENT, mondays_before_incident


def profile(cp, table, column):
    rows = cp[(cp.table == table) & (cp.column == column)]
    return mondays_before_incident(rows), rows[rows.run_date == INCIDENT].iloc[0]


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


def test_lineage_edges_reference_known_nodes(lineage):
    ids = {n["id"] for n in lineage["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in lineage["edges"])
