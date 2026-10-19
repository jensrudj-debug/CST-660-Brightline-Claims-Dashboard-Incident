"""row_volume.csv: one row per successful table-producing run."""

import pandas as pd

from . import config as C


def build(counts: pd.DataFrame, freshness: pd.DataFrame) -> pd.DataFrame:
    ok = freshness[freshness["status"] == "success"]
    run_ids = ok.set_index(["run_date", "job"])["run_id"]
    out = counts.assign(run_date=counts["run_date"].map(lambda d: d.isoformat()))
    out["run_id"] = [run_ids[(d, t)] for d, t in zip(out["run_date"], out["table"])]
    out["layer"] = out["table"].map(lambda t: C.JOBS[t]["layer"])
    return out[["run_id", "run_date", "table", "layer", "load_type", "rows_written",
                "table_row_count"]]
