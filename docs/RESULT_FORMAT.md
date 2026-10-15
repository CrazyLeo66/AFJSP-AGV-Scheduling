# Result format

Result CSVs and JSONs retain the archived numeric objectives, statuses, optimality gaps, runtimes, and experimental parameters. Path fields in this publication copy point to repository files where a direct mapping was available; other historical local paths use a neutral `LOCAL_SOURCE` prefix. The original unmodified records are retained outside this repository.

## Main and large-scale comparisons

[`results/main_comparison/raw/`](../results/main_comparison/raw/) contains 51-case method summaries and failure records. Per-instance MILP and ICP outputs are in `details/`. HCP is represented by its archived summary; these records do not assert a fresh solver run.

[`results/large_scale/raw/run_level_results.csv`](../results/large_scale/raw/run_level_results.csv) has one row per instance and method, including status, Cmax, bound, gap, runtime, source batch, retry information, and mapped log/solution paths. `final_60_results.csv` has one paired row per instance. The raw `RD_Percent` column uses HCP as denominator. The separate [`derived/final_60_rd_eq44.csv`](../results/large_scale/derived/final_60_rd_eq44.csv) follows the manuscript's Eq. (44):

```text
RD (%) = 100 × (HCP_Cmax − ICP_Cmax) / ICP_Cmax
```

RD is blank when either Cmax is unavailable. The derived file records its formula and source and is **not** an original solver output. Run `python scripts/derive_large_scale_rd.py --output local_runs/final_60_rd_eq44.csv` to recalculate it without solving.

## Sensitivity and feature analysis

[`results/sensitivity/configuration_index.csv`](../results/sensitivity/configuration_index.csv) maps each of 574 final configurations to its input, HCP result and log, with status, Cmax, gap, runtime, and stage limits. The empty ICP fields accurately indicate that this release contains HCP sensitivity outcomes, not paired ICP–HCP results. All indexed HCP records have an assigned first-stage limit at most 900 seconds and a total 3600-second stage budget in the archived fields.

The 240-case feature core table contains six study features (`AS`, `NMLS`, `AD`, `AW`, `AED`, `AOS`), ICP/HCP Cmax, and RD. Historical repeated-OOF files in `results/feature_analysis/model_oof/` contain 86,400 prediction rows and 520 base-fold rows. They are labeled as historical analysis records. In those records, M9 OOF R² is approximately 0.748683; the manuscript reports 0.7525. No values have been changed to remove that difference.
