import argparse
import contextlib
import csv
import importlib.util
import json
import math
import statistics
import sys
import time
import traceback
from copy import deepcopy
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_INPUT_DIR = REPO_ROOT / "instances/main_51"
DEFAULT_ASSEMBLY_TOPOLOGY_DIR = REPO_ROOT / "instances/assembly_topologies"
DEFAULT_HCP_SOLVER = REPO_ROOT / "algorithms/hierarchical_cp/hierarchical_cp.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "local_runs/sensitivity"

EXPECTED_BASE_COUNT = 41
EXPECTED_COUNTS = {
    "AGV_Number": 246,
    "Assembly_Topology": 123,
    "Transportation_Distance": 205,
}
EXPECTED_TOTAL_VARIANTS = 574

ANALYSIS_ORDER = ["AGV_Number", "Assembly_Topology", "Transportation_Distance"]
TOPOLOGY_ORDER = ["WBT", "BT", "DCT"]
TD_LEVELS = [
    (0.10, "L010", "0.10"),
    (0.50, "L050", "0.50"),
    (1.00, "L100", "1.00"),
    (1.50, "L150", "1.50"),
    (2.00, "L200", "2.00"),
]
AGV_LEVELS = [(r, f"R{r}", f"R={r}") for r in range(1, 7)]

DETECTION_FIELDS = [
    "Instance_Name",
    "Source_JSON_Path",
    "Detected_Family",
    "Detected_Scale",
    "Include_Flag",
    "Exclude_Reason",
    "Warning",
]

MANIFEST_FIELDS = [
    "Variant_ID",
    "Base_Instance_Name",
    "Family",
    "Scale",
    "Analysis_Type",
    "Parameter",
    "Parameter_Label",
    "Variant_JSON_Path",
    "Source_Base_JSON_Path",
    "Generation_Status",
    "Generation_Warning",
]

RESULT_FIELDS = [
    "Variant_ID",
    "Base_Instance_Name",
    "Family",
    "Scale",
    "Analysis_Type",
    "Parameter",
    "Parameter_Label",
    "Input_JSON_Path",
    "Method",
    "Upper_Status",
    "Upper_Cmax",
    "Upper_Runtime_s",
    "Upper_Time_Limit_s",
    "Lower_Status",
    "Lower_Cmax",
    "Lower_Runtime_s",
    "Lower_Time_Limit_s",
    "Status",
    "Cmax",
    "Objective_Value",
    "Best_Bound",
    "Optimality_Gap",
    "Total_Runtime_s",
    "Total_Time_Limit_s",
    "Output_Directory",
    "Error_Message",
]

SUMMARY_FIELDS = [
    "Variant_ID",
    "Base_Instance_Name",
    "Family",
    "Scale",
    "Analysis_Type",
    "Parameter",
    "Parameter_Label",
    "Input_JSON_Path",
    "Upper_Status",
    "Upper_Cmax",
    "Upper_Runtime_s",
    "Upper_Time_Limit_s",
    "Lower_Status",
    "Lower_Cmax",
    "Lower_Runtime_s",
    "Lower_Time_Limit_s",
    "Status",
    "Cmax",
    "Objective_Value",
    "Best_Bound",
    "Optimality_Gap",
    "Total_Runtime_s",
    "Total_Time_Limit_s",
    "Error_Message",
    "Result_JSON_Path",
    "Log_Path",
]

ELIGIBILITY_FIELDS = [
    "Base_Instance_Name",
    "Family",
    "Scale",
    "AGV_All_OPTIMAL",
    "Topology_All_OPTIMAL",
    "Distance_All_OPTIMAL",
    "AGV_N_OPTIMAL",
    "Topology_N_OPTIMAL",
    "Distance_N_OPTIMAL",
    "AGV_N_Total",
    "Topology_N_Total",
    "Distance_N_Total",
    "AGV_Max_Runtime",
    "Topology_Max_Runtime",
    "Distance_Max_Runtime",
    "AGV_Eligible_For_MainText_Figure",
    "Topology_Eligible_For_MainText_Figure",
    "Distance_Eligible_For_MainText_Figure",
]


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_csv_dicts(path):
    with Path(path).open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def append_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def output_paths(output_dir):
    root = Path(output_dir)
    return {
        "root": root,
        "generated_agv": root / "Generated_Instances" / "AGV_Number",
        "generated_topology": root / "Generated_Instances" / "Assembly_Topology",
        "generated_distance": root / "Generated_Instances" / "Transportation_Distance",
        "hcp_per_instance": root / "HCP" / "Per_Instance",
        "hcp_logs": root / "HCP" / "Logs",
        "hcp_summary": root / "HCP" / "Summary",
        "summary": root / "Summary",
        "combined_summary": root / "Combined_Summary",
        "logs": root / "Logs",
    }


def ensure_output_tree(output_dir):
    paths = output_paths(output_dir)
    for path in paths.values():
        if path != paths["root"]:
            path.mkdir(parents=True, exist_ok=True)
    return paths


def log_line(output_dir, message):
    append_text(output_paths(output_dir)["logs"] / "run_629_hcp_only.log", f"[{now_text()}] {message}\n")


def detect_family(path, data):
    path_text = str(path).replace("/", "\\")
    path_lower = path_text.lower()
    instance = str(data.get("Instance", Path(path).stem))
    name_upper = instance.upper()
    stem_upper = Path(path).stem.upper()

    if "brandimarte" in path_lower or name_upper.startswith(("B_MK", "MK")) or stem_upper.startswith(("B_MK", "MK")):
        return "Brandimarte"
    if "deroussi_norre" in path_lower or name_upper.startswith("DN_"):
        return "Deroussi_Norre"
    if "homayouni_fontes" in path_lower or name_upper.startswith("HF_"):
        return "Homayouni_Fontes"
    if "chambers_barnes" in path_lower or name_upper.startswith("CB_"):
        return "Chambers_Barnes"
    if "_MK" in name_upper or "_MK" in stem_upper:
        return "MK"
    return "Unknown"


def detect_scale(path, data):
    return str(data.get("Scale", "Unknown"))


def warning_for_instance(path, data):
    required = ["Instance", "N_Jobs", "M_Count", "AGV_Count", "Distance", "Proc_Data", "DAG", "Root_Jobs"]
    warnings = []
    for key in required:
        if key not in data:
            warnings.append(f"Missing {key}")
    if data.get("Instance") and data.get("Instance") != Path(path).stem:
        warnings.append("Instance field differs from filename stem")
    return "; ".join(warnings)


