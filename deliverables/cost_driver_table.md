# Cost driver table: silent upstream schema change → partial load

| Category | Driver | Quantity | Unit cost | This incident | Expected per incident | Basis |
|---|---|---|---|---:|---:|---|
| Compute | Backfill rerun of the closed-claims path | 0.45 credits | $3.00/credit | $1.34 | $1.34 | freshness.csv, incident run (9 jobs) |
| Compute | Investigation queries (profiles, reconciliation, counterfactual) | 2.0 credits | $3.00/credit | $6.00 | $6.00 | assumption A6 |
| Rework | Triage and containment, incl. the HR-feed detour | 4 people × 3.67 h = 14.7 h | $95/h | $1,393 | $1,393 | timeline 06:40–10:20; detour 50 min |
| Rework | Fix and code review | 2.17 h + 0.5 h | $95/h | $253 | $253 | timeline 10:20–12:30 |
| Rework | Backfill and reconciliation (attended) | 2.17 h | $95/h | $206 | $206 | timeline 16:30–18:40 |
| Rework | Republish and sign-off | BI 0.83 h + VP 0.25 h | $95/h, $150/h | $117 | $117 | timeline 18:40–19:30 |
| Rework | Post-incident review | 5 people × 1.0 h | $95/h | $475 | $475 | assumption A9 |
| Business impact | Dashboard untrusted for decision users | 10 h (12 h 50 min untrusted) | $80/h | $800 | $800 | timeline 06:40–19:30; assumption A10 |
| Business impact | Staffing decision on a 38%-low closed-claims count | 11 of 212 adjusters × 40 h (536 claims missing ≈ 78 adjuster-weeks) | $60/h | $0.00 | $6,600 | exposure $26,400 × p = 25%; $0 realized (contained 06:55) |
| **Total** | | | | **$3,252** | **$9,852** | **$39,406/yr** at 4 incidents/yr |

Expected cost per incident by category: compute $7.34 (<1%), rework $2,444 (25%), business impact $7,400 (75%). Compute for the whole 60-day window was 27.9 credits ($83.67), so faster or cheaper compute would not change this incident class's cost.

**Countermeasures, in priority order**

| Priority | Countermeasure | Addresses | One-time cost | Annual run cost | Annual cost avoided | Net, year 1 | Payback |
|---|---|---|---:|---:|---:|---:|---:|
| 1 | Publish gate between `int_claims_enriched` and the marts: block on row-volume z-score, key-column null rate and schema drift; show "data delayed" on the dashboard | Bottleneck 1 (8 h 40 min undetected); every source, not just PAS; bad numbers never reach the tile, so the staffing exposure goes to $0; triage cut 50% | $3,800 | $2,369 | $29,187 | $23,017 | 1.7 months |
| 2 | Data contract on the PAS extract, tested in PAS CI, with data-consumer sign-off for breaking changes | Root cause (prevents the class at the source); 75% of incidents in the class never ship | $5,700 | $2,312 | $29,555 | $21,543 | 2.5 months |

Priority 1 comes first: it is cheaper, pays back sooner, covers ClaimCenter and Workday changes as well as PAS, and removes the largest driver (the staffing decision) even when a breaking change still ships. Priority 2 prevents 75% of incidents but needs PAS team time on every release (~24/yr observed). The two savings overlap (both act on the same incidents), so they are not additive.

**Assumptions**

- **A1.** Warehouse credits cost $3.00 each (list price).
- **A2.** Loaded rates: engineers and BI analysts $95/h, VP $150/h, Claims Ops users $80/h, adjusters $60/h.
- **A3.** Hours come from the incident investigation timeline; credits, adjuster headcount and claim counts come from telemetry/.
- **A4.** Triage involved 4 people for its full span (IC plus Data Eng, Analytics Eng and PAS on-call).
- **A5.** Fix review takes 0.5 h; VP sign-off takes 0.25 h.
- **A6.** Ad-hoc investigation queries used 2.0 credits.
- **A7.** A staffing decision made on the bad number would move 5% of adjusters for one week; the chance a bad number drives such a decision before detection is 25%.
- **A8.** The class recurs 4 times a year. Telemetry shows 1 silent breaking change among 8 source schema changes in 60 days (~6/yr); 4 assumes some changes fail loudly instead.
- **A9.** The post-incident review takes 5 people 1.0 h.
- **A10.** 20 decision users each lose 0.5 h to the untrusted dashboard.
- **A11.** Publish gate: 40 h to build, 2 h/month upkeep, nightly checks cost about the same as one `int_claims_enriched` run (0.082 credits); it cuts triage effort by 50%.
- **A12.** Data contract: 60 h to build, 1 h review per PAS release; it prevents 75% of incidents (renames and type changes on contracted columns).
