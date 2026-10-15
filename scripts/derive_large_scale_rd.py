"""Derive paper Eq. (44) RD from an immutable large-scale result table.

This is a post hoc analysis utility, not an experiment runner or solver output.
"""
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


def derive(source: Path, output: Path) -> None:
    with source.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = []
    for row in rows:
        icp, hcp = row["ICP_Cmax"], row["HCP_Cmax"]
        rd = (float(hcp) - float(icp)) / float(icp) * 100 if icp and hcp and float(icp) != 0 else None
        result.append({
            "instance": row["Instance"], "icp_status": row["ICP_Status"], "hcp_status": row["HCP_Status"],
            "icp_cmax": icp, "hcp_cmax": hcp,
            "rd_percent_eq44_icp_denominator": "" if rd is None else format(rd, ".12g"),
            "raw_rd_percent_hcp_denominator": row["RD_Percent"],
            "comparison_category": row["Comparison_Category"],
            "formula": "(HCP_Cmax-ICP_Cmax)/ICP_Cmax*100",
            "input_file": str(source), "script": "scripts/derive_large_scale_rd.py",
            "computed_utc": stamp, "record_kind": "DERIVED_NOT_SOLVER_OUTPUT",
        })
    if len(result) != 60:
        raise ValueError(f"Expected 60 rows, got {len(result)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result[0]))
        writer.writeheader()
        writer.writerows(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("results/large_scale/raw/final_60_results.csv"))
    parser.add_argument("--output", type=Path, default=Path("local_runs/final_60_rd_eq44.csv"))
    args = parser.parse_args()
    derive(args.input, args.output)
