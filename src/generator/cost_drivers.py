"""Cost driver table for the incident class: python -m generator.cost_drivers

Quantities come from telemetry/ and the incident timeline (via value_stream);
prices, headcounts and probabilities are the ASSUMPTIONS below, each stated in
the output. Writes deliverables/cost_driver_table.md and refreshes the copy in
README.md between the cost-table markers.
"""

import argparse
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pandas as pd

from . import config as C
from .value_stream import HR_DETOUR, RESPONSE, _t, fmt, measure

PATH_JOBS = ("raw_policies", "raw_claims", "stg_policies", "stg_claims", "int_claims_enriched",
             "fct_claims_closed", "fct_claims_open", "fct_fnol", "dash_claims_severity")

# Every price, headcount and probability used below. Rendered verbatim as the
# assumptions list, so the table never relies on an unstated number.
ASSUMPTIONS = dict(
    credit_usd=3.00,          # warehouse list price per credit
    eng_rate=95,              # loaded $/h: data, analytics and PAS engineers, BI analysts
    vp_rate=150,              # loaded $/h: VP of Claims Operations
    ops_user_rate=80,         # loaded $/h: Claims Ops dashboard users
    adjuster_rate=60,         # loaded $/h: claims adjuster
    investigation_credits=2.0,  # ad-hoc profiling, reconciliation and counterfactual queries
    triage_people=4,          # IC plus Data Eng, Analytics Eng and PAS on-call
    review_h=0.5,             # code review of the fix
    signoff_vp_h=0.25,        # VP time to sign off the corrected dashboard
    pir_people=5, pir_h=1.0,  # blameless post-incident review
    dashboard_users=20, user_lost_h=0.5,  # users who hit the untrusted dashboard
    misallocated_share=0.05,  # share of adjusters moved for one week on a bad number
    p_decision=0.25,          # chance a bad number drives a decision before detection
    incidents_per_year=4,     # observed 1 per 60 days (~6/yr); 4 assumes some break loudly
    gate_build_h=40, gate_maint_h_per_month=2, gate_triage_cut=0.5,
    contract_build_h=60, contract_review_h_per_release=1, contract_prevents=0.75,
)


@dataclass(frozen=True)
class Line:
    category: str
    driver: str
    quantity: str
    unit: str
    cost: float          # realized this incident
    expected: float      # expected per incident of the class
    basis: str


def money(x: float) -> str:
    return f"${x:,.0f}" if abs(x) >= 100 else f"${x:,.2f}"


def hours(td) -> float:
    return td.total_seconds() / 3600


def quantities(telemetry: Path) -> dict:
    fr = pd.read_csv(telemetry / "freshness.csv", keep_default_na=False)
    rv = pd.read_csv(telemetry / "row_volume.csv")
    events = json.loads((telemetry / "schema_changes.json").read_text(encoding="utf-8"))
    inc = C.INCIDENT_DATE.isoformat()
    ok = fr[fr.status == "success"]
    nightly = ok[ok.job.isin(PATH_JOBS)].groupby("run_date").credits_used.sum()
    source = [e for e in events if e["source_system"] != "dbt"]
    pas_releases = {e["change_ref"] for e in source if e["source_system"] == "PAS"}
    vs = measure(telemetry)
    response = {name: (_t(start), _t(end), pt) for name, _, start, end, pt, _ in RESPONSE}
    return dict(
        rerun_credits=float(nightly.loc[inc]),
        gate_credits_per_night=float(ok[ok.job == "int_claims_enriched"].credits_used.median()),
        window_credits=float(fr.credits_used.sum()),
        adjusters=int(rv[(rv.table == "raw_adjuster_hr") & (rv.run_date == inc)]
                      .rows_written.iloc[0]),
        published=vs["published"], true_count=vs["true_count"],
        source_changes=len(source), pas_releases_per_year=len(pas_releases) * 365 / C.WINDOW_DAYS,
        triage_h=hours(response["Triage + contain"][1] - response["Triage + contain"][0]),
        fix_h=sum(hours(_t(b) - _t(a)) for a, b in response["Fix + deploy"][2]),
        backfill_h=hours(response["Backfill + validate"][1] - response["Backfill + validate"][0]),
        signoff_h=hours(response["Republish + sign-off"][1] - response["Republish + sign-off"][0]),
        untrusted_h=hours(response["Republish + sign-off"][1] - response["Triage + contain"][0]),
        detour_h=hours(_t(HR_DETOUR[1]) - _t(HR_DETOUR[0])),
    )


