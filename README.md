# Brightline Claims-Dashboard Incident

CST-660 assignment. At 6:40 a.m. the VP of Claims Operations reports that the daily
claims-severity dashboard shows a 38% drop in closed claims, and staffing decisions
are due in two hours. This repo generates the observability evidence for that
incident as reproducible synthetic telemetry, then uses it to investigate.

## Quick start

```sh
python -m venv .venv
.venv\Scripts\activate        # Windows; use `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"

make generate                 # writes telemetry/
make test                     # checks the telemetry against the answer key
```

Without `make`: `python -m generator --out telemetry` and `python -m pytest`.

Requires Python 3.11+. Dependencies are pinned exactly in `pyproject.toml`
because outputs must be byte-identical across runs.

## Telemetry

60 daily runs, Thu 2026-08-21 through the incident on Mon 2026-10-19. Times are
America/Chicago (CDT, `-05:00`).

| File | Grain | Contents |
|---|---|---|
| `freshness.csv` | job attempt | scheduled / queued / started / finished times, SLA deadline, status, error, run type (scheduled, retry, full_refresh), warehouse, credits used, rows scanned |
| `row_volume.csv` | table per run | rows written and resulting table size, load type, `run_id` linking to `freshness.csv` |
| `schema_changes.json` | change event | source and dbt schema changes: when each occurred and when ingest first loaded it, change ticket or PR, column and type changes, value mappings |
| `column_profiles.csv` | column per table per run | row count, null rate, distinct count, category mix (JSON), mean of `incurred_amount` |
| `lineage.json` | node / edge | source systems → ingest → staging → intermediate → marts → dashboards → tiles, with owners, schedules, columns read and SQL predicates per edge |
| `lineage.png` | | rendered lineage graph |

The pipeline: three sources (PAS policy admin, ClaimCenter, Workday HR) are
ingested at 02:00–03:00, dbt builds staging, `int_claims_enriched`, and three marts
from 04:00, and the dashboard extracts refresh at 05:45.

## How the data is generated

`src/generator/` simulates the entities first: about 167k policies, 64k claims
(from a January burn-in so open inventory is steady), and every write to a policy
record. `pipeline.py` then applies each model's logic to those rows run by run.
Row counts, null rates, and severity means are computed from those results, never
scaled or written in directly, so every anomaly in the telemetry follows from a
mechanism you can trace.

| Module | Role |
|---|---|
| `config.py` | window, incident events, pipeline DAG, simulation parameters |
| `world.py` | policies, claims, policy write events |
| `pipeline.py` | per-run row-level state and table row counts |
| `freshness.py`, `row_volume.py`, `schema_changes.py`, `column_profiles.py`, `lineage.py` | one output each |
| `rng.py` | named random streams from `default_rng(42)`; adding a component never reshuffles another's draws |

## Reproducibility

- One seed (42); each component draws from its own named child stream.
- CSVs use fixed float formats; JSON is key-sorted; every file uses `\n` line
  endings (enforced in git by `.gitattributes`); no generation timestamps.
- `lineage.png` uses fixed node positions, the bundled DejaVu Sans font, and no
  software metadata.
- `make test` regenerates everything and compares it byte for byte.

## Tests

`tests/test_answer_key.py` checks the properties the investigation depends on,
computed from the generated files: byte-identical re-runs, the closed-claims drop
size, raw-table volumes against prior Mondays, the timing of the `policy_status`
null-rate change relative to the schema log, and the presence of the red herrings.
The other test files check structural consistency across outputs.

## Deliverables

1. **[Incident investigation timeline](https://claude.ai/artifact/Vuy5u9GGzoUGQiEJcTStTc)**:
   symptoms vs six candidate causes, what each telemetry file ruled in or out,
   a minute-by-minute timeline, and the root cause with five lines of evidence.
   The answer: PAS release 4.12 renamed `policy_status` to `pol_status_cd`,
   leaving the old column NULL on policies written after the release, and the
   `policy_status = 'ACTIVE'` filter in `int_claims_enriched` silently dropped
   them. The tile showed 926 closed claims against a true 1,462 (−38.0% vs the
   prior-Monday median of 1,494) while every job ran green and on time.

In progress:

2. Value stream map of the analytics delivery flow: lead and process time per
   stage, wait states, rework loops, two largest bottlenecks
3. Cost driver table: compute, rework, and business-impact costs, two
   prioritized countermeasures, stated assumptions
4. 5–10 minute recorded walkthrough for the VP of Claims Operations and the
   platform lead

## Repository layout

```
src/generator/   telemetry generator (python -m generator)
telemetry/       generated outputs (committed)
tests/           pytest suite
CLAUDE.md        project spec and answer key for the generator
```
