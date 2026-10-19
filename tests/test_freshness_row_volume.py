"""Structural properties of freshness.csv and row_volume.csv."""

import pandas as pd

from conftest import INCIDENT, mondays_before_incident


def test_open_claims_flat(rv):
    rows = rv[rv.table == "fct_claims_open"]
    change = (rows[rows.run_date == INCIDENT].rows_written.iloc[0]
              / mondays_before_incident(rows).rows_written.median() - 1)
    assert abs(change) < 0.03


def test_incident_day_all_green_and_only_hr_feed_late(fr):
    day = fr[fr.run_date == INCIDENT]
    assert (day.status == "success").all()
    late = day[pd.to_datetime(day.finished_at) > pd.to_datetime(day.sla_deadline)]
    assert late.job.tolist() == ["raw_adjuster_hr"]


def test_every_failed_attempt_has_a_successful_retry(fr):
    failed = fr[fr.status == "failed"]
    retried = fr[(fr.run_type == "retry") & (fr.status == "success")]
    assert set(zip(failed.run_date, failed.job)) == set(zip(retried.run_date, retried.job))


def test_row_volume_run_ids_exist_in_freshness(rv, fr):
    ok = set(fr[fr.status == "success"].run_id)
    assert set(rv.run_id) <= ok
