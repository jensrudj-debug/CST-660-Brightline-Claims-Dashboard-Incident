# Project: Brightline claims-dashboard incident telemetry
Goal: generate reproducible synthetic observability data for a 60-day window ending Mon 2026-10-19.

## Answer key (the data must obey this)
- Sun 22:00: PAS release 4.12 renames policy_status -> pol_status_cd (ACTIVE -> A)
- Mon 02:00 ingest: old column kept but NULL for policies touched since release
- int_claims_enriched filters policy_status = 'ACTIVE' -> NULLs silently dropped
- Closed claims touch policy records, so fct_claims_closed drops ~38%; open claims ~flat
- All pipeline jobs green and on time (HR feed is green but late; see red herrings);
  mean severity moves <2%; line-of-business mix stable

## Red herrings
- Adjuster HR feed 50 min late on incident day (feeds a different tile)
- Unrelated dbt deploy Friday; mild weekend FNOL dip within normal range

## Rules
- numpy default_rng(42); re-runs must be byte-identical
- Outputs in telemetry/: freshness.csv, row_volume.csv, schema_changes.json,
  column_profiles.csv, lineage.json (+ lineage.png)
- Anomalies must be computable from the data, never hard-coded in text

## Clarifications
### Causal mechanics
- The ~38% drop must come from a modeled process (the share of closed claims
  whose policy was touched since release), not from scaling counts by 0.38.
  Generator parameters are fine; output text must not state the result
- Root cause: upstream PAS column rename broke the join filter in
  int_claims_enriched, producing a partial load that looks like a business
  decline (jobs succeed, rows are simply missing)
- Partial-load mechanism: fct_claims_closed rebuilds a trailing 7-day window
  of closed claims joined to the current policy snapshot. Claim closures write
  loss history back to the policy with a lag; write-backs landing after the
  Sun 22:00 release leave policy_status NULL, so those claims fail the
  'ACTIVE' filter. The ~38% emerges from the modeled write-back lag
  distribution (share of trailing-window closures written back post-release)
- Open claims stay flat because only claim closure touches policy records;
  open claims' policies are untouched
- Which closed claims get dropped must be independent of severity and LOB,
  so mean severity (<2%) and LOB mix stay stable
- The window ends on incident Monday, so there is one anomalous day. Define
  "normal range" as within 2 sigma of the same weekday in prior weeks; the
  weekend FNOL dip must fall inside it

### Reproducibility
- Derive per-table child generators from default_rng(42) (SeedSequence spawn)
  so adding one table does not reshuffle the others
- Fixed float formatting in CSVs, JSON with sort_keys, "\n" line endings,
  no generation timestamps in any output
- lineage.png: pinned library versions, fixed node positions (no automatic
  layout), savefig(metadata={"Software": None})

### Defaults (change if needed)
- Timezone: America/Chicago, stored with offset; no DST change in the window
  (window is Thu 2026-08-21 to Mon 2026-10-19; DST ends Nov 1)
- Tables (proposed): raw_policies, raw_claims, raw_adjuster_hr, stg_policies,
  stg_claims, int_claims_enriched, fct_claims_closed, fct_claims_open,
  fct_fnol, dashboard tiles (severity dashboard; adjuster staffing tile)
- column_profiles.csv per column per run: null_rate, distinct_count,
  top_values / category mix, mean (for severity). This is where policy_status
  NULLs and pol_status_cd = 'A' show up

## Telemetry must also support the downstream artifacts
- freshness.csv: per run and per stage, record queued_at, started_at, finished_at
  (not just last-updated) so lead time, process time, and wait time are derivable
- Per run, record compute usage (duration, warehouse credits, rows scanned)
  so compute cost is calculated, not assumed
- Include rework events (reruns, backfills, reopened tickets) so rework loops
  show up in the data
- row_volume.csv: one row per run (with run_id), not daily aggregates
- column_profiles.csv: null rate, category mix, and mean severity

## Deliverables (beyond telemetry)
1. Incident investigation timeline: symptoms vs candidate causes, what each
   signal ruled in/out, root cause with supporting evidence
2. Value stream map of the analytics delivery flow: lead time and process time
   per stage, wait states, rework loops, two largest bottlenecks
3. Cost driver table: compute, rework, and business-impact costs of this
   incident class; two prioritized countermeasures; stated assumptions
- Context: VP reports at 6:40 a.m.; staffing decisions in 2 hours; three teams
  disputing ownership (maps to the two red herrings + the real cause)
- 5-10 min camera-on walkthrough (VP Claims Ops + platform lead) is recorded by
  the user: blameless, answer first, no script reading. Claude may help with
  talking points only, not a script to read