def detect_base_instances(base_input_dir, output_dir):
    paths = ensure_output_tree(output_dir)
    json_paths = sorted(Path(base_input_dir).rglob("*.json"))
    rows = []
    included = []
    seen_names = {}

    for json_path in json_paths:
        warning = ""
        try:
            data = read_json(json_path)
        except Exception as exc:
            data = {}
            warning = f"JSON read error: {exc}"

        instance_name = str(data.get("Instance", json_path.stem))
        family = detect_family(json_path, data)
        scale = detect_scale(json_path, data)
        include = family in {"Deroussi_Norre", "Homayouni_Fontes", "Chambers_Barnes"}
        exclude_reason = ""
        upper_name = instance_name.upper()
        upper_stem = json_path.stem.upper()

        if "brandimarte" in str(json_path).lower():
            include = False
            exclude_reason = "Path contains Brandimarte"
        elif upper_name.startswith(("B_MK", "MK")) or upper_stem.startswith(("B_MK", "MK")) or "_MK" in upper_name:
            include = False
            exclude_reason = "MK / Brandimarte instance"
        elif family not in {"Deroussi_Norre", "Homayouni_Fontes", "Chambers_Barnes"}:
            include = False
            exclude_reason = "Family not in non-MK include list"

        detected_warning = warning_for_instance(json_path, data) if data else warning
        if warning and detected_warning:
            detected_warning = f"{warning}; {detected_warning}"
        if instance_name in seen_names:
            detected_warning = (detected_warning + "; " if detected_warning else "") + f"Duplicate Instance name with {seen_names[instance_name]}"
        seen_names[instance_name] = str(json_path)

        row = {
            "Instance_Name": instance_name,
            "Source_JSON_Path": str(json_path),
            "Detected_Family": family,
            "Detected_Scale": scale,
            "Include_Flag": "YES" if include else "NO",
            "Exclude_Reason": exclude_reason,
            "Warning": detected_warning,
        }
        rows.append(row)
        if include:
            included.append(
                {
                    "Instance_Name": instance_name,
                    "Source_JSON_Path": str(json_path),
                    "Family": family,
                    "Scale": scale,
                    "N_Jobs": int(data.get("N_Jobs", len(data.get("Proc_Data", [])))),
                    "Total_Ops": int(data.get("Total_Ops", sum(len(job) for job in data.get("Proc_Data", [])))),
                }
            )

    report_csv = paths["summary"] / "Base_Instance_Detection_Report.csv"
    report_txt = paths["summary"] / "Base_Instance_Detection_Report.txt"
    write_csv(report_csv, rows, DETECTION_FIELDS)

    excluded = [r for r in rows if r["Include_Flag"] != "YES"]
    lines = [
        "Base Instance Detection Report",
        f"Generated at: {now_text()}",
        f"Base input directory: {Path(base_input_dir)}",
        f"Number of original JSON files found: {len(rows)}",
        f"Number of non-MK base instances included: {len(included)}",
        f"Number of excluded MK / Brandimarte instances: {len(excluded)}",
        "",
        "Included base instances:",
    ]
    for rec in sorted(included, key=lambda x: x["Instance_Name"]):
        lines.append(f"- {rec['Instance_Name']} | {rec['Family']} | {rec['Scale']} | J={rec['N_Jobs']} | {rec['Source_JSON_Path']}")
    if len(included) != EXPECTED_BASE_COUNT:
        lines.extend(["", f"STOP: expected {EXPECTED_BASE_COUNT} non-MK base instances, found {len(included)}."])
    report_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log_line(output_dir, f"Detected {len(rows)} JSON files, included {len(included)} non-MK base instances.")

    if len(included) != EXPECTED_BASE_COUNT:
        raise RuntimeError(f"Expected {EXPECTED_BASE_COUNT} non-MK base instances, found {len(included)}. Reports written to {report_csv} and {report_txt}.")

    return sorted(included, key=lambda x: x["Instance_Name"]), rows


def parse_topology_file(path, n_jobs):
    text = Path(path).read_text(encoding="utf-8")
    in_tree = False
    dag = {str(j): [] for j in range(1, int(n_jobs) + 1)}
    for raw in text.splitlines():
        line = raw.strip()
        if line == "### ASSEMBLY_TREE ###":
            in_tree = True
            continue
        if not in_tree or not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        child, parent = int(parts[0]), int(parts[1])
        if not (1 <= child <= n_jobs and 1 <= parent <= n_jobs):
            raise ValueError(f"Topology edge out of range in {path}: {line}")
        dag[str(parent)].append(child)
    for preds in dag.values():
        preds.sort()
    predecessor_jobs = {pred for preds in dag.values() for pred in preds}
    root_jobs = [job for job in range(1, int(n_jobs) + 1) if job not in predecessor_jobs]
    if not root_jobs:
        raise ValueError(f"No root job detected in topology file {path}")
    edge_count = sum(len(v) for v in dag.values())
    if edge_count != int(n_jobs) - 1:
        raise ValueError(f"Unexpected edge count in {path}: expected {int(n_jobs) - 1}, found {edge_count}")
    return dag, root_jobs


def topology_file_for(assembly_topology_dir, n_jobs, topology):
    return Path(assembly_topology_dir) / f"J={int(n_jobs)}" / f"J={int(n_jobs)}_{topology}.txt"


def validate_topologies(base_instances, assembly_topology_dir, output_dir):
    missing = []
    errors = []
    for n_jobs in sorted({int(b["N_Jobs"]) for b in base_instances}):
        for topology in TOPOLOGY_ORDER:
            path = topology_file_for(assembly_topology_dir, n_jobs, topology)
            if not path.exists():
                missing.append(f"Missing topology file for J={n_jobs}, {topology}: {path}")
                continue
            try:
                parse_topology_file(path, n_jobs)
            except Exception as exc:
                errors.append(f"Invalid topology file for J={n_jobs}, {topology}: {path} | {exc}")
    if missing or errors:
        report = output_paths(output_dir)["logs"] / "Topology_Matching_Error_Report.txt"
        report.write_text("\n".join(["Topology matching failed.", *missing, *errors]) + "\n", encoding="utf-8")
        raise RuntimeError(f"Topology matching failed. See {report}")


def add_variant_metadata(data, variant_id, base_name, analysis_type, parameter, parameter_label, source_path):
    data["Variant_ID"] = variant_id
    data["Base_Instance_Name"] = base_name
    data["Analysis_Type"] = analysis_type
    data["Parameter"] = str(parameter)
    data["Parameter_Label"] = str(parameter_label)
    data["Source_Base_JSON_Path"] = str(source_path)
    return data


def scale_distance_matrix(distance, factor):
    if abs(float(factor) - 1.0) < 1e-12:
        return deepcopy(distance)
    scaled = {}
    for src, row in distance.items():
        scaled[src] = {}
        for dst, value in row.items():
            product = float(value) * float(factor)
            rounded = round(product, 10)
            if abs(rounded - round(rounded)) < 1e-10:
                scaled[src][dst] = int(round(rounded))
            else:
                scaled[src][dst] = rounded
    return scaled


def manifest_sort_key(row):
    analysis = row.get("Analysis_Type", "")
    analysis_rank = ANALYSIS_ORDER.index(analysis) if analysis in ANALYSIS_ORDER else 99
    base = row.get("Base_Instance_Name", "")
    param = row.get("Parameter", "")
    if analysis == "AGV_Number":
        try:
            p_rank = int(float(param))
        except Exception:
            p_rank = 99
    elif analysis == "Assembly_Topology":
        p_rank = TOPOLOGY_ORDER.index(str(param)) if str(param) in TOPOLOGY_ORDER else 99
    elif analysis == "Transportation_Distance":
        td_order = [label for _, _, label in TD_LEVELS]
        p_rank = td_order.index(str(param)) if str(param) in td_order else 99
    else:
        p_rank = 99
    return analysis_rank, base, p_rank


