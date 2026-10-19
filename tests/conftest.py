"""Generate the telemetry once per test session and share the loaded outputs."""

import json

import pandas as pd
import pytest

from generator.__main__ import main

INCIDENT = "2026-10-19"


@pytest.fixture(scope="session")
def out(tmp_path_factory):
    path = tmp_path_factory.mktemp("telemetry")
    main(["--out", str(path)])
    return path


@pytest.fixture(scope="session")
def rv(out):
    return pd.read_csv(out / "row_volume.csv")


@pytest.fixture(scope="session")
def fr(out):
    return pd.read_csv(out / "freshness.csv", keep_default_na=False)


@pytest.fixture(scope="session")
def cp(out):
    return pd.read_csv(out / "column_profiles.csv")


@pytest.fixture(scope="session")
def events(out):
    return json.loads((out / "schema_changes.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def lineage(out):
    return json.loads((out / "lineage.json").read_text(encoding="utf-8"))


def mondays_before_incident(df):
    """Rows from prior Monday runs: the same weekday as the incident run."""
    return df[(df.run_date < INCIDENT) & (pd.to_datetime(df.run_date).dt.dayofweek == 0)]
