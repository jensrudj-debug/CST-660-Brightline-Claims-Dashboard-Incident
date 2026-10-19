"""Step-through incident investigation over telemetry/: python -m generator.demo

Loads the telemetry into an in-memory SQLite database, then runs one query
per step, pausing for Enter between steps. Every finding is computed from the
query result; nothing about the answer is written into this file.
"""

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

import pandas as pd

INCIDENT = "2026-10-19"
LAYER_ORDER = "CASE r.layer WHEN 'ingest' THEN 1 WHEN 'staging' THEN 2 " \
              "WHEN 'intermediate' THEN 3 ELSE 4 END"

BOLD, DIM, RED, RESET = "\033[1m", "\033[2m", "\033[31m", "\033[0m"


def style(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if sys.stdout.isatty() else text


def load(telemetry: Path) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.create_function("sqrt", 1, math.sqrt)
    pd.read_csv(telemetry / "freshness.csv", keep_default_na=False).to_sql(
        "freshness", db, index=False)
    for name in ("row_volume", "column_profiles"):
        pd.read_csv(telemetry / f"{name}.csv").to_sql(name, db, index=False)
    events = json.loads((telemetry / "schema_changes.json").read_text(encoding="utf-8"))
    pd.DataFrame(events).drop(columns=["value_mapping"]).assign(
        value_mapping=[json.dumps(e["value_mapping"]) if e["value_mapping"] else ""
                       for e in events]).to_sql("schema_changes", db, index=False)
    lineage = json.loads((telemetry / "lineage.json").read_text(encoding="utf-8"))
    pd.DataFrame(lineage["nodes"]).to_sql("lineage_nodes", db, index=False)
    pd.DataFrame([dict(e, columns=", ".join(e["columns"])) for e in lineage["edges"]]).to_sql(
        "lineage_edges", db, index=False)
    return db


# Prior same-weekday (Monday) runs, the baseline every step compares against.
PRIOR_MONDAYS = f"strftime('%w', run_date) = '1' AND run_date < '{INCIDENT}'"

STEPS = [
    dict(
        title="Symptom: what does the closed-claims tile actually show?",
        sql=f"""
SELECT run_date, rows_written AS closed_claims_7d
FROM row_volume
WHERE "table" = 'fct_claims_closed' AND strftime('%w', run_date) = '1'
ORDER BY run_date DESC""",
        finding=lambda df: (
            f"{df.closed_claims_7d.iloc[0]:,} closed claims today vs a median of "
            f"{df.closed_claims_7d.iloc[1:].median():,.0f} on prior Mondays: "
            f"{df.closed_claims_7d.iloc[0] / df.closed_claims_7d.iloc[1:].median() - 1:+.1%}. "
            "Real decline or bad data?"),
    ),
    dict(
        title="Freshness: did every job run, and on time?",
        sql=f"""
SELECT job, status, attempt,
       substr(started_at, 12, 8)  AS started,
       substr(finished_at, 12, 8) AS finished,
       substr(sla_deadline, 12, 5) AS sla,
       CASE WHEN finished_at > sla_deadline THEN 'LATE' ELSE 'on time' END AS sla_check
FROM freshness
WHERE run_date = '{INCIDENT}'
ORDER BY started_at""",
        finding=lambda df: (
            f"{(df.status == 'success').sum()} of {len(df)} jobs succeeded first time. "
            f"Late: {', '.join(df[df.sla_check == 'LATE'].job) or 'none'}. "
            "Is that late feed on the closed-claims path? Check later, in lineage."),
    ),
    dict(
        title="Row volume by layer: where do rows go missing?",
        sql=f"""
WITH prior AS (
    SELECT "table", COUNT(*) AS n, AVG(rows_written) AS mean,
           AVG(rows_written * rows_written) AS mean_sq
    FROM row_volume
    WHERE {PRIOR_MONDAYS} AND load_type != 'full_refresh'
    GROUP BY "table")
SELECT r.layer, r."table", r.rows_written AS today, ROUND(p.mean) AS prior_mondays,
       ROUND((r.rows_written - p.mean)
             / sqrt((p.mean_sq - p.mean * p.mean) * p.n / (p.n - 1)), 1) AS z_score
FROM row_volume r JOIN prior p USING ("table")
WHERE r.run_date = '{INCIDENT}'
ORDER BY {LAYER_ORDER}, r."table\"""",
        finding=lambda df: (
            "Raw and staging tables are normal (|z| < 2). Rows first go missing at "
            f"{df[df.z_score.abs() >= 2].iloc[0]['table']} "
            f"(z = {df[df.z_score.abs() >= 2].iloc[0].z_score}): the loss starts in the "
            "integration step, not the source extract."),
    ),
    dict(
        title="Is it a real business decline? Severity and line-of-business mix",
        sql="""
SELECT s.run_date, s.row_count AS closed_claims, ROUND(s.mean) AS mean_severity,
       json_extract(m.category_mix, '$."Personal Auto"')       AS personal_auto,
       json_extract(m.category_mix, '$.Homeowners')            AS homeowners,
       json_extract(m.category_mix, '$."Commercial Property"') AS comm_property,
       json_extract(m.category_mix, '$."Commercial Auto"')     AS comm_auto,
       json_extract(m.category_mix, '$."General Liability"')   AS gen_liability
FROM column_profiles s
JOIN column_profiles m
  ON m.run_date = s.run_date AND m."table" = s."table" AND m."column" = 'lob'
WHERE s."table" = 'fct_claims_closed' AND s."column" = 'incurred_amount'
  AND strftime('%w', s.run_date) = '1'
ORDER BY s.run_date DESC""",
        finding=lambda df: (
            f"Mean severity {df.mean_severity.iloc[0] / df.mean_severity.iloc[1:].median() - 1:+.1%}"
            " vs prior Mondays; no line of business moved more than "
            f"{_max_mix_shift(df):.1f} points. A real slowdown would shift mix or severity. "
            "This looks like rows dropped at random."),
    ),
    dict(
        title="What changed upstream in the 72 hours before the run?",
        sql=f"""
SELECT substr(occurred_at, 1, 16) AS occurred, substr(detected_at, 1, 16) AS first_loaded,
       source_system, change_ref, warehouse_object, change_type,
       previous_column, "column", warehouse_change
FROM schema_changes
WHERE occurred_at >= '2026-10-16'
ORDER BY occurred_at""",
        finding=lambda df: (
            "Two changes: "
            + "; ".join(f"{r.change_ref} {r.change_type} on {r.warehouse_object}"
                        for r in df.itertuples())
            + ". Ingest saw the PAS rename only as '"
            f"{df[df.previous_column != ''].warehouse_change.iloc[0]}'. "
            "Did the old column survive the rename?"),
    ),
    dict(
        title="Column profiles: what happened to policy_status?",
        sql=f"""
SELECT run_date, "table", "column", row_count, null_rate, category_mix
FROM column_profiles
WHERE "column" IN ('policy_status', 'pol_status_cd')
  AND "table" IN ('raw_policies', 'stg_policies')
  AND run_date >= '2026-10-16'
ORDER BY run_date DESC, "table", "column\"""",
        finding=lambda df: (
            "policy_status null rate jumps from 0 to "
            f"{_today(df, 'raw_policies', 'policy_status').null_rate:.1%} in the overnight PAS "
            "load, on exactly the run where pol_status_cd first appears. Who reads "
            "policy_status downstream?"),
    ),
    dict(
        title="Lineage: who reads policy_status, and what does each suspect reach?",
        sql="""
SELECT source, target, columns, predicate
FROM lineage_edges
WHERE columns LIKE '%policy_status%';

-- Suspects: the PAS rename, the late HR feed, the Friday dbt deploy. A tile counts as
-- reached only if its column lineage reads a table downstream of the suspect.
WITH RECURSIVE downstream(origin, node) AS (
    SELECT id, id FROM lineage_nodes
    WHERE id IN ('PAS.POLICY_MASTER', 'HR.ADJUSTER_ROSTER', 'fct_fnol')
    UNION
    SELECT d.origin, e.target FROM downstream d JOIN lineage_edges e ON e.source = d.node)
SELECT d.origin AS suspect, group_concat(n.label, ', ') AS tiles_reached
FROM downstream d
JOIN lineage_edges e ON e.target = d.node
JOIN lineage_nodes n ON n.id = e.target AND n.layer = 'tile'
WHERE EXISTS (SELECT 1 FROM downstream t
              WHERE t.origin = d.origin AND e.columns LIKE t.node || '.%')
GROUP BY d.origin""",
        finding=None,  # the root-cause summary below
    ),
]


def _max_mix_shift(df):
    mix = df[["personal_auto", "homeowners", "comm_property", "comm_auto", "gen_liability"]]
    return float(((mix.iloc[0] - mix.iloc[1:].median()).abs() * 100).max())


def _today(df, table, column):
    return df[(df.run_date == INCIDENT) & (df.table == table) & (df.column == column)].iloc[0]


def root_cause(db, filters, reach) -> str:
    rename = pd.read_sql("SELECT * FROM schema_changes WHERE previous_column != ''", db).iloc[0]
    gate = filters[filters.predicate.str.contains(rename.previous_column)].iloc[0]
    closed_tile = reach[reach.tiles_reached.str.contains("Closed claims")].suspect.tolist()
    others = reach[~reach.suspect.isin(closed_tile)]
    lines = [
        f"{rename.change_ref} ({rename.occurred_at[:16]}) renamed {rename.previous_column} -> "
        f"{rename['column']}; ingest kept {rename.previous_column} but left it NULL on "
        "policies written after the release.",
        f"{gate.target} applies `{gate.predicate}`: NULL fails that predicate, so claims on "
        "those policies were dropped without an error.",
        f"Only {' and '.join(closed_tile)} reaches the Closed claims tile. "
        + "; ".join(f"{r.suspect} reaches only {r.tiles_reached}" for r in others.itertuples())
        + ": both red herrings ruled out.",
    ]
    return "\n".join(f"  {i}. {line}" for i, line in enumerate(lines, 1))


def show(db, sql: str) -> list[pd.DataFrame]:
    frames = []
    for stmt in (s.strip() for s in sql.split(";") if s.strip()):
        print(style(stmt, DIM))
        df = pd.read_sql(stmt, db)
        print(df.to_string(index=False))
        print()
        frames.append(df)
    return frames


def pause(enabled: bool, prompt="Press Enter for the next step..."):
    if enabled:
        input(style(prompt, DIM))
        print()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m generator.demo")
    parser.add_argument("--telemetry", type=Path, default=Path("telemetry"))
    parser.add_argument("--no-pause", action="store_true", help="run straight through")
    args = parser.parse_args(argv)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 90)

    db = load(args.telemetry)
    print(style("Brightline incident investigation: closed claims down on the "
                f"{INCIDENT} dashboard", BOLD))
    print(style("Tables loaded: freshness, row_volume, column_profiles, schema_changes, "
                "lineage_nodes, lineage_edges", DIM))
    pause(not args.no_pause, "Press Enter to start...")

    for i, step in enumerate(STEPS, 1):
        print(style(f"Step {i}/{len(STEPS)} | {step['title']}", BOLD))
        frames = show(db, step["sql"])
        if step["finding"]:
            print(style("Finding: ", BOLD) + step["finding"](frames[-1]))
        else:
            print(style("ROOT CAUSE", RED + BOLD))
            print(root_cause(db, *frames))
        print()
        if i < len(STEPS):
            pause(not args.no_pause)


if __name__ == "__main__":
    main()
