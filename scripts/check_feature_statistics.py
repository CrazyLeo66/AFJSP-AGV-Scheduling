"""Recompute Table 6 correlations and Eq. (45) full-data OLS, without model refits."""
import csv
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "results/feature_analysis/core"


def rank(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    j = 0
    while j < len(values):
        k = j + 1
        while k < len(values) and values[order[k]] == values[order[j]]:
            k += 1
        mean_rank = (j + 1 + k) / 2
        for index in order[j:k]:
            result[index] = mean_rank
        j = k
    return result


def corr(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(
        sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)
    )


def main():
    with (CORE / "core_data_240.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with (CORE / "table6_univariate.csv").open(newline="", encoding="utf-8") as f:
        reported = {r["Feature"]: r for r in csv.DictReader(f)}
    if len(rows) != 240:
        raise ValueError("Expected 240 feature rows")
    y = [float(r["RD_percent"]) for r in rows]
    correlations = {}
    for feature in ("AS", "NMLS", "AD", "AW", "AED", "AOS"):
        values = [float(r[feature]) for r in rows]
        pearson = corr(values, y)
        spearman = corr(rank(values), rank(y))
        if abs(pearson - float(reported[feature]["Pearson_r"])) > 1e-9 or abs(spearman - float(reported[feature]["Spearman_rho"])) > 1e-9:
            raise ValueError(f"Table 6 mismatch: {feature}")
        correlations[feature] = {"pearson": pearson, "spearman": spearman}
    X = np.array([[float(r[c]) for c in ("AS", "NMLS", "AOS")] for r in rows])
    target = np.array(y)
    means = X.mean(axis=0)
    sample_sd = X.std(axis=0, ddof=1)
    z = (X - means) / sample_sd
    matrix = np.column_stack((np.ones(len(target)), z))
    coefficients = np.linalg.lstsq(matrix, target, rcond=None)[0]
    predicted = matrix @ coefficients
    r2 = 1 - np.sum((target - predicted) ** 2) / np.sum((target - target.mean()) ** 2)
    output = {"rows": len(rows), "table6": correlations, "eq45_feature_order": ["AS", "NMLS", "AOS"],
              "means": means.tolist(), "sample_sd_ddof1": sample_sd.tolist(),
              "intercept_and_coefficients": coefficients.tolist(), "in_sample_r2": float(r2),
              "oof_status": "NOT_REPRODUCED"}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