def lines(q: dict, a: dict) -> list[Line]:
    eng, cr = a["eng_rate"], a["credit_usd"]
    triage_ph = a["triage_people"] * q["triage_h"]
    shortfall = q["true_count"] - q["published"]
    per_adjuster = q["true_count"] / q["adjusters"]
    moved = round(q["adjusters"] * a["misallocated_share"])
    exposure = moved * 40 * a["adjuster_rate"]
    users_h = a["dashboard_users"] * a["user_lost_h"]

    def same(category, driver, quantity, unit, cost, basis):
        return Line(category, driver, quantity, unit, cost, cost, basis)

    return [
        same("Compute", "Backfill rerun of the closed-claims path",
             f"{q['rerun_credits']:.2f} credits", f"{money(cr)}/credit",
             q["rerun_credits"] * cr, "freshness.csv, incident run (9 jobs)"),
        same("Compute", "Investigation queries (profiles, reconciliation, counterfactual)",
             f"{a['investigation_credits']:.1f} credits", f"{money(cr)}/credit",
             a["investigation_credits"] * cr, "assumption A6"),
        same("Rework", "Triage and containment, incl. the HR-feed detour",
             f"{a['triage_people']} people × {q['triage_h']:.2f} h = {triage_ph:.1f} h",
             f"${eng}/h", triage_ph * eng,
             f"timeline 06:40–10:20; detour {q['detour_h'] * 60:.0f} min"),
        same("Rework", "Fix and code review",
             f"{q['fix_h']:.2f} h + {a['review_h']} h", f"${eng}/h",
             (q["fix_h"] + a["review_h"]) * eng, "timeline 10:20–12:30"),
        same("Rework", "Backfill and reconciliation (attended)",
             f"{q['backfill_h']:.2f} h", f"${eng}/h", q["backfill_h"] * eng,
             "timeline 16:30–18:40"),
        same("Rework", "Republish and sign-off",
             f"BI {q['signoff_h']:.2f} h + VP {a['signoff_vp_h']} h",
             f"${eng}/h, ${a['vp_rate']}/h",
             q["signoff_h"] * eng + a["signoff_vp_h"] * a["vp_rate"], "timeline 18:40–19:30"),
        same("Rework", "Post-incident review",
             f"{a['pir_people']} people × {a['pir_h']} h", f"${eng}/h",
             a["pir_people"] * a["pir_h"] * eng, "assumption A9"),
        same("Business impact", "Dashboard untrusted for decision users",
             f"{users_h:.0f} h ({fmt(timedelta(hours=q['untrusted_h']))} untrusted)",
             f"${a['ops_user_rate']}/h",
             users_h * a["ops_user_rate"], "timeline 06:40–19:30; assumption A10"),
        Line("Business impact", "Staffing decision on a 38%-low closed-claims count",
             f"{moved} of {q['adjusters']} adjusters × 40 h "
             f"({shortfall:,} claims missing ≈ {shortfall / per_adjuster:.0f} adjuster-weeks)",
             f"${a['adjuster_rate']}/h", 0.0, exposure * a["p_decision"],
             f"exposure {money(exposure)} × p = {a['p_decision']:.0%}; $0 realized "
             "(contained 06:55)"),
    ]


def countermeasures(q: dict, a: dict, rows: list[Line]) -> list[dict]:
    eng = a["eng_rate"]
    per_incident = sum(r.expected for r in rows)
    triage = next(r.expected for r in rows if r.driver.startswith("Triage"))
    decision = next(r.expected for r in rows if r.driver.startswith("Staffing"))
    n = a["incidents_per_year"]
    gate_saving = n * (decision + triage * a["gate_triage_cut"])
    contract_saving = n * per_incident * a["contract_prevents"]
    out = [
        dict(name="Publish gate between `int_claims_enriched` and the marts: block on "
                  "row-volume z-score, key-column null rate and schema drift; show "
                  "\"data delayed\" on the dashboard",
             addresses="Bottleneck 1 (8 h 40 min undetected); every source, not just PAS",
             one_time=a["gate_build_h"] * eng,
             run=(q["gate_credits_per_night"] * 365 * a["credit_usd"]
                  + a["gate_maint_h_per_month"] * 12 * eng),
             saving=gate_saving,
             effect=f"bad numbers never reach the tile, so the staffing exposure goes to $0; "
                    f"triage cut {a['gate_triage_cut']:.0%}"),
        dict(name="Data contract on the PAS extract, tested in PAS CI, with data-consumer "
                  "sign-off for breaking changes",
             addresses="Root cause (prevents the class at the source)",
             one_time=a["contract_build_h"] * eng,
             run=a["contract_review_h_per_release"] * q["pas_releases_per_year"] * eng,
             saving=contract_saving,
             effect=f"{a['contract_prevents']:.0%} of incidents in the class never ship"),
    ]
    for c in out:
        c["net_year_1"] = c["saving"] - c["one_time"] - c["run"]
        c["payback_months"] = c["one_time"] / max((c["saving"] - c["run"]) / 12, 1e-9)
    return out


