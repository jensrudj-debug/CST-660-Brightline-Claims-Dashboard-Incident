"""Value stream map of the incident: python -m generator.value_stream

Nightly-delivery stages are measured from telemetry/freshness.csv (incident
run). Response stages use the times in the incident investigation timeline.
Their process time comes from the same sources: triage counts the timeline
steps that ended in a recorded finding, the backfill and republish count the
measured compute of rerunning the pipeline and refreshing BI, and everything
else in a stage's span is waiting.
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from . import config as C  # noqa: E402
from .pipeline import ACTIVE, run_state  # noqa: E402
from .world import simulate  # noqa: E402

DETECTED_AT = datetime(2026, 10, 19, 6, 40)  # VP reports the drop
STAGE_JOBS = {
    "ingest": ("raw_policies", "raw_claims"),
    "transform": ("stg_policies", "stg_claims", "int_claims_enriched", "fct_claims_closed",
                  "fct_claims_open", "fct_fnol"),
    "bi": ("dash_claims_severity",),
}
# Incident response, from the timeline doc: (name, owner, start, end, process time source,
# wait label). Process time sources: a list of timeline work intervals, or "rerun" / "bi"
# for the measured compute of the nightly path / the BI refresh.
RESPONSE = (
    ("Triage + contain", "IC + three teams", "06:40", "10:20",
     [("06:40", "06:55"), ("08:00", "10:20")], "HR-feed detour, wait for bridge call"),
    ("Fix + deploy", "Analytics Eng.", "10:20", "16:30", [("10:20", "12:30")],
     "change-approval queue"),
    ("Backfill + validate", "Data Engineering", "16:30", "18:40", "rerun",
     "warehouse slot, reconciliation"),
    ("Republish + sign-off", "BI + Claims Ops", "18:40", "19:30", "bi", "VP and BI sign-off"),
)
PT_NOTE = {"rerun": " (rerun)", "bi": " (refresh)"}
HR_DETOUR = ("07:10", "08:00")

SURFACE, INK, INK_2, MUTED, EDGE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"
CRIT, TINT = "#d03b3b", "#f0efec"


def _t(hhmm: str) -> datetime:
    return datetime.combine(DETECTED_AT.date(), datetime.strptime(hhmm, "%H:%M").time())


def fmt(td: timedelta) -> str:
    s = int(round(td.total_seconds()))
    if s < 3600:
        m, s = divmod(s, 60)
        return f"{m} min {s:02d} s" if m < 10 else f"{m} min"
    h, m = divmod(round(s / 60), 60)
    return f"{h} h {m:02d} min"


def measure(telemetry: Path) -> dict:
    fr = pd.read_csv(telemetry / "freshness.csv", keep_default_na=False)
    cp = pd.read_csv(telemetry / "column_profiles.csv")
    inc = C.INCIDENT_DATE.isoformat()
    day = fr[(fr.run_date == inc) & (fr.status == "success")].set_index("job")
    started = pd.to_datetime(day.started_at).dt.tz_localize(None)
    finished = pd.to_datetime(day.finished_at).dt.tz_localize(None)

    spans = {k: (started[list(j)].min(), finished[list(j)].max()) for k, j in STAGE_JOBS.items()}
    path_jobs = [j for jobs in STAGE_JOBS.values() for j in jobs]

    pas = cp[(cp.run_date == inc) & (cp.table == "raw_policies") & (cp.column == "policy_status")]
    world = simulate()
    st = run_state(world, C.INCIDENT_DATE)
    published = int((st.closed_window & st.active).sum())
    true_count = int((st.closed_window & (world.policy_status[world.claim_policy] == ACTIVE)).sum())
    return dict(
        spans=spans,
        ca_ingest=1 - float(pas.null_rate.iloc[0]),
        ca_closed=published / true_count,
        published=published, true_count=true_count,
        path_credits=float(day.loc[path_jobs, "credits_used"].sum()),
        retries=int((fr.run_type == "retry").sum()),
        full_refreshes=int((fr.run_type == "full_refresh").sum()),
    )


def stages(m: dict) -> tuple[list[dict], list[dict]]:
    """Nightly and response stages with process time, wait after, and lead time."""
    sp = m["spans"]
    release = C.PAS_RELEASE_AT
    nightly = [
        dict(name="Upstream release", owner="PAS team · PAS 4.12", start=release, end=release,
             pt=timedelta(0), next_start=sp["ingest"][0], ca="C&A 0% (no notice sent)",
             wait_label="next ingest window", supplier=True),
        dict(name="Ingest + land", owner="Data Engineering", start=sp["ingest"][0],
             end=sp["ingest"][1], next_start=sp["transform"][0],
             ca=f"C&A {m['ca_ingest']:.1%} of PAS rows", wait_label="dbt schedule (04:00)"),
        dict(name="Staging → marts (dbt)", owner="Analytics Eng.", start=sp["transform"][0],
             end=sp["transform"][1], next_start=sp["bi"][0],
             ca=f"C&A {m['ca_closed']:.0%} of closed claims", wait_label="BI schedule (05:45)"),
        dict(name="BI refresh", owner="Claims Analytics (BI)", start=sp["bi"][0],
             end=sp["bi"][1], next_start=DETECTED_AT, ca=f"C&A {m['ca_closed']:.0%}",
             wait_label="until the VP opens it"),
    ]
    for s in nightly:
        s["measured"] = True
        s.setdefault("pt", s["end"] - s["start"])
        s["wait"] = s["next_start"] - s["end"]

    measured_pt = {"rerun": sum((s["pt"] for s in nightly), timedelta()),
                   "bi": nightly[-1]["pt"]}
    response = []
    for name, owner, start, end, pt_source, wait_label in RESPONSE:
        lt = _t(end) - _t(start)
        if isinstance(pt_source, str):
            pt, note = measured_pt[pt_source], PT_NOTE[pt_source]
        else:
            pt, note = sum((_t(b) - _t(a) for a, b in pt_source), timedelta()), ""
        response.append(dict(name=name, owner=owner, start=_t(start), end=_t(end), pt=pt,
                             wait=lt - pt, ca="C&A 100%", wait_label=wait_label, note=note))
    for s in nightly + response:
        s["lt"] = s["pt"] + s["wait"]
    return nightly, response


# --- Drawing -------------------------------------------------------------------

BOX_W, BOX_H = 205, 104
COLS = (30, 268, 506, 744)
ROW_A, ROW_B = 498, 220  # box bottoms


def _box(ax, x, y, s, bad=False):
    style = dict(fc="#fbeaea" if bad else SURFACE, ec=CRIT if bad else EDGE,
                 lw=2.2 if bad else 1.3, ls="--" if s.get("supplier") else "-")
    ax.add_patch(FancyBboxPatch((x, y), BOX_W, BOX_H, boxstyle="round,pad=0,rounding_size=7",
                                **style, zorder=2))
    tx = x + 11
    ax.text(tx, y + 84, s["name"], fontsize=10.5, fontweight="bold", color=INK, va="center")
    ax.text(tx, y + 66, s["owner"], fontsize=8.4, color=INK_2, va="center")
    clock = (f"{s['start']:%a %H:%M}" if s["pt"] == timedelta(0)
             else f"{s['start']:%H:%M:%S}–{s['end']:%H:%M:%S}" if s.get("measured")
             else f"{s['start']:%H:%M}–{s['end']:%H:%M}")
    ax.text(tx, y + 49, clock, fontsize=8.4, color=INK_2, va="center")
    pt = "—" if s["pt"] == timedelta(0) else fmt(s["pt"]) + s.get("note", "")
    ax.text(tx, y + 30, f"PT {pt}   ·   LT {fmt(s['lt'])}", fontsize=8.6, color=INK,
            va="center", fontweight="bold")
    ax.text(tx, y + 12, s["ca"], fontsize=8.4, color=INK, va="center")


def _ladder(ax, segs, hi, lo):
    """segs: (x_from, x_to, kind, label, color, bold); kind 'pt' on the low rung, 'wait' high."""
    for i, (x0, x1, kind, label, color, bold) in enumerate(segs):
        y = lo if kind == "pt" else hi
        ax.plot([x0, x1], [y, y], color=color, lw=2.6 if color == CRIT else 1.4,
                solid_capstyle="butt", zorder=1)
        if i:  # riser joining this rung to the previous one
            ax.plot([x0, x0], [lo, hi], color=EDGE, lw=1.4, zorder=1)
        ty = lo - 11 if kind == "pt" else hi + 9
        ax.text((x0 + x1) / 2, ty, label, ha="center", va="center", fontsize=8.2,
                color=CRIT if color == CRIT else INK, fontweight="bold" if bold else "normal")


def _arrow(ax, path, dashed=False, color=EDGE, lw=1.3):
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        last = (x1, y1) == path[-1]
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>" if last else "-",
                                     mutation_scale=11, color=color, lw=lw,
                                     ls=(0, (4, 3)) if dashed else "-", shrinkA=0, shrinkB=0,
                                     zorder=1))


def render(m: dict, path: Path) -> None:
    nightly, response = stages(m)
    total_lt = response[-1]["end"] - C.PAS_RELEASE_AT
    total_pt = sum((s["pt"] for s in nightly + response), timedelta())
    total_wait = total_lt - total_pt
    undetected = DETECTED_AT - C.PAS_RELEASE_AT
    undetected_wait = sum((s["wait"] for s in nightly), timedelta())
    fix = response[1]

    plt.rcParams.update({"font.family": "DejaVu Sans"})
    fig, ax = plt.subplots(figsize=(16, 11.2), dpi=150)
    fig.subplots_adjust(left=0.005, right=0.995, top=0.995, bottom=0.005)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_axis_off()
    ax.set_xlim(0, 1000)
    ax.set_ylim(0, 700)

    eff = total_pt / total_lt
    ax.text(30, 678,f"Waiting, not work, filled {1 - eff:.0%} of the {fmt(total_lt)} incident",
            fontsize=16, fontweight="bold", color=INK, va="center")
    ax.text(30, 656, "Value stream map · Brightline claims-severity pipeline · PAS 4.12 release "
            "(Sun 22:00) to trusted dashboard (Mon 19:30), all times Central",
            fontsize=9.5, color=INK_2, va="center")
    # Assignment requirements, keyed to how the map shows each one.
    key = ((30, 52, "PT / LT", "process time and lead time per stage, in each box"),
           (360, 40, "ladder", "low rung = process time, high rung = wait state"),
           (660, 38, "- - ->", "rework loop"), (790, 22, "red", "two largest bottlenecks"))
    for x, w, tag, desc in key:
        ax.text(x, 634, tag, fontsize=8.6, fontweight="bold",
                color=CRIT if tag == "red" else INK, va="center")
        ax.text(x + w + 6, 634, desc, fontsize=8.6, color=INK_2, va="center")

    # Row A: nightly delivery, left to right.
    ax.text(30, 614,"NIGHTLY DELIVERY  ·  measured: telemetry/freshness.csv, incident run",
            fontsize=8.8, fontweight="bold", color=MUTED, va="center")
    for x, s in zip(COLS, nightly):
        _box(ax, x, ROW_A, s)
    for xa, xb in zip(COLS, COLS[1:]):
        _arrow(ax, [(xa + BOX_W, ROW_A + 52), (xb, ROW_A + 52)])
    _arrow(ax, [(COLS[3] + BOX_W, ROW_A + 52), (980, ROW_A + 52), (980, ROW_B + 52),
                (COLS[3] + BOX_W, ROW_B + 52)])
    ax.text(991, (ROW_A + ROW_B) / 2 + 52, "detected 06:40", ha="center", va="center",
            rotation=-90, fontsize=8.4, color=INK_2)

    hi, lo = ROW_A - 18, ROW_A - 40
    segs = []
    for i, (x, s) in enumerate(zip(COLS, nightly)):
        x_next = COLS[i + 1] + 8 if i + 1 < len(COLS) else 968
        if s["pt"] > timedelta(0):
            segs.append((x + 8, x + 96, "pt", fmt(s["pt"]), INK_2, False))
            segs.append((x + 96, x_next, "wait", f"wait {fmt(s['wait'])} · {s['wait_label']}",
                         CRIT, False))
        else:
            segs.append((x + 8, x_next, "wait", f"wait {fmt(s['wait'])} · {s['wait_label']}",
                         CRIT, False))
    _ladder(ax, segs, hi, lo)
    ax.plot([30, 30, 968, 968], [lo - 26, lo - 32, lo - 32, lo - 26], color=CRIT, lw=1.6)
    ax.text(499, lo - 45,
            f"Bottleneck 1 · undetected propagation: {fmt(undetected)} from release to "
            f"detection, {fmt(undetected_wait)} of it waiting between scheduled batches "
            f"with no volume or null-rate check",
            ha="center", va="center", fontsize=9.2, fontweight="bold", color=CRIT, zorder=3,
            bbox=dict(fc=SURFACE, ec="none", pad=1.5))

    # Row B: incident response, right to left.
    ax.text(30, ROW_B + BOX_H + 22, "INCIDENT RESPONSE  ·  read right to left  ·  times: "
            "incident investigation timeline", fontsize=8.8, fontweight="bold", color=MUTED,
            va="center")
    for x, s in zip(reversed(COLS), response):
        _box(ax, x, ROW_B, s, bad=s is fix)
    for xa, xb in zip(list(reversed(COLS)), list(reversed(COLS))[1:]):
        _arrow(ax, [(xa, ROW_B + 52), (xb + BOX_W, ROW_B + 52)])

    hi, lo = ROW_B - 18, ROW_B - 40
    segs = []
    xs = list(reversed(COLS))
    for i, (x, s) in enumerate(zip(xs, response)):
        x_next = xs[i + 1] + BOX_W - 8 if i + 1 < len(xs) else 38
        bad = s is fix
        segs.append((x + BOX_W - 8, x + BOX_W - 96, "pt", fmt(s["pt"]), INK_2, False))
        segs.append((x + BOX_W - 96, x_next, "wait",
                     f"wait {fmt(s['wait'])} · {s['wait_label']}",
                     CRIT if bad else EDGE, bad))
    _ladder(ax, segs, hi, lo)
    ax.text(COLS[2] + BOX_W / 2, lo - 26,
            f"Bottleneck 2 · fix-approval queue: the patch was ready at 12:30 and waited "
            f"{fmt(fix['wait'])}, the largest single wait in the incident",
            ha="center", va="center", fontsize=9.2, fontweight="bold", color=CRIT)

    # Rework loops.
    bx = COLS[1] + BOX_W - 30
    _arrow(ax, [(bx, ROW_B + BOX_H), (bx, ROW_A - 48)], dashed=True, color=INK_2)
    ax.text(bx - 10, ROW_B + BOX_H + 50,
            f"Rework 1 · backfill reruns ingest → BI:\n{m['path_credits']:.2f} credits and "
            f"{fmt(sum((s['pt'] for s in nightly), timedelta()))} again",
            fontsize=8.3, color=INK_2, va="center", ha="right")
    tx0, tx1 = COLS[3] + 40, COLS[3] + 165
    ax.add_patch(FancyArrowPatch((tx1, ROW_B + BOX_H), (tx0, ROW_B + BOX_H),
                                 connectionstyle="arc3,rad=0.9", arrowstyle="-|>",
                                 mutation_scale=11, color=INK_2, lw=1.3, ls=(0, (4, 3))))
    d0, d1 = (_t(t) for t in HR_DETOUR)
    ax.text(tx0 - 14, ROW_B + BOX_H + 44,
            f"Rework 2 · HR-feed detour {HR_DETOUR[0]}–{HR_DETOUR[1]} ({fmt(d1 - d0)}):\n"
            "a late feed on another tile's path", ha="right", va="center", fontsize=8.3,
            color=INK_2)
    ax.text(30, 30,
            f"Routine rework, 60-day window: {m['retries']} failed jobs retried after a "
            f"{C.RETRY_BACKOFF_S // 60}-min backoff, {m['full_refreshes']} full refresh "
            "after the Friday dbt deploy",
            fontsize=8.3, color=INK_2, va="center")

    # Totals.
    ax.add_patch(FancyBboxPatch((30, 82), 938, 40, boxstyle="round,pad=0,rounding_size=7",
                                fc=TINT, ec=EDGE, lw=1.2))
    ax.text(499, 102,
            f"Lead time {fmt(total_lt)}   ·   Process time {fmt(total_pt)}   ·   "
            f"Wait {fmt(total_wait)}   ·   Flow efficiency {eff:.0%}",
            ha="center", va="center", fontsize=11.5, fontweight="bold", color=INK)
    ax.text(30, 56,
            f"C&A = share of output the next stage can use without rework (closed claims: "
            f"{m['published']:,} published of {m['true_count']:,}). Nightly, rerun and refresh "
            "PT are measured compute from freshness.csv; triage and fix PT are the timeline "
            "steps that ended in a finding.", fontsize=8, color=INK_2, va="center")

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE, metadata={"Software": None})
    plt.close(fig)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m generator.value_stream")
    parser.add_argument("--telemetry", type=Path, default=Path("telemetry"))
    parser.add_argument("--out", type=Path, default=Path("deliverables/value_stream_map.png"))
    args = parser.parse_args(argv)
    render(measure(args.telemetry), args.out)


if __name__ == "__main__":
    main()