def generate_variants(base_input_dir, assembly_topology_dir, output_dir):
    paths = ensure_output_tree(output_dir)
    base_instances, detection_rows = detect_base_instances(base_input_dir, output_dir)
    validate_topologies(base_instances, assembly_topology_dir, output_dir)

    manifest_rows = []
    for base in base_instances:
        base_path = Path(base["Source_JSON_Path"])
        base_name = base["Instance_Name"]
        family = base["Family"]
        scale = base["Scale"]
        n_jobs = int(base["N_Jobs"])
        source_data = read_json(base_path)

        for agv_count, suffix, label in AGV_LEVELS:
            variant_id = f"{base_name}__AGV_{suffix}"
            variant_data = deepcopy(source_data)
            variant_data["AGV_Count"] = int(agv_count)
            add_variant_metadata(variant_data, variant_id, base_name, "AGV_Number", agv_count, label, base_path)
            variant_path = paths["generated_agv"] / f"{variant_id}.json"
            write_json(variant_path, variant_data)
            manifest_rows.append(
                {
                    "Variant_ID": variant_id,
                    "Base_Instance_Name": base_name,
                    "Family": family,
                    "Scale": scale,
                    "Analysis_Type": "AGV_Number",
                    "Parameter": str(agv_count),
                    "Parameter_Label": label,
                    "Variant_JSON_Path": str(variant_path),
                    "Source_Base_JSON_Path": str(base_path),
                    "Generation_Status": "OK",
                    "Generation_Warning": "",
                }
            )

        for topology in TOPOLOGY_ORDER:
            variant_id = f"{base_name}__TOPO_{topology}"
            topo_path = topology_file_for(assembly_topology_dir, n_jobs, topology)
            dag, root_jobs = parse_topology_file(topo_path, n_jobs)
            variant_data = deepcopy(source_data)
            variant_data["DAG"] = dag
            variant_data["Root_Jobs"] = root_jobs
            add_variant_metadata(variant_data, variant_id, base_name, "Assembly_Topology", topology, topology, base_path)
            variant_path = paths["generated_topology"] / f"{variant_id}.json"
            write_json(variant_path, variant_data)
            manifest_rows.append(
                {
                    "Variant_ID": variant_id,
                    "Base_Instance_Name": base_name,
                    "Family": family,
                    "Scale": scale,
                    "Analysis_Type": "Assembly_Topology",
                    "Parameter": topology,
                    "Parameter_Label": topology,
                    "Variant_JSON_Path": str(variant_path),
                    "Source_Base_JSON_Path": str(base_path),
                    "Generation_Status": "OK",
                    "Generation_Warning": f"Topology source: {topo_path}",
                }
            )

        for factor, suffix, label in TD_LEVELS:
            variant_id = f"{base_name}__TD_{suffix}"
            variant_data = deepcopy(source_data)
            variant_data["Distance"] = scale_distance_matrix(source_data["Distance"], factor)
            add_variant_metadata(variant_data, variant_id, base_name, "Transportation_Distance", label, label, base_path)
            variant_path = paths["generated_distance"] / f"{variant_id}.json"
            write_json(variant_path, variant_data)
            manifest_rows.append(
                {
                    "Variant_ID": variant_id,
                    "Base_Instance_Name": base_name,
                    "Family": family,
                    "Scale": scale,
                    "Analysis_Type": "Transportation_Distance",
                    "Parameter": label,
                    "Parameter_Label": label,
                    "Variant_JSON_Path": str(variant_path),
                    "Source_Base_JSON_Path": str(base_path),
                    "Generation_Status": "OK",
                    "Generation_Warning": "",
                }
            )

    manifest_rows = sorted(manifest_rows, key=manifest_sort_key)
    counts = {analysis: sum(1 for r in manifest_rows if r["Analysis_Type"] == analysis) for analysis in ANALYSIS_ORDER}
    total = len(manifest_rows)
    manifest_path = paths["summary"] / "Sensitivity_Variant_Manifest.csv"
    write_csv(manifest_path, manifest_rows, MANIFEST_FIELDS)

    count_lines = [
        "Sensitivity Variant Generation Check",
        f"Generated at: {now_text()}",
        f"AGV_Number: {counts['AGV_Number']} / expected {EXPECTED_COUNTS['AGV_Number']}",
        f"Assembly_Topology: {counts['Assembly_Topology']} / expected {EXPECTED_COUNTS['Assembly_Topology']}",
        f"Transportation_Distance: {counts['Transportation_Distance']} / expected {EXPECTED_COUNTS['Transportation_Distance']}",
        f"Total: {total} / expected {EXPECTED_TOTAL_VARIANTS}",
    ]
    (paths["summary"] / "Sensitivity_Variant_Generation_Check.txt").write_text("\n".join(count_lines) + "\n", encoding="utf-8")

    if counts != EXPECTED_COUNTS or total != EXPECTED_TOTAL_VARIANTS:
        raise RuntimeError(f"Variant count mismatch. Manifest written to {manifest_path}; HCP solve is stopped.")

    log_line(output_dir, f"Generated variants: AGV={counts['AGV_Number']}, Topology={counts['Assembly_Topology']}, Distance={counts['Transportation_Distance']}, Total={total}.")
    return manifest_rows, detection_rows


def manifest_path(output_dir):
    return output_paths(output_dir)["summary"] / "Sensitivity_Variant_Manifest.csv"


def load_manifest(output_dir):
    path = manifest_path(output_dir)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}. Run --mode generate first.")
    rows = read_csv_dicts(path)
    return sorted(rows, key=manifest_sort_key)


