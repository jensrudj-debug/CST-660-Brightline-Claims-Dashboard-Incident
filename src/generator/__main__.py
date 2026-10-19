"""Generate telemetry: python -m generator [--out telemetry]"""

import argparse
from pathlib import Path

from . import freshness, pipeline, row_volume, schema_changes
from .io import write_csv, write_json
from .world import simulate


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m generator")
    parser.add_argument("--out", type=Path, default=Path("telemetry"))
    args = parser.parse_args(argv)

    counts = pipeline.table_counts(simulate())
    fresh = freshness.build(counts)
    write_csv(fresh, args.out / "freshness.csv")
    write_csv(row_volume.build(counts, fresh), args.out / "row_volume.csv")
    write_json(schema_changes.build(fresh), args.out / "schema_changes.json")


if __name__ == "__main__":
    main()
