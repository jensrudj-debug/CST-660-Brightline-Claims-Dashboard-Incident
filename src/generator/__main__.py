"""Generate telemetry: python -m generator [--out telemetry]"""

import argparse
from pathlib import Path

from . import column_profiles, freshness, lineage, pipeline, row_volume, schema_changes
from .io import write_csv, write_json
from .world import simulate


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m generator")
    parser.add_argument("--out", type=Path, default=Path("telemetry"))
    args = parser.parse_args(argv)

    world = simulate()
    counts = pipeline.table_counts(world)
    fresh = freshness.build(counts)
    volume = row_volume.build(counts, fresh)
    events = schema_changes.build(fresh)
    write_csv(fresh, args.out / "freshness.csv")
    write_csv(volume, args.out / "row_volume.csv")
    write_json(events, args.out / "schema_changes.json")
    write_csv(column_profiles.build(world, volume, events), args.out / "column_profiles.csv")

    graph = lineage.build_graph()
    write_json(lineage.to_json(graph), args.out / "lineage.json")
    lineage.render_png(graph, args.out / "lineage.png")


if __name__ == "__main__":
    main()