def load_hcp_solver(hcp_solver):
    solver_path = Path(hcp_solver)
    spec = importlib.util.spec_from_file_location("hcp_solver_module_629", solver_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["hcp_solver_module_629"] = module
    spec.loader.exec_module(module)
    return module


def safe_build_phase2_agv_fixed_order(solver_module, data, phase1, time_limit, workers, log=False, phase2_mode="retime"):
    """Compatibility copy of solver phase 2 with identical constraints.

    OR-Tools 9.15 can exit at native level when the original code repeatedly
    reads fixed IntVar domains through .Proto().domain inside the pairwise AGV
    loop. Store those fixed origin/destination indices as Python constants
    instead. This does not change variables, constraints, objective, or solve
    parameters.
    """
    if phase2_mode not in ("retime", "strict"):
        raise ValueError("phase2_mode must be 'retime' or 'strict'.")

    cp_model = solver_module.cp_model
    model = cp_model.CpModel()
    machines = [f"M{i}" for i in range(1, int(data["M_Count"]) + 1)]
    locs = [solver_module.LOC_WH] + machines
    loc_to_i = {name: i for i, name in enumerate(locs)}
    i_to_loc = {i: name for name, i in loc_to_i.items()}
    horizon = solver_module.simple_horizon(data)

    op_start = {}
    op_end = {}
    fixed_machine = phase1["machine_of"]

    for j, job in enumerate(data["Proc_Data"], start=1):
        for o, _ in enumerate(job, start=1):
            start_var = model.NewIntVar(0, horizon, f"p2_s_j{j}_o{o}")
            end_var = model.NewIntVar(0, horizon, f"p2_e_j{j}_o{o}")
            op_start[j, o] = start_var
            op_end[j, o] = end_var
            model.Add(end_var == start_var + phase1["proc_scaled"][j, o])
            if phase2_mode == "strict":
                model.Add(start_var == phase1["op_start"][j, o])
                model.Add(end_var == phase1["op_end"][j, o])

    for machine in machines:
        order = phase1["machine_order"].get(machine, [])
        for prev_op, next_op in zip(order, order[1:]):
            model.Add(op_start[next_op] >= op_end[prev_op])

    tasks = solver_module.build_tasks(data)
    task_start = []
    task_end = []
    task_origin = []
    task_dest = []
    task_origin_i = []
    task_dest_i = []
    task_dur = []
    task_has_agv = []
    task_y = []

    for task in tasks:
        if task["source_fixed"] is not None:
            src_name = task["source_fixed"]
        else:
            src_name = fixed_machine[task["source_op"]]

        if task["dest_fixed"] is not None:
            dst_name = task["dest_fixed"]
        else:
            dst_name = fixed_machine[task["dest_op"]]

        src_i = loc_to_i[src_name]
        dst_i = loc_to_i[dst_name]
        loaded_dur = solver_module.scaled_time(data["Distance"][src_name][dst_name], data["Speeds"]["S_L"])
        has_agv_const = 1 if src_i != dst_i else 0

        start_var = model.NewIntVar(0, horizon, f"p2_ts_{task['name']}")
        end_var = model.NewIntVar(0, horizon, f"p2_te_{task['name']}")
        origin = model.NewIntVar(src_i, src_i, f"p2_orig_{task['name']}")
        dest = model.NewIntVar(dst_i, dst_i, f"p2_dest_{task['name']}")
        dur = model.NewIntVar(loaded_dur, loaded_dur, f"p2_dur_{task['name']}")
        has_agv = model.NewBoolVar(f"p2_has_agv_{task['name']}")
        model.Add(has_agv == has_agv_const)
        model.Add(end_var == start_var + dur)

        if task["source_op"] is not None:
            model.Add(start_var >= op_end[task["source_op"]])
        if task["dest_op"] is not None:
            model.Add(op_start[task["dest_op"]] >= end_var)

        y_vars = []
        for a in range(int(data["AGV_Count"])):
            y = model.NewBoolVar(f"p2_y_{task['name']}_a{a + 1}")
            y_vars.append(y)
        model.Add(sum(y_vars) == has_agv)

        init_gap = solver_module.scaled_time(data["Distance"][solver_module.LOC_WH][src_name], data["Speeds"]["S_U"])
        if has_agv_const == 1:
            model.Add(start_var >= init_gap)

        task_start.append(start_var)
        task_end.append(end_var)
        task_origin.append(origin)
        task_dest.append(dest)
        task_origin_i.append(src_i)
        task_dest_i.append(dst_i)
        task_dur.append(dur)
        task_has_agv.append(has_agv)
        task_y.append(y_vars)

    for i in range(len(tasks)):
        src_i = locs[task_origin_i[i]]
        dst_i = locs[task_dest_i[i]]
        for j in range(i + 1, len(tasks)):
            src_j = locs[task_origin_i[j]]
            dst_j = locs[task_dest_i[j]]
            gap_ij = solver_module.scaled_time(data["Distance"][dst_i][src_j], data["Speeds"]["S_U"])
            gap_ji = solver_module.scaled_time(data["Distance"][dst_j][src_i], data["Speeds"]["S_U"])
            for a in range(int(data["AGV_Count"])):
                before = model.NewBoolVar(f"p2_before_t{i}_t{j}_a{a + 1}")
                model.Add(task_start[j] >= task_end[i] + gap_ij).OnlyEnforceIf([task_y[i][a], task_y[j][a], before])
                model.Add(task_start[i] >= task_end[j] + gap_ji).OnlyEnforceIf([task_y[i][a], task_y[j][a], before.Not()])

    cmax = model.NewIntVar(0, horizon, "P2_C_max")
    root_return_tasks = [i for i, task in enumerate(tasks) if task["name"].startswith("out_")]
    if root_return_tasks:
        model.AddMaxEquality(cmax, [task_end[i] for i in root_return_tasks])
    else:
        root_ends = []
        for root_job in solver_module.root_jobs_from_data(data):
            root_last = len(data["Proc_Data"][root_job - 1])
            root_ends.append(op_end[root_job, root_last])
        model.AddMaxEquality(cmax, root_ends)
    model.Minimize(cmax)

    solver = cp_model.CpSolver()
    solver_module.set_solver_params(solver, time_limit, workers, log=log)
    status = solver.Solve(model)
    proto = model.Proto()

    return {
        "model": model,
        "solver": solver,
        "status": status,
        "status_name": solver.StatusName(status),
        "proto": proto,
        "machines": machines,
        "locs": locs,
        "i_to_loc": i_to_loc,
        "tasks": tasks,
        "op_start": op_start,
        "op_end": op_end,
        "task_start": task_start,
        "task_end": task_end,
        "task_origin": task_origin,
        "task_dest": task_dest,
        "task_dur": task_dur,
        "task_has_agv": task_has_agv,
        "task_y": task_y,
        "cmax": cmax,
        "phase2_mode": phase2_mode,
    }


def numeric(value):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    except Exception:
        return None


def valid_hcp_result(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = read_json(path)
    except Exception:
        return False
    if not data.get("Status"):
        return False
    if numeric(data.get("Total_Runtime_s")) is None:
        return False
    if data.get("Cmax") in (None, "") and data.get("Objective_Value") in (None, ""):
        return False
    return True


def result_dir_for(output_dir, variant_id):
    return output_paths(output_dir)["hcp_per_instance"] / variant_id


def standard_result_from_manifest(row, output_dir, upper_limit, total_limit):
    result_dir = result_dir_for(output_dir, row["Variant_ID"])
    return {
        "Variant_ID": row["Variant_ID"],
        "Base_Instance_Name": row["Base_Instance_Name"],
        "Family": row["Family"],
        "Scale": row["Scale"],
        "Analysis_Type": row["Analysis_Type"],
        "Parameter": row["Parameter"],
        "Parameter_Label": row["Parameter_Label"],
        "Input_JSON_Path": row["Variant_JSON_Path"],
        "Method": "HCP",
        "Upper_Status": "",
        "Upper_Cmax": "",
        "Upper_Runtime_s": "",
        "Upper_Time_Limit_s": float(upper_limit),
        "Lower_Status": "",
        "Lower_Cmax": "",
        "Lower_Runtime_s": "",
        "Lower_Time_Limit_s": "",
        "Status": "",
        "Cmax": "",
        "Objective_Value": "",
        "Best_Bound": "",
        "Optimality_Gap": "",
        "Total_Runtime_s": "",
        "Total_Time_Limit_s": float(total_limit),
        "Output_Directory": str(result_dir),
        "Error_Message": "",
    }


def write_input_pointer(result_dir, input_json_path):
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "input_path.txt").write_text(str(input_json_path) + "\n", encoding="utf-8")


def solve_variant(row, hcp_solver_module, output_dir, upper_limit, total_limit, force=False):
    variant_id = row["Variant_ID"]
    result_dir = result_dir_for(output_dir, variant_id)
    result_path = result_dir / "hcp_result.json"
    upper_log_path = result_dir / "hcp_upper_log.txt"
    lower_log_path = result_dir / "hcp_lower_log.txt"
    full_log_path = result_dir / "hcp_log.txt"
    input_json_path = Path(row["Variant_JSON_Path"])

    if not force and valid_hcp_result(result_path):
        log_line(output_dir, f"SKIP valid existing result: {variant_id}")
        return "skipped", read_json(result_path)

    result_dir.mkdir(parents=True, exist_ok=True)
    write_input_pointer(result_dir, input_json_path)
    result = standard_result_from_manifest(row, output_dir, upper_limit, total_limit)
    raw_result = None
    start = time.time()
    command_used = (
        f"IN_PROCESS_API {Path(row['Variant_JSON_Path'])} | "
        f"build_phase1_afjsp(time_limit={upper_limit}); "
        f"build_phase2_agv_fixed_order(time_limit=max(1,{total_limit}-Upper_Runtime_s))"
    )

    append_text(full_log_path, f"[{now_text()}] START {variant_id}\nCommand used: {command_used}\n")
    log_line(output_dir, f"START {variant_id} | {row['Analysis_Type']} | {row['Parameter']} | method=HCP")

    try:
        data = read_json(input_json_path)
        with upper_log_path.open("w", encoding="utf-8") as upper_log, contextlib.redirect_stdout(upper_log), contextlib.redirect_stderr(upper_log):
            print(f"[{now_text()}] Upper phase start")
            print(f"Input JSON: {input_json_path}")
            print(f"Upper time limit: {upper_limit}")
            phase1_ctx = hcp_solver_module.build_phase1_afjsp(data, time_limit=float(upper_limit), workers=8, log=False)
            phase1 = hcp_solver_module.extract_phase1_solution(data, phase1_ctx)
            print(f"[{now_text()}] Upper phase end")

        upper_runtime = round(float(phase1_ctx["solver"].WallTime()), 6)
        upper_status = phase1["solve_status"] if phase1 else str(phase1_ctx.get("status_name", "UNKNOWN"))
        upper_cmax = phase1["cmax"] if phase1 else ""
        lower_limit = round(max(1.0, float(total_limit) - float(upper_runtime)), 6)

        result.update(
            {
                "Upper_Status": upper_status,
                "Upper_Cmax": upper_cmax,
                "Upper_Runtime_s": upper_runtime,
                "Upper_Time_Limit_s": float(upper_limit),
                "Lower_Time_Limit_s": lower_limit,
            }
        )

        if phase1 is None:
            result.update(
                {
                    "Lower_Status": "NOT_RUN",
                    "Lower_Runtime_s": 0.0,
                    "Status": upper_status,
                    "Total_Runtime_s": upper_runtime,
                    "Error_Message": "Upper phase did not produce a feasible solution.",
                }
            )
        else:
            with lower_log_path.open("w", encoding="utf-8") as lower_log, contextlib.redirect_stdout(lower_log), contextlib.redirect_stderr(lower_log):
                print(f"[{now_text()}] Lower phase start")
                print(f"Input JSON: {input_json_path}")
                print(f"Lower time limit: {lower_limit}")
                phase2_ctx = safe_build_phase2_agv_fixed_order(
                    hcp_solver_module,
                    data,
                    phase1,
                    time_limit=float(lower_limit),
                    workers=8,
                    log=False,
                    phase2_mode="retime",
                )
                raw_result = hcp_solver_module.extract_results(input_json_path, data, phase1_ctx, phase1, phase2_ctx)
                print(f"[{now_text()}] Lower phase end")

            lower_runtime = round(float(phase2_ctx["solver"].WallTime()), 6)
            lower_status = raw_result["Meta_Data"]["Solve_Status"] if raw_result else str(phase2_ctx.get("status_name", "UNKNOWN"))
            total_runtime = round(float(upper_runtime) + float(lower_runtime), 6)
            if raw_result:
                result.update(
                    {
                        "Lower_Status": lower_status,
                        "Lower_Cmax": raw_result["Metrics"].get("Objective_Cmax", ""),
                        "Lower_Runtime_s": lower_runtime,
                        "Status": raw_result["Meta_Data"].get("Solve_Status", lower_status),
                        "Cmax": raw_result["Metrics"].get("Objective_Cmax", ""),
                        "Objective_Value": raw_result["Metrics"].get("Objective_Cmax", ""),
                        "Best_Bound": raw_result["Metrics"].get("Best_Bound", ""),
                        "Optimality_Gap": raw_result["Metrics"].get("MIP_Gap", ""),
                        "Total_Runtime_s": total_runtime,
                    }
                )
                write_json(result_dir / "hcp_raw_solver_result.json", raw_result)
            else:
                result.update(
                    {
                        "Lower_Status": lower_status,
                        "Lower_Runtime_s": lower_runtime,
                        "Status": lower_status,
                        "Total_Runtime_s": total_runtime,
                        "Error_Message": "Lower phase did not produce a feasible solution.",
                    }
                )

    except Exception as exc:
        elapsed = round(time.time() - start, 6)
        result.update(
            {
                "Status": "ERROR",
                "Total_Runtime_s": elapsed,
                "Error_Message": f"{type(exc).__name__}: {exc}",
            }
        )
        append_text(full_log_path, traceback.format_exc() + "\n")

    write_json(result_path, result)
    end = time.time()
    append_text(
        full_log_path,
        (
            f"[{now_text()}] END {variant_id}\n"
            f"Upper_Status: {result.get('Upper_Status')}\n"
            f"Lower_Status: {result.get('Lower_Status')}\n"
            f"Final_Status: {result.get('Status')}\n"
            f"Upper_Runtime_s: {result.get('Upper_Runtime_s')}\n"
            f"Lower_Runtime_s: {result.get('Lower_Runtime_s')}\n"
            f"Total_Runtime_s: {result.get('Total_Runtime_s')}\n"
            f"Wall_elapsed_s: {round(end - start, 6)}\n"
            f"Error_Message: {result.get('Error_Message')}\n\n"
        ),
    )
    log_line(
        output_dir,
        (
            f"END {variant_id} | upper={result.get('Upper_Status')} | lower={result.get('Lower_Status')} | "
            f"status={result.get('Status')} | upper_rt={result.get('Upper_Runtime_s')} | "
            f"lower_rt={result.get('Lower_Runtime_s')} | total_rt={result.get('Total_Runtime_s')} | "
            f"error={result.get('Error_Message')}"
        ),
    )
    return "solved", result


def row_complexity(row):
    try:
        data = read_json(row["Source_Base_JSON_Path"])
        n_jobs = int(data.get("N_Jobs", 9999))
        total_ops = int(data.get("Total_Ops", sum(len(job) for job in data.get("Proc_Data", []))))
    except Exception:
        n_jobs = 9999
        total_ops = 999999
    small_rank = 0 if "small" in str(row.get("Scale", "")).lower() else 1
    return small_rank, n_jobs, total_ops, row.get("Base_Instance_Name", "")


def select_smoke_rows(manifest_rows):
    by_base = {}
    for row in manifest_rows:
        by_base.setdefault(row["Base_Instance_Name"], []).append(row)
    base_order = sorted(by_base, key=lambda b: row_complexity(by_base[b][0]))
    for base in base_order:
        rows = by_base[base]
        chosen = []
        for analysis in ANALYSIS_ORDER:
            candidates = sorted([r for r in rows if r["Analysis_Type"] == analysis], key=manifest_sort_key)
            if candidates:
                chosen.append(candidates[0])
        if len(chosen) == 3:
            return chosen
    raise RuntimeError("Could not select one smoke-test variant from each analysis type.")


def run_smoke_test(output_dir, hcp_solver, upper_limit, total_limit):
    ensure_output_tree(output_dir)
    rows = load_manifest(output_dir)
    hcp_solver_module = load_hcp_solver(hcp_solver)
    smoke_rows = select_smoke_rows(rows)
    report_path = output_paths(output_dir)["logs"] / "Smoke_Test_Report.txt"
    report_lines = [
        "Smoke Test Report",
        f"Generated at: {now_text()}",
        f"Upper time limit: {upper_limit}",
        f"Total time limit: {total_limit}",
        "",
    ]

    required = [
        "Upper_Status",
        "Upper_Cmax",
        "Upper_Runtime_s",
        "Upper_Time_Limit_s",
        "Lower_Status",
        "Lower_Cmax",
        "Lower_Runtime_s",
        "Lower_Time_Limit_s",
        "Status",
        "Cmax",
        "Objective_Value",
        "Best_Bound",
        "Optimality_Gap",
        "Total_Runtime_s",
        "Total_Time_Limit_s",
    ]
    ok = True
    for row in smoke_rows:
        state, result = solve_variant(row, hcp_solver_module, output_dir, upper_limit, total_limit, force=False)
        missing = [field for field in required if field not in result or result.get(field) in (None, "")]
        result_ok = not missing and result.get("Status") in {"OPTIMAL", "FEASIBLE"}
        ok = ok and result_ok
        report_lines.extend(
            [
                f"Variant_ID: {row['Variant_ID']}",
                f"Analysis_Type: {row['Analysis_Type']}",
                f"Parameter: {row['Parameter']}",
                f"Run_State: {state}",
                f"Status: {result.get('Status')}",
                f"Upper_Status: {result.get('Upper_Status')}",
                f"Lower_Status: {result.get('Lower_Status')}",
                f"Upper_Runtime_s: {result.get('Upper_Runtime_s')}",
                f"Lower_Time_Limit_s: {result.get('Lower_Time_Limit_s')}",
                f"Total_Runtime_s: {result.get('Total_Runtime_s')}",
                f"Missing_or_blank_required_fields: {', '.join(missing) if missing else 'NONE'}",
                "",
            ]
        )
    report_lines.append(f"Smoke_Test_Status: {'PASS' if ok else 'FAIL'}")
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    log_line(output_dir, f"Smoke test {'PASS' if ok else 'FAIL'} for {[r['Variant_ID'] for r in smoke_rows]}")
    if not ok:
        raise RuntimeError(f"Smoke test failed. See {report_path}")
    return smoke_rows


def run_hcp_batch(output_dir, hcp_solver, upper_limit, total_limit):
    ensure_output_tree(output_dir)
    rows = load_manifest(output_dir)
    if len(rows) != EXPECTED_TOTAL_VARIANTS:
        raise RuntimeError(f"Manifest has {len(rows)} rows, expected {EXPECTED_TOTAL_VARIANTS}; HCP batch stopped.")
    counts = {analysis: sum(1 for r in rows if r["Analysis_Type"] == analysis) for analysis in ANALYSIS_ORDER}
    if counts != EXPECTED_COUNTS:
        raise RuntimeError(f"Manifest counts mismatch: {counts}; HCP batch stopped.")

    hcp_solver_module = load_hcp_solver(hcp_solver)
    stats = {
        "started_at": now_text(),
        "solved_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "total_variants": len(rows),
        "remaining_count": 0,
        "last_variant": "",
    }

    for idx, row in enumerate(rows, start=1):
        variant_id = row["Variant_ID"]
        log_line(
            output_dir,
            f"Variant index {idx}/{len(rows)} | variant id={variant_id} | base={row['Base_Instance_Name']} | analysis={row['Analysis_Type']} | parameter={row['Parameter']} | method=HCP",
        )
        state, result = solve_variant(row, hcp_solver_module, output_dir, upper_limit, total_limit, force=False)
        if state == "skipped":
            stats["skipped_count"] += 1
        else:
            stats["solved_count"] += 1
        if result.get("Status") == "ERROR" or result.get("Error_Message"):
            stats["error_count"] += 1
        stats["last_variant"] = variant_id
        stats_path = output_paths(output_dir)["combined_summary"] / "HCP_Batch_Run_Stats.json"
        write_json(stats_path, stats)

    stats["finished_at"] = now_text()
    summary_rows = summarize_hcp(output_dir)
    stats["remaining_count"] = sum(1 for r in summary_rows if r.get("Status") in ("", "MISSING"))
    write_json(output_paths(output_dir)["combined_summary"] / "HCP_Batch_Run_Stats.json", stats)
    return stats


def summary_sort_key(row):
    return manifest_sort_key(row)


def summary_row_from_result(manifest_row, result, result_path, log_path):
    row = {field: "" for field in SUMMARY_FIELDS}
    for field in [
        "Variant_ID",
        "Base_Instance_Name",
        "Family",
        "Scale",
        "Analysis_Type",
        "Parameter",
        "Parameter_Label",
    ]:
        row[field] = manifest_row.get(field, "")
    row["Input_JSON_Path"] = manifest_row.get("Variant_JSON_Path", "")
    for field in [
        "Upper_Status",
        "Upper_Cmax",
        "Upper_Runtime_s",
        "Upper_Time_Limit_s",
        "Lower_Status",
        "Lower_Cmax",
        "Lower_Runtime_s",
        "Lower_Time_Limit_s",
        "Status",
        "Cmax",
        "Objective_Value",
        "Best_Bound",
        "Optimality_Gap",
        "Total_Runtime_s",
        "Total_Time_Limit_s",
        "Error_Message",
    ]:
        row[field] = result.get(field, "")
    row["Result_JSON_Path"] = str(result_path)
    row["Log_Path"] = str(log_path)
    return row


def summarize_hcp(output_dir):
    ensure_output_tree(output_dir)
    rows = load_manifest(output_dir)
    summary_rows = []
    for manifest_row in rows:
        variant_id = manifest_row["Variant_ID"]
        result_dir = result_dir_for(output_dir, variant_id)
        result_path = result_dir / "hcp_result.json"
        log_path = result_dir / "hcp_log.txt"
        if result_path.exists():
            try:
                result = read_json(result_path)
            except Exception as exc:
                result = standard_result_from_manifest(manifest_row, output_dir, 900, 3600)
                result["Status"] = "ERROR"
                result["Error_Message"] = f"Unreadable result JSON: {exc}"
        else:
            result = standard_result_from_manifest(manifest_row, output_dir, 900, 3600)
            result["Status"] = "MISSING"
            result["Error_Message"] = "hcp_result.json not found"
        summary_rows.append(summary_row_from_result(manifest_row, result, result_path, log_path))

    summary_rows = sorted(summary_rows, key=summary_sort_key)
    hcp_summary_path = output_paths(output_dir)["hcp_summary"] / "HCP_Sensitivity_629_Summary.csv"
    write_csv(hcp_summary_path, summary_rows, SUMMARY_FIELDS)
    write_csv(output_paths(output_dir)["combined_summary"] / "HCP_All_574_Variants.csv", summary_rows, SUMMARY_FIELDS)

    write_eligibility_tables(output_dir, summary_rows)
    write_batch_report(output_dir, summary_rows)
    log_line(output_dir, f"Summarized HCP results to {hcp_summary_path}")
    return summary_rows


def max_numeric(values):
    nums = [numeric(v) for v in values]
    nums = [v for v in nums if v is not None]
    return max(nums) if nums else ""


def write_eligibility_tables(output_dir, summary_rows):
    by_base = {}
    for row in summary_rows:
        by_base.setdefault(row["Base_Instance_Name"], []).append(row)

    eligibility_rows = []
    for base in sorted(by_base):
        rows = by_base[base]
        first = rows[0]
        agv_rows = [r for r in rows if r["Analysis_Type"] == "AGV_Number"]
        topo_rows = [r for r in rows if r["Analysis_Type"] == "Assembly_Topology"]
        dist_rows = [r for r in rows if r["Analysis_Type"] == "Transportation_Distance"]

        def counts(part_rows, expected):
            n_total = len(part_rows)
            n_opt = sum(1 for r in part_rows if r.get("Status") == "OPTIMAL")
            all_opt = n_total == expected and n_opt == expected
            return all_opt, n_opt, n_total, max_numeric(r.get("Total_Runtime_s") for r in part_rows)

        agv_all, agv_opt, agv_total, agv_max = counts(agv_rows, 6)
        topo_all, topo_opt, topo_total, topo_max = counts(topo_rows, 3)
        dist_all, dist_opt, dist_total, dist_max = counts(dist_rows, 5)
        eligibility_rows.append(
            {
                "Base_Instance_Name": base,
                "Family": first["Family"],
                "Scale": first["Scale"],
                "AGV_All_OPTIMAL": "YES" if agv_all else "NO",
                "Topology_All_OPTIMAL": "YES" if topo_all else "NO",
                "Distance_All_OPTIMAL": "YES" if dist_all else "NO",
                "AGV_N_OPTIMAL": agv_opt,
                "Topology_N_OPTIMAL": topo_opt,
                "Distance_N_OPTIMAL": dist_opt,
                "AGV_N_Total": agv_total,
                "Topology_N_Total": topo_total,
                "Distance_N_Total": dist_total,
                "AGV_Max_Runtime": agv_max,
                "Topology_Max_Runtime": topo_max,
                "Distance_Max_Runtime": dist_max,
                "AGV_Eligible_For_MainText_Figure": "YES" if agv_all else "NO",
                "Topology_Eligible_For_MainText_Figure": "YES" if topo_all else "NO",
                "Distance_Eligible_For_MainText_Figure": "YES" if dist_all else "NO",
            }
        )

    combined = output_paths(output_dir)["combined_summary"]
    write_csv(combined / "HCP_AllOptimal_BaseInstances_By_Analysis.csv", eligibility_rows, ELIGIBILITY_FIELDS)
    for analysis, file_name in [
        ("AGV_Number", "AGV_Number_HCP_OPTIMAL_Only.csv"),
        ("Assembly_Topology", "Assembly_Topology_HCP_OPTIMAL_Only.csv"),
        ("Transportation_Distance", "Transportation_Distance_HCP_OPTIMAL_Only.csv"),
    ]:
        opt_rows = [r for r in summary_rows if r["Analysis_Type"] == analysis and r.get("Status") == "OPTIMAL"]
        write_csv(combined / file_name, opt_rows, SUMMARY_FIELDS)


def safe_stats(values):
    nums = [numeric(v) for v in values]
    nums = [v for v in nums if v is not None]
    if not nums:
        return "", "", ""
    return round(statistics.mean(nums), 6), round(statistics.median(nums), 6), round(max(nums), 6)


def read_detection_summary(output_dir):
    path = output_paths(output_dir)["summary"] / "Base_Instance_Detection_Report.csv"
    if not path.exists():
        return [], 0, 0, 0
    rows = read_csv_dicts(path)
    included = sum(1 for r in rows if r.get("Include_Flag") == "YES")
    excluded = len(rows) - included
    return rows, len(rows), included, excluded


def write_batch_report(output_dir, summary_rows):
    paths = output_paths(output_dir)
    detection_rows, original_count, included_count, excluded_count = read_detection_summary(output_dir)
    manifest_rows = load_manifest(output_dir)
    generated_counts = {analysis: sum(1 for r in manifest_rows if r["Analysis_Type"] == analysis) for analysis in ANALYSIS_ORDER}
    generated_total = len(manifest_rows)

    stats_path = paths["combined_summary"] / "HCP_Batch_Run_Stats.json"
    run_stats = {}
    if stats_path.exists():
        try:
            run_stats = read_json(stats_path)
        except Exception:
            run_stats = {}

    status_counts = {}
    for row in summary_rows:
        status = row.get("Status") or "MISSING"
        status_counts[status] = status_counts.get(status, 0) + 1
    by_analysis_status = {}
    for row in summary_rows:
        key = (row["Analysis_Type"], row.get("Status") or "MISSING")
        by_analysis_status[key] = by_analysis_status.get(key, 0) + 1

    hcp_completed = sum(1 for r in summary_rows if r.get("Status") not in ("", "MISSING"))
    hcp_remaining = sum(1 for r in summary_rows if r.get("Status") in ("", "MISSING"))
    hcp_error = sum(
        1
        for r in summary_rows
        if r.get("Status") == "ERROR"
        or (r.get("Error_Message") and r.get("Status") not in {"MISSING", "FEASIBLE", "OPTIMAL"})
    )

    total_mean, total_median, total_max = safe_stats(r.get("Total_Runtime_s") for r in summary_rows)
    upper_mean, upper_median, upper_max = safe_stats(r.get("Upper_Runtime_s") for r in summary_rows)
    lower_mean, lower_median, lower_max = safe_stats(r.get("Lower_Runtime_s") for r in summary_rows)

    eligibility_path = paths["combined_summary"] / "HCP_AllOptimal_BaseInstances_By_Analysis.csv"
    eligibility_rows = read_csv_dicts(eligibility_path) if eligibility_path.exists() else []
    agv_eligible = sum(1 for r in eligibility_rows if r.get("AGV_Eligible_For_MainText_Figure") == "YES")
    topo_eligible = sum(1 for r in eligibility_rows if r.get("Topology_Eligible_For_MainText_Figure") == "YES")
    dist_eligible = sum(1 for r in eligibility_rows if r.get("Distance_Eligible_For_MainText_Figure") == "YES")

    feasible_only = [r["Variant_ID"] for r in summary_rows if r.get("Status") == "FEASIBLE"]
    timeout = [r["Variant_ID"] for r in summary_rows if r.get("Status") == "UNKNOWN" or "time" in str(r.get("Error_Message", "")).lower()]
    errors = [
        r["Variant_ID"]
        for r in summary_rows
        if r.get("Status") == "ERROR"
        or (r.get("Error_Message") and r.get("Status") not in {"MISSING", "FEASIBLE", "OPTIMAL"})
    ]

    recommendation = (
        "YES: all 574 HCP variants have completed with OPTIMAL/FEASIBLE results and can be matched with ICP by Variant_ID."
        if hcp_remaining == 0 and hcp_error == 0
        else "NO / PARTIAL: HCP results are not complete enough for final ICP-HCP matching; resolve missing/error/timeout variants first."
    )

    lines = [
        "HCP Batch Report 629",
        f"Generated at: {now_text()}",
        "",
        f"Base input directory: {DEFAULT_BASE_INPUT_DIR}",
        f"Assembly topology directory: {DEFAULT_ASSEMBLY_TOPOLOGY_DIR}",
        f"Output root: {paths['root']}",
        "",
        f"Number of original JSON files found: {original_count}",
        f"Number of non-MK base instances included: {included_count}",
        f"Number of excluded MK / Brandimarte instances: {excluded_count}",
        "",
        f"Expected total variants: {EXPECTED_TOTAL_VARIANTS}",
        f"Generated AGV variants: {generated_counts.get('AGV_Number', 0)}",
        f"Generated topology variants: {generated_counts.get('Assembly_Topology', 0)}",
        f"Generated distance variants: {generated_counts.get('Transportation_Distance', 0)}",
        f"Generated total variants: {generated_total}",
        "",
        f"HCP solved count: {run_stats.get('solved_count', hcp_completed)}",
        f"HCP skipped count: {run_stats.get('skipped_count', 0)}",
        f"HCP remaining count: {hcp_remaining}",
        f"HCP error count: {hcp_error}",
        "",
        f"HCP OPTIMAL count: {status_counts.get('OPTIMAL', 0)}",
        f"HCP FEASIBLE count: {status_counts.get('FEASIBLE', 0)}",
        f"HCP INFEASIBLE count: {status_counts.get('INFEASIBLE', 0)}",
        f"HCP UNKNOWN count: {status_counts.get('UNKNOWN', 0)}",
        "",
        "HCP OPTIMAL count by Analysis_Type:",
    ]
    for analysis in ANALYSIS_ORDER:
        lines.append(f"- {analysis}: {by_analysis_status.get((analysis, 'OPTIMAL'), 0)}")
    lines.append("")
    lines.append("HCP FEASIBLE count by Analysis_Type:")
    for analysis in ANALYSIS_ORDER:
        lines.append(f"- {analysis}: {by_analysis_status.get((analysis, 'FEASIBLE'), 0)}")
    lines.extend(
        [
            "",
            f"Mean HCP total runtime: {total_mean}",
            f"Median HCP total runtime: {total_median}",
            f"Max HCP total runtime: {total_max}",
            "",
            f"Mean upper runtime: {upper_mean}",
            f"Median upper runtime: {upper_median}",
            f"Max upper runtime: {upper_max}",
            "",
            f"Mean lower runtime: {lower_mean}",
            f"Median lower runtime: {lower_median}",
            f"Max lower runtime: {lower_max}",
            "",
            f"AGV all-optimal eligible base instances: {agv_eligible}",
            f"Topology all-optimal eligible base instances: {topo_eligible}",
            f"Distance all-optimal eligible base instances: {dist_eligible}",
            "",
            "Feasible-only variants list:",
            *(f"- {x}" for x in feasible_only),
            "",
            "Timeout variants list:",
            *(f"- {x}" for x in timeout),
            "",
            "Error variants list:",
            *(f"- {x}" for x in errors),
            "",
            "Recommendation:",
            recommendation,
        ]
    )
    (paths["combined_summary"] / "HCP_Batch_Report_629.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_mode(args):
    ensure_output_tree(args.output_dir)
    mode = args.mode
    if mode == "generate":
        generate_variants(args.base_input_dir, args.assembly_topology_dir, args.output_dir)
    elif mode == "smoke_test":
        if not manifest_path(args.output_dir).exists():
            generate_variants(args.base_input_dir, args.assembly_topology_dir, args.output_dir)
        run_smoke_test(args.output_dir, args.hcp_solver, args.hcp_upper_time_limit, args.total_time_limit)
        summarize_hcp(args.output_dir)
    elif mode == "hcp":
        run_hcp_batch(args.output_dir, args.hcp_solver, args.hcp_upper_time_limit, args.total_time_limit)
    elif mode == "summarize_hcp":
        summarize_hcp(args.output_dir)
    elif mode == "all":
        generate_variants(args.base_input_dir, args.assembly_topology_dir, args.output_dir)
        run_smoke_test(args.output_dir, args.hcp_solver, args.hcp_upper_time_limit, args.total_time_limit)
        run_hcp_batch(args.output_dir, args.hcp_solver, args.hcp_upper_time_limit, args.total_time_limit)
        summarize_hcp(args.output_dir)
    else:
        raise ValueError(f"Unsupported mode: {mode}")


def parse_args():
    parser = argparse.ArgumentParser(description="629 sensitivity experiment HCP-only batch runner.")
    parser.add_argument("--base_input_dir", type=Path, default=DEFAULT_BASE_INPUT_DIR)
    parser.add_argument("--assembly_topology_dir", type=Path, default=DEFAULT_ASSEMBLY_TOPOLOGY_DIR)
    parser.add_argument("--hcp_solver", type=Path, default=DEFAULT_HCP_SOLVER)
    parser.add_argument("--output_dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--hcp_upper_time_limit", type=float, default=900)
    parser.add_argument("--total_time_limit", type=float, default=3600)
    parser.add_argument("--mode", choices=["generate", "smoke_test", "hcp", "summarize_hcp", "all"], default="hcp")
    return parser.parse_args()


def main():
    args = parse_args()
    log_line(args.output_dir, f"run_629_sensitivity_hcp_only.py start | mode={args.mode}")
    try:
        run_mode(args)
    except Exception as exc:
        log_line(args.output_dir, f"FATAL | {type(exc).__name__}: {exc}")
        raise
    finally:
        log_line(args.output_dir, f"run_629_sensitivity_hcp_only.py end | mode={args.mode}")


if __name__ == "__main__":
    main()
