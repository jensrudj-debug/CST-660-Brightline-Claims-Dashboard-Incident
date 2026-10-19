"""Structural properties of schema_changes.json."""

from datetime import datetime


def ts(s):
    return datetime.fromisoformat(s)


def pas_rename(events):
    return next(e for e in events if e["change_ref"] == "PAS-REL-4.12")


def dbt_deploy(events):
    return next(e for e in events if e["occurred_at"].startswith("2026-10-16")
                and e["source_system"] == "dbt")


def test_routine_event_count(events):
    special = {pas_rename(events)["event_id"], dbt_deploy(events)["event_id"]}
    assert 15 <= len([e for e in events if e["event_id"] not in special]) <= 25


def test_pas_rename_at_release_seen_by_ingest_as_additive(events):
    e = pas_rename(events)
    assert e["occurred_at"] == "2026-10-18T22:00:00-05:00"
    assert (e["previous_column"], e["column"]) == ("policy_status", "pol_status_cd")
    assert e["value_mapping"]["ACTIVE"] == "A"
    assert e["warehouse_change"] == "column_added"
    assert e["detected_at"].startswith("2026-10-19T02:")


def test_detection_follows_occurrence_and_ids_are_chronological(events):
    assert all(ts(e["detected_at"]) >= ts(e["occurred_at"]) for e in events)
    assert [e["occurred_at"] for e in events] == sorted(e["occurred_at"] for e in events)


def test_no_routine_change_touches_policy_status(events):
    others = [e for e in events if e["change_ref"] != "PAS-REL-4.12"]
    assert not any("status" in (e["column"] or "") for e in others)
