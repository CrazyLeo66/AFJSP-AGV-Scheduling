# Instance format

Inputs are UTF-8 JSON. The principal fields are `Instance`, `Scale`, `N_Jobs`, `M_Count`, `Total_Ops`, `AGV_Count`, `Speeds`, `Distance`, `Proc_Data`, `DAG`, and `Root_Jobs`.

- `Proc_Data` is a list of jobs, each containing ordered operations. Each operation maps eligible machine IDs (for example `M1`) to processing times.
- `DAG` maps a job ID to its predecessor jobs and expresses assembly precedence. `Root_Jobs` identifies final assembly jobs.
- `Distance` maps origins to destinations, including machine locations and the loading/unloading location `L/U`. `Speeds` records loaded and unloaded AGV speeds as `S_L` and `S_U`.
- Final sensitivity inputs add `Variant_ID`, `Base_Instance_Name`, `Analysis_Type`, and `Parameter`. Provenance path strings in those files have been made nonpersonal in this publication copy; use the [configuration index](../results/sensitivity/configuration_index.csv) to locate actual repository files.

The main 51, large-scale 60, sensitivity 574, and feature-analysis 240 JSON sets are separate cohorts. Similar instance names do not imply interchangeable results.
