# Experimental data index

| Study | Inputs | Code or analysis | Archived results |
|---|---|---|---|
| Main 51, MILP/ICP/HCP | [`instances/main_51/`](../instances/main_51/) | [`algorithms/`](../algorithms/) | [`results/main_comparison/`](../results/main_comparison/) |
| Large-scale 60 | [`instances/large_scale_60/`](../instances/large_scale_60/) | Windows batch source in [`experiments/large_scale/`](../experiments/large_scale/); Eq. (44) derivative in `scripts/` | [`results/large_scale/`](../results/large_scale/) |
| Three sensitivity dimensions on 41 bases | [`instances/sensitivity/`](../instances/sensitivity/) | [`experiments/sensitivity/run_hcp.py`](../experiments/sensitivity/run_hcp.py) | [`results/sensitivity/`](../results/sensitivity/): HCP records and 574-row index |
| Feature analysis, 240 variants | [`instances/feature_analysis_240/`](../instances/feature_analysis_240/) | Statistics checks in [`scripts/`](../scripts/) | [`results/feature_analysis/`](../results/feature_analysis/): core data, solver records, historical OOF tables |

Raw archived outputs and later arithmetic derivatives are separated by directory. For field definitions and the RD formula, see [`RESULT_FORMAT.md`](RESULT_FORMAT.md).