def render(q: dict, a: dict) -> str:
    rows = lines(q, a)
    cms = countermeasures(q, a, rows)
    n = a["incidents_per_year"]
    realized = sum(r.cost for r in rows)
    expected = sum(r.expected for r in rows)
    by_cat = {}
    for r in rows:
        by_cat[r.category] = by_cat.get(r.category, 0) + r.expected

    out = ["| Category | Driver | Quantity | Unit cost | This incident | Expected per incident "
           "| Basis |", "|---|---|---|---|---:|---:|---|"]
    for r in rows:
        out.append(f"| {r.category} | {r.driver} | {r.quantity} | {r.unit} | {money(r.cost)} | "
                   f"{money(r.expected)} | {r.basis} |")
    out.append(f"| **Total** | | | | **{money(realized)}** | **{money(expected)}** | "
               f"**{money(expected * n)}/yr** at {n} incidents/yr |")
    def share(v):
        return "<1%" if v / expected < 0.01 else f"{v / expected:.0%}"

    out += ["", "Expected cost per incident by category: " + ", ".join(
        f"{k.lower()} {money(v)} ({share(v)})" for k, v in by_cat.items())
        + f". Compute for the whole 60-day window was {q['window_credits']:.1f} credits "
        f"({money(q['window_credits'] * a['credit_usd'])}), so faster or cheaper compute "
        "would not change this incident class's cost.", "",
        "**Countermeasures, in priority order**", "",
        "| Priority | Countermeasure | Addresses | One-time cost | Annual run cost "
        "| Annual cost avoided | Net, year 1 | Payback |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for i, c in enumerate(cms, 1):
        out.append(f"| {i} | {c['name']} | {c['addresses']}; {c['effect']} | "
                   f"{money(c['one_time'])} | {money(c['run'])} | {money(c['saving'])} | "
                   f"{money(c['net_year_1'])} | {c['payback_months']:.1f} months |")
    out += ["", f"Priority 1 comes first: it is cheaper, pays back sooner, covers ClaimCenter "
            f"and Workday changes as well as PAS, and removes the largest driver (the staffing "
            f"decision) even when a breaking change still ships. Priority 2 prevents "
            f"{a['contract_prevents']:.0%} of incidents but needs PAS team time on every "
            f"release (~{q['pas_releases_per_year']:.0f}/yr observed). The two savings overlap "
            "(both act on the same incidents), so they are not additive.", "",
            "**Assumptions**", ""]
    notes = [
        ("A1", f"Warehouse credits cost {money(a['credit_usd'])} each (list price)."),
        ("A2", f"Loaded rates: engineers and BI analysts ${a['eng_rate']}/h, VP "
               f"${a['vp_rate']}/h, Claims Ops users ${a['ops_user_rate']}/h, adjusters "
               f"${a['adjuster_rate']}/h."),
        ("A3", "Hours come from the incident investigation timeline; credits, adjuster "
               "headcount and claim counts come from telemetry/."),
        ("A4", f"Triage involved {a['triage_people']} people for its full span (IC plus "
               "Data Eng, Analytics Eng and PAS on-call)."),
        ("A5", f"Fix review takes {a['review_h']} h; VP sign-off takes {a['signoff_vp_h']} h."),
        ("A6", f"Ad-hoc investigation queries used {a['investigation_credits']} credits."),
        ("A7", f"A staffing decision made on the bad number would move "
               f"{a['misallocated_share']:.0%} of adjusters for one week; the chance a bad "
               f"number drives such a decision before detection is {a['p_decision']:.0%}."),
        ("A8", f"The class recurs {n} times a year. Telemetry shows 1 silent breaking change "
               f"among {q['source_changes']} source schema changes in 60 days (~6/yr); 4 "
               "assumes some changes fail loudly instead."),
        ("A9", f"The post-incident review takes {a['pir_people']} people {a['pir_h']} h."),
        ("A10", f"{a['dashboard_users']} decision users each lose {a['user_lost_h']} h to the "
                "untrusted dashboard."),
        ("A11", f"Publish gate: {a['gate_build_h']} h to build, {a['gate_maint_h_per_month']} "
                f"h/month upkeep, nightly checks cost about the same as one "
                f"`int_claims_enriched` run ({q['gate_credits_per_night']:.3f} credits); it "
                f"cuts triage effort by {a['gate_triage_cut']:.0%}."),
        ("A12", f"Data contract: {a['contract_build_h']} h to build, "
                f"{a['contract_review_h_per_release']} h review per PAS release; it prevents "
                f"{a['contract_prevents']:.0%} of incidents (renames and type changes on "
                "contracted columns)."),
    ]
    out += [f"- **{k}.** {v}" for k, v in notes]
    return "\n".join(out) + "\n"


START, END = "<!-- cost-table:start -->", "<!-- cost-table:end -->"


def update_readme(readme: Path, table: str) -> None:
    text = readme.read_text(encoding="utf-8")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    readme.write_text(f"{head}{START}\n{table}{END}{tail}", encoding="utf-8", newline="\n")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m generator.cost_drivers")
    parser.add_argument("--telemetry", type=Path, default=Path("telemetry"))
    parser.add_argument("--out", type=Path, default=Path("deliverables/cost_driver_table.md"))
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args(argv)
    table = render(quantities(args.telemetry), ASSUMPTIONS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Cost driver table: silent upstream schema change → partial load\n\n" + table)
    if args.readme.exists() and START in args.readme.read_text(encoding="utf-8"):
        update_readme(args.readme, table)


if __name__ == "__main__":
    main()
