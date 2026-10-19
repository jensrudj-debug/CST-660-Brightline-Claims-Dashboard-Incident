"""Answer-key properties of freshness.csv and row_volume.csv, computed from the data."""

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
def rv(out):
    return pd.read_csv(out / "row_volume.csv")


@pytest.fixture(scope="module")
def fr(out):
    return pd.read_csv(out / "freshness.csv", keep_default_na=False)


def prior_mondays(rv, table):
    rows = rv[(rv.table == table) & (rv.run_date < INCIDENT)
              & (pd.to_datetime(rv.run_date).dt.dayofweek == 0) & (rv.load_type != "full_refresh")]
    return rows.rows_written


def incident(rv, table):
    return rv[(rv.table == table) & (rv.run_date == INCIDENT)].rows_written.iloc[0]


def test_reruns_are_byte_identical(out, tmp_path):
    main(["--out", str(tmp_path)])
    for name in ("freshness.csv", "row_volume.csv"):
        assert (tmp_path / name).read_bytes() == (out / name).read_bytes()


def test_closed_claims_drop_about_38_percent(rv):
    drop = 1 - incident(rv, "fct_claims_closed") / prior_mondays(rv, "fct_claims_closed").median()
    assert 0.34 <= drop <= 0.42


def test_open_claims_flat(rv):
    change = incident(rv, "fct_claims_open") / prior_mondays(rv, "fct_claims_open").median() - 1
    assert abs(change) < 0.03


def test_sunday_fnol_dip_within_normal_range(rv):
    base = prior_mondays(rv, "fct_fnol")
    assert abs(incident(rv, "fct_fnol") - base.mean()) < 2 * base.std()


def test_incident_day_all_green_and_only_hr_feed_late(fr):
    day = fr[fr.run_date == INCIDENT]
    assert (day.status == "success").all()
    late = day[pd.to_datetime(day.finished_at) > pd.to_datetime(day.sla_deadline)]
    assert late.job.tolist() == ["raw_adjuster_hr"]


def test_hr_feed_about_50_minutes_late(fr):
    hr = fr[fr.job == "raw_adjuster_hr"]
    start = pd.to_datetime(hr.started_at)
    minutes = start.dt.hour * 60 + start.dt.minute
    late = minutes[hr.run_date == INCIDENT].iloc[0] - minutes[hr.run_date < INCIDENT].median()
    assert 45 <= late <= 55
