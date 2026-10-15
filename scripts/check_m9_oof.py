"""Verify archived July 22 OOF arithmetic only; no model fitting or solver work."""
import csv
from collections import defaultdict
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "results/feature_analysis/model_oof"


def read(name):
    with (BASE / name).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    preds = read("Model_OOF_Predictions_By_Repeat_18Models.csv")
    folds = read("Group_Fold_Assignments_20Repeats.csv")
    archived = {r["Model"]: r for r in read("Residual_Summary_By_Model_18Models.csv")}
    if len(preds) != 86400 or len(folds) != 520 or len(archived) != 18:
        raise ValueError("Historical source row counts changed")
    fold_map = {(r["Repeat"], r["Base_Instance"]): r["Fold"] for r in folds}
    if len(fold_map) != 520:
        raise ValueError("Duplicate fold assignment")
    grouped = defaultdict(list)
    observed = {}
    fold_errors = 0
    for r in preds:
        key = (r["Model"], r["Instance"])
        grouped[key].append(float(r["OOF_Prediction"]))
        y = float(r["Observed_RD"])
        if key in observed and abs(observed[key] - y) > 1e-9:
            raise ValueError(f"Conflicting observed RD: {key}")
        observed[key] = y
        if fold_map.get((r["Repeat"], r["Base_Instance"])) != r["Fold"]:
            fold_errors += 1
    if len(grouped) != 4320 or any(len(v) != 20 for v in grouped.values()) or fold_errors:
        raise ValueError("Incomplete OOF repetition or fold mismatch")
    metrics = {}
    for model in sorted(archived):
        keys = [key for key in grouped if key[0] == model]
        actual = [observed[key] for key in keys]
        predicted = [math.fsum(grouped[key]) / 20 for key in keys]
        mean_actual = math.fsum(actual) / len(actual)
        residual = [a - b for a, b in zip(actual, predicted)]
        r2 = 1 - math.fsum(v * v for v in residual) / math.fsum((v - mean_actual) ** 2 for v in actual)
        rmse = math.sqrt(math.fsum(v * v for v in residual) / len(residual))
        mae = math.fsum(abs(v) for v in residual) / len(residual)
        for name, value in (("OOF_R2", r2), ("OOF_RMSE", rmse), ("OOF_MAE", mae)):
            if abs(value - float(archived[model][name])) > 1e-9:
                raise ValueError(f"Summary mismatch: {model} {name}")
        metrics[model] = {"r2": r2, "rmse": rmse, "mae": mae}
    print(json.dumps({"prediction_rows": len(preds), "fold_rows": len(folds),
                      "models": len(metrics), "instances_per_model": 240,
                      "repeats": 20, "fold_mapping_errors": fold_errors,
                      "m9_archived": metrics["M9"],
                      "m9_paper": {"r2": 0.7525, "rmse": 4.8530, "mae": 3.5566},
                      "m9_status": "MISMATCH_WITH_PAPER"}, indent=2))


if __name__ == "__main__":
    main()
