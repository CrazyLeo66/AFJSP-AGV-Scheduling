# Usage

Run commands from the repository root. Install [`requirements.txt`](../requirements.txt) into a Python 3.12 environment first. Gurobi needs a valid license. Solvers were not run during preparation of this release copy; commands below are based on the inspected source arguments and existing inputs.

## Small instance

The README gives the ICP example. A MILP invocation uses the same instance and a separate output location:

```bash
python algorithms/milp/milp.py \
  instances/main_51/Deroussi_Norre/DN_AFJSPT01_BT.json \
  --time-limit 30 --no-lp \
  --output local_runs/milp_small.json
```

The historical main HCP CLI accepts `--input`, `--phase1-time-limit`, `--phase2-time-limit`, and `--phase2-mode retime`. Its own stage-limit allocation does not establish the exact historical budget used for every archived main comparison result. Use the archived summaries for analysis unless a separate experiment protocol has been confirmed.

## Data analysis without solving

```bash
python scripts/derive_large_scale_rd.py --output local_runs/final_60_rd_eq44.csv
python scripts/check_feature_statistics.py
python scripts/check_m9_oof.py
```

The first command writes only a derived RD table. The two checks recompute Table 6/whole-data linear coefficients and historical OOF aggregation from included CSVs. They do not solve scheduling instances or retrain the 18 models.

The historical sensitivity driver is [`experiments/sensitivity/run_hcp.py`](../experiments/sensitivity/run_hcp.py), and the original large-scale batch driver is [`experiments/large_scale/Runner/run_final_60_new_solve.py`](../experiments/large_scale/Runner/run_final_60_new_solve.py). Their personal path defaults were replaced with repository paths and local output directories; mathematical model logic was retained. The large-scale driver uses Windows process APIs and is not a cross-platform entry point. These batch drivers have not been executed in this release preparation; avoid launching their default long-running experiments merely to inspect the archived results.
