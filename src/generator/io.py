"""Deterministic writers: fixed float format, "\n" line endings, UTF-8."""

from pathlib import Path

import pandas as pd


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        df.to_csv(f, index=False, lineterminator="\n", float_format="%.4f")
