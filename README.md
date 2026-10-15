# AFJSP-AGV-Scheduling

Research code and experimental data for flexible job shop scheduling with assembly precedence constraints and automated guided vehicles (AGVs). The study compares integrated and hierarchical approaches to coordinating production and transportation while minimizing makespan.

The associated manuscript is *Integrated or hierarchical? The value of joint production and AGV scheduling in flexible job shops with assembly precedence constraints*. It is currently under submission; no DOI or publication status is assigned here.

## Problem

An instance combines flexible machine assignment, operation sequencing, AGV assignment, loaded and unloaded transportation, and cross-job assembly precedence. A feasible schedule must respect machine capacity, vehicle capacity, transport timing, and the precedence graph. The objective is the completion time of the final assembly, `Cmax`.

## Algorithms

| Method | Description | Code |
|---|---|---|
| MILP | Mixed-integer linear formulation of the integrated problem | [`algorithms/milp/milp.py`](algorithms/milp/milp.py) |
| ICP | Integrated CP-SAT model for production and AGV scheduling | [`algorithms/integrated_cp/integrated_cp.py`](algorithms/integrated_cp/integrated_cp.py) |
| HCP | Two-stage CP-SAT approach: production decisions first, AGV scheduling with fixed machine assignment/order and operation retiming second | [`algorithms/hierarchical_cp/hierarchical_cp.py`](algorithms/hierarchical_cp/hierarchical_cp.py) |

The code includes purpose-specific large-scale solver versions and historical batch drivers. Archived outcomes are provided for analysis; the presence of a source file does not establish that it produced every archived run under identical settings.

## Repository structure

```text
algorithms/       MILP, ICP, HCP, and large-scale solver sources
instances/        Main, large-scale, sensitivity, and feature-analysis inputs
experiments/      Historical HCP sensitivity and Windows large-scale drivers
results/          Raw summaries, per-instance outputs, logs, and derived tables
scripts/          Read-only statistics checks and RD derivation
docs/             Input format, result format, usage, and data index
```

## Benchmark instances and experimental data

The 51 main instances cover Deroussi–Norre (10), Homayouni–Fontes (10), Chambers–Barnes (21), and Brandimarte (10). The repository also contains 60 larger instances, 574 sensitivity configurations drawn from 41 non-Brandimarte bases, and 240 feature-analysis variants. Inputs are under [`instances/`](instances/).

[`results/main_comparison/`](results/main_comparison/) contains archived MILP, ICP, and HCP outcomes for the 51-case comparison. [`results/large_scale/`](results/large_scale/) holds the 60-case records, referenced logs and available solution JSONs. Its original results remain in `raw/`; the Eq. (44) RD calculation using ICP as denominator is a separately labeled derivative in `derived/`.

[`results/sensitivity/`](results/sensitivity/) contains the **HCP results** and a [574-configuration index](results/sensitivity/configuration_index.csv) for AGV count, assembly topology, and transportation distance. These files do not by themselves reproduce an ICP–HCP paired sensitivity analysis. [`results/feature_analysis/`](results/feature_analysis/) includes 240-case solver summaries, six-feature statistics, a nine-feature candidate table, and historical repeated out-of-fold (OOF) model records. The OOF records are archived research data and should not be treated as a complete reproduction of the manuscript's model table. See the concise [data index](docs/EXPERIMENT_DATA.md).

## Requirements

- Python 3.12
- OR-Tools 9.15.6755 for CP-SAT
- Gurobi 12.0.3 and a valid Gurobi license for MILP
- NumPy for the feature-statistics check

See [`requirements.txt`](requirements.txt). The version numbers describe the recorded research environment; a solver run has not been executed as part of this GitHub preparation.

## Quick start

From the repository root, after installing the relevant solver dependency, run an archived small instance with ICP:

```bash
python algorithms/integrated_cp/integrated_cp.py \
  --input instances/main_51/Deroussi_Norre/DN_AFJSPT01_BT.json \
  --time-limit 30 --workers 1 \
  --details-dir local_runs/icp_small
```

The CLI arguments and input path were checked against the source. The solve itself has **not** been tested in this preparation environment, where OR-Tools and Gurobi are unavailable. Output goes to the ignored `local_runs/` directory. Further usage and analysis commands are in [`docs/USAGE.md`](docs/USAGE.md).

## Citation and use

If you use these materials, cite the associated manuscript by its title and authors as given in the manuscript until a formal publication citation is available. No open-source license has been assigned to this repository. Third-party benchmark reuse and redistribution should follow the permissions of the original data providers.
