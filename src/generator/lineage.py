"""lineage.json and lineage.png: upstream dependencies from source systems to dashboard tiles.

Edges carry the columns each step reads and the step's SQL predicate, as a
lineage tool would extract them from model code. Node positions are fixed
(no automatic layout) so the PNG is byte-identical across runs.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from . import config as C  # noqa: E402

LAYERS = ("source", "ingest", "staging", "intermediate", "mart", "dashboard", "tile")

# id, system, owner, ingest job
SOURCES = (
    ("PAS.POLICY_MASTER", "PAS", "Policy Admin Engineering", "raw_policies"),
    ("CC.CLAIM", "ClaimCenter", "Claims Systems", "raw_claims"),
    ("HR.ADJUSTER_ROSTER", "Workday HR", "People Systems (vendor feed)", "raw_adjuster_hr"),
)
TILES = (
    ("tile_closed_claims", "Closed claims (7-day)", "dash_claims_severity"),
    ("tile_mean_severity", "Mean severity", "dash_claims_severity"),
    ("tile_open_inventory", "Open inventory", "dash_claims_severity"),
    ("tile_fnol_volume", "FNOL volume", "dash_claims_severity"),
    ("tile_adjuster_staffing", "Adjuster staffing", "dash_adjuster_staffing"),
)
# (upstream, downstream): columns read, SQL predicate
EDGE_DETAIL = {
    ("PAS.POLICY_MASTER", "raw_policies"): (["*"], "CDC since last sync"),
    ("CC.CLAIM", "raw_claims"): (["*"], "CDC since last sync"),
    ("HR.ADJUSTER_ROSTER", "raw_adjuster_hr"): (["*"], "daily roster file"),
    ("raw_policies", "stg_policies"): (["policy_id", "policy_status", "lob"],
                                       "latest row per policy_id"),
    ("raw_claims", "stg_claims"): (["claim_id", "policy_id", "lob", "reported_at", "closed_at",
                                    "incurred_amount"], "latest row per claim_id"),
    ("stg_policies", "int_claims_enriched"): (["policy_id", "policy_status"],
                                              "join on policy_id where policy_status = 'ACTIVE'"),
    ("stg_claims", "int_claims_enriched"): (["claim_id", "policy_id", "lob", "reported_at",
                                             "closed_at", "incurred_amount"],
                                            "closed_at is null or closed in trailing 7 days"),
    ("int_claims_enriched", "fct_claims_closed"): (["claim_id", "lob", "closed_at",
                                                    "incurred_amount"],
                                                   "closed_at in trailing 7 days"),
    ("int_claims_enriched", "fct_claims_open"): (["claim_id", "lob", "incurred_amount"],
                                                 "closed_at is null"),
    ("stg_claims", "fct_fnol"): (["claim_id", "lob", "reported_at"],
                                 "reported_at on run_date - 1"),
}
OWNER_DATA_PLATFORM = "Data Platform"
OWNER_BI = "Claims Analytics (BI)"

# Categorical slots 1-3 (validated all-pairs) by system boundary; owner is in the label too.
GROUP_COLOR = {"source": "#2a78d6", "platform": "#eb6834", "bi": "#1baf7a"}
SURFACE, INK, INK_2, MUTED, EDGE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"


def build_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    for node_id, system, owner, _ in SOURCES:
        g.add_node(node_id, layer="source", system=system, owner=owner, label=node_id)
    for job, spec in C.JOBS.items():
        owner = OWNER_BI if spec["layer"] == "dashboard" else OWNER_DATA_PLATFORM
        system = "BI extract" if spec["layer"] == "dashboard" else "warehouse"
        g.add_node(job, layer=spec["layer"], system=system, owner=owner, label=job,
                   schedule=spec["schedule"], sla=spec["sla"])
        for up in spec["upstream"]:
            g.add_edge(up, job)
    for node_id, _, _, job in SOURCES:
        g.add_edge(node_id, job)
    for tile, label, dash in TILES:
        g.add_node(tile, layer="tile", system="dashboard", owner=OWNER_BI, label=label)
        g.add_edge(dash, tile)
    assert nx.is_directed_acyclic_graph(g)
    return g


def to_json(g: nx.DiGraph) -> dict:
    nodes = [dict(id=n, **g.nodes[n]) for n in sorted(g, key=lambda n: (LAYERS.index(
        g.nodes[n]["layer"]), n))]
    edges = []
    for u, v in sorted(g.edges, key=lambda e: (LAYERS.index(g.nodes[e[1]]["layer"]), e)):
        columns, predicate = EDGE_DETAIL.get((u, v), ([], None))
        edges.append(dict(source=u, target=v, columns=columns, predicate=predicate))
    return dict(nodes=nodes, edges=edges)


# Fixed layout: (column, row) per node; rows top to bottom.
POSITIONS = {
    "PAS.POLICY_MASTER": (0, 1), "CC.CLAIM": (0, 2.5), "HR.ADJUSTER_ROSTER": (0, 4.25),
    "raw_policies": (1, 1), "raw_claims": (1, 2.5), "raw_adjuster_hr": (1, 4.25),
    "stg_policies": (2, 1), "stg_claims": (2, 2.5),
    "int_claims_enriched": (3, 1.75),
    "fct_claims_closed": (4, 0.75), "fct_claims_open": (4, 1.75), "fct_fnol": (4, 3),
    "dash_claims_severity": (5, 1.75), "dash_adjuster_staffing": (5, 4.25),
    "tile_closed_claims": (6, 0.5), "tile_mean_severity": (6, 1.35),
    "tile_open_inventory": (6, 2.2), "tile_fnol_volume": (6, 3.05),
    "tile_adjuster_staffing": (6, 4.25),
}
COL_W, ROW_H, BOX_W, BOX_H = 2.35, 1.0, 1.95, 0.62


def _xy(node):
    col, row = POSITIONS[node]
    return col * COL_W, -row * ROW_H


def _group(owner):
    return "platform" if owner == OWNER_DATA_PLATFORM else "bi" if owner == OWNER_BI else "source"


def render_png(g: nx.DiGraph, path: Path) -> None:
    assert set(POSITIONS) == set(g), "every node needs a fixed position"
    plt.rcParams.update({"font.family": "DejaVu Sans", "svg.hashsalt": "lineage"})
    fig, ax = plt.subplots(figsize=(16.5, 5.6), dpi=150)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.98, bottom=0.02)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_axis_off()

    for u, v in g.edges:
        (x0, y0), (x1, y1) = _xy(u), _xy(v)
        ax.add_patch(FancyArrowPatch((x0 + BOX_W / 2, y0), (x1 - BOX_W / 2, y1),
                                     arrowstyle="-|>", mutation_scale=10, color=EDGE, lw=1.2,
                                     shrinkA=0, shrinkB=1, zorder=1))

    for n, attrs in g.nodes(data=True):
        x, y = _xy(n)
        color = GROUP_COLOR[_group(attrs["owner"])]
        ax.add_patch(FancyBboxPatch((x - BOX_W / 2, y - BOX_H / 2), BOX_W, BOX_H,
                                    boxstyle="round,pad=0,rounding_size=0.08",
                                    fc=SURFACE, ec=color, lw=2, zorder=2))
        ax.text(x, y + 0.09, attrs["label"], ha="center", va="center", fontsize=8.6,
                color=INK, zorder=3)
        sub = attrs["owner"] if attrs["layer"] in ("source", "tile") else (
            f"{attrs['schedule']} · SLA {attrs['sla']}")
        ax.text(x, y - 0.15, sub, ha="center", va="center", fontsize=6.6, color=INK_2, zorder=3)

    for i, layer in enumerate(LAYERS):
        ax.text(i * COL_W, 0.05, layer.upper(), ha="center", va="bottom", fontsize=7.5,
                color=MUTED, fontweight="bold")

    legend = (("source", "Source systems"), ("platform", f"{OWNER_DATA_PLATFORM} (warehouse)"),
              ("bi", OWNER_BI))
    for i, (group, label) in enumerate(legend):
        x = i * 3.2
        ax.add_patch(FancyBboxPatch((x - 0.15, -5.17), 0.3, 0.18,
                                    boxstyle="round,pad=0,rounding_size=0.04",
                                    fc=SURFACE, ec=GROUP_COLOR[group], lw=2))
        ax.text(x + 0.25, -5.08, label, ha="left", va="center", fontsize=7.8, color=INK_2)
    ax.text(6 * COL_W + BOX_W / 2, -5.08, "Brightline claims-dashboard lineage", ha="right",
            va="center", fontsize=9, color=INK, fontweight="bold")

    ax.set_xlim(-BOX_W / 2 - 0.1, 6 * COL_W + BOX_W / 2 + 0.1)
    ax.set_ylim(-5.4, 0.35)
    ax.set_aspect("auto")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE, metadata={"Software": None})
    plt.close(fig)
