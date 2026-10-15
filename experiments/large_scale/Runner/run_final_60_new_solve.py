import argparse
import csv
import importlib.util
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from ctypes import Structure, byref, c_size_t, sizeof, windll
from ctypes import wintypes
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
EXP_ROOT = BASE_DIR.parent
REPO_ROOT = EXP_ROOT.parent.parent
PYTHON_EXE = Path(sys.executable).resolve()
INSTANCES_DIR = REPO_ROOT / "instances/large_scale_60"
ICP_SOLVER = REPO_ROOT / "algorithms/large_scale/integrated_solver_seed1.py"
HCP_SOLVER = REPO_ROOT / "algorithms/large_scale/hierarchical_solver_seed1.py"
RESULTS_DIR = REPO_ROOT / "local_runs/large_scale/results"
SMOKE_DIR = REPO_ROOT / "local_runs/large_scale/smoke"
NEW_SOLVE_DIR = REPO_ROOT / "local_runs/large_scale/solve"
WORK_DIR = REPO_ROOT / "local_runs/large_scale/runtime"

SCALES = list(range(220, 401, 20))


def _read_manifest_rows():
    path = RESULTS_DIR / "final_60_manifest.csv"
    if not path.exists():
        path = EXP_ROOT / "Generation" / "final_60_manifest.csv"
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return sorted(rows, key=lambda r: (int(r["Operations"]), int(r["Pair_Index"]), 0 if r["Topology"] == "BT" else 1))


MANIFEST_ROWS = _read_manifest_rows()
INSTANCES = [row["Instance"] for row in MANIFEST_ROWS]
INSTANCE_SEEDS = {row["Instance"]: row["Seed"] for row in MANIFEST_ROWS}
INSTANCE_BASES = sorted({row["Base_Instance"] for row in MANIFEST_ROWS}, key=lambda b: (int(b.split("_")[0][2:]), int(b.split("_")[1])))

TOTAL_METHOD_RUNS = len(INSTANCES) * 2
WORKERS = 8
RANDOM_SEED = 1
ORTOOLS_VERSION = "9.15.6755"
ICP_TIME_LIMIT = 3600.0
HCP_UPPER_TIME_LIMIT = 900.0
HARD_TIMEOUT = 4500.0
SMOKE_TIME_LIMIT = 30.0
SMOKE_HARD_TIMEOUT = 600.0


RUN_LEVEL_FIELDS = [
    "Instance", "Base_Instance", "Topology", "Seed", "Operations", "Jobs", "Machines", "AGVs", "Method", "Random_Seed",
    "Solve_Status", "Raw_CP_Status", "Cmax", "Best_Bound", "Gap_Percent",
    "Solver_Time_Limit_s", "Actual_Solver_WallTime_s", "End_to_End_Time_s",
    "Num_Search_Workers", "Branches", "Conflicts", "Peak_Memory_MB",
    "Child_Process_Exit_Code", "Feasibility_Check_Passed", "Feasibility_Check_Message",
    "Solution_File", "Log_File", "Error_Message",
    "Upper_Status", "Upper_Cmax", "Upper_Best_Bound", "Upper_Gap_Percent",
    "Upper_Time_Limit_s", "Upper_Actual_Time_s", "Lower_Status", "Lower_Cmax",
    "Lower_Best_Bound", "Lower_Gap_Percent", "Lower_Time_Limit_s",
    "Lower_Actual_Time_s", "HCP_Total_Solver_Time_s", "Result_Source",
    "Was_Rerun", "Original_Status", "Original_Error", "Rerun_Reason",
]

PAIRED_FIELDS = [
    "Instance", "Base_Instance", "Topology", "Seed", "Jobs", "Operations", "Machines", "AGVs",
    "HCP_Status", "HCP_Cmax", "HCP_Upper_Status", "HCP_Lower_Status", "HCP_Upper_Time_s", "HCP_Lower_Time_s",
    "HCP_Total_Time_s", "HCP_Peak_Memory_MB", "HCP_Feasibility_Passed",
    "ICP_Status", "ICP_Cmax", "ICP_Best_Bound", "ICP_Gap_Percent",
    "ICP_Time_s", "ICP_Peak_Memory_MB", "ICP_Feasibility_Passed", "ICP_Failure_Type",
    "ICP_Feasible_Not_Optimal", "HCP_Result_Source", "ICP_Result_Source",
    "Delta_Cmax", "RD_Percent", "Comparison_Category", "Comparison_Valid", "Note",
]

HCP_BETTER_FIELDS = [
    "Instance", "Base_Instance", "Topology", "Operations", "Jobs", "Machines", "AGVs",
    "ICP_Status", "ICP_Cmax", "ICP_Best_Bound", "ICP_Gap_Percent",
    "ICP_Time_s", "HCP_Status", "HCP_Cmax", "HCP_Time_s", "Delta_Cmax", "RD_Percent",
]

HCP_ONLY_FIELDS = [
    "Instance", "Topology", "Operations", "Jobs", "Machines", "AGVs",
    "HCP_Status", "HCP_Cmax", "HCP_Time_s", "ICP_Status", "ICP_Failure_Type",
    "ICP_Best_Bound", "ICP_Time_s", "ICP_Peak_Memory_MB",
]

ICP_FEASIBLE_NOT_OPTIMAL_FIELDS = [
    "Instance", "Base_Instance", "Topology", "Operations", "Jobs", "Machines", "AGVs",
    "ICP_Status", "ICP_Cmax", "ICP_Best_Bound", "ICP_Gap_Percent", "ICP_Time_s",
    "ICP_Peak_Memory_MB", "HCP_Status", "HCP_Cmax", "HCP_Time_s",
    "Delta_Cmax", "RD_Percent", "Comparison_Category",
]

TOPOLOGY_PAIRED_FIELDS = [
    "Base_Instance", "Operations", "Jobs", "Machines", "AGVs",
    "BT_ICP_Status", "BT_ICP_Cmax", "BT_ICP_Best_Bound", "BT_ICP_Gap",
    "BT_HCP_Status", "BT_HCP_Cmax", "BT_Delta", "BT_RD", "BT_Category",
    "DCT_ICP_Status", "DCT_ICP_Cmax", "DCT_ICP_Best_Bound", "DCT_ICP_Gap",
    "DCT_HCP_Status", "DCT_HCP_Cmax", "DCT_Delta", "DCT_RD", "DCT_Category",
    "DCT_More_Favorable_To_HCP",
]

SCALE_TOPOLOGY_SUMMARY_FIELDS = [
    "Scale", "Topology", "Number_of_Instances",
    "ICP_Optimal_Count", "ICP_Feasible_Not_Optimal_Count", "ICP_No_Feasible_Count",
    "HCP_Feasible_Count", "HCP_No_Feasible_Count",
    "HCP_BETTER_Count", "HCP_ONLY_FEASIBLE_Count", "ICP_BETTER_Count",
    "EQUAL_Count", "ICP_ONLY_FEASIBLE_Count", "NO_FEASIBLE_Count",
]

PROGRESS_FIELDS = [
    "Timestamp", "Background_PID", "Current_Instance", "Current_Method", "Current_Status",
    "Completed_Method_Runs", "Total_Method_Runs", "Completed_Pairs",
    "Total_Pairs", "HCP_Better_Count", "HCP_Only_Feasible_Count",
    "ICP_Feasible_Not_Optimal_Count", "ICP_Better_Count", "Last_Completed_Instance",
    "Last_Error",
]

RETRY_PROGRESS_FIELDS = PROGRESS_FIELDS + ["Completed_Retry_Method_Runs", "Total_Retry_Method_Runs"]


class PROCESS_MEMORY_COUNTERS(Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", c_size_t),
        ("WorkingSetSize", c_size_t),
        ("QuotaPeakPagedPoolUsage", c_size_t),
        ("QuotaPagedPoolUsage", c_size_t),
        ("QuotaPeakNonPagedPoolUsage", c_size_t),
        ("QuotaNonPagedPoolUsage", c_size_t),
        ("PagefileUsage", c_size_t),
        ("PeakPagefileUsage", c_size_t),
    ]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def ensure_dirs():
    for p in [
        EXP_ROOT,
        RESULTS_DIR,
        NEW_SOLVE_DIR / "HCP" / "Solutions",
        NEW_SOLVE_DIR / "HCP" / "Logs",
        NEW_SOLVE_DIR / "ICP" / "Solutions",
        NEW_SOLVE_DIR / "ICP" / "Logs",
        SMOKE_DIR,
        SMOKE_DIR / "HCP" / "Solutions",
        SMOKE_DIR / "HCP" / "Logs",
        SMOKE_DIR / "ICP" / "Solutions",
        SMOKE_DIR / "ICP" / "Logs",
        WORK_DIR,
    ]:
        p.mkdir(parents=True, exist_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
        f.flush()
        os.fsync(f.fileno())


def load_solve_plan():
    path = RESULTS_DIR / "solve_plan.csv"
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    order = {instance: i for i, instance in enumerate(INSTANCES)}
    method_order = {"HCP": 0, "ICP": 1}
    return sorted(rows, key=lambda r: (order.get(r.get("Instance"), 9999), method_order.get(r.get("Method"), 9)))


def bool_text(value):
    return str(value).strip().lower() == "true"


def row_has_environment_error(row):
    text = " ".join(str(row.get(field, "")) for field in ["Error_Message", "Feasibility_Check_Message"])
    text = text.lower()
    return "no module named 'ortools'" in text or ("modulenotfounderror" in text and "ortools" in text)


def target_run_plan():
    return [row for row in load_solve_plan() if bool_text(row.get("Need_Run"))]


def load_environment_retry_plan():
    path = RESULTS_DIR / "environment_failure_audit.csv"
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    order = {instance: i for i, instance in enumerate(INSTANCES)}
    method_order = {"HCP": 0, "ICP": 1}
    rows = [row for row in rows if bool_text(row.get("Need_Rerun"))]
    return sorted(rows, key=lambda r: (order.get(r.get("Instance"), 9999), method_order.get(r.get("Method"), 9)))


def append_log(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
        if text and not text.endswith("\n"):
            f.write("\n")
        f.flush()


def ensure_ortools_available(log_file=None):
    check = subprocess.run(
        [str(PYTHON_EXE), "-c", "import ortools; from ortools.sat.python import cp_model; print(ortools.__version__)"],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0 and check.stdout.strip().splitlines()[-1].strip() == ORTOOLS_VERSION:
        return
    if log_file:
        append_log(log_file, f"[{now_iso()}] OR-Tools import/version check failed; reinstalling ortools=={ORTOOLS_VERSION}.")
        append_log(log_file, check.stdout)
        append_log(log_file, check.stderr)
    install = subprocess.run(
        [str(PYTHON_EXE), "-m", "pip", "install", f"ortools=={ORTOOLS_VERSION}"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if log_file:
        append_log(log_file, install.stdout)
        append_log(log_file, install.stderr)
    if install.returncode != 0:
        raise RuntimeError(f"Unable to ensure OR-Tools {ORTOOLS_VERSION}: {install.stderr or install.stdout}")
    verify = subprocess.run(
        [str(PYTHON_EXE), "-c", "import ortools; from ortools.sat.python import cp_model; print(ortools.__version__)"],
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0 or verify.stdout.strip().splitlines()[-1].strip() != ORTOOLS_VERSION:
        raise RuntimeError(f"OR-Tools verification failed after install: {verify.stderr or verify.stdout}")


def status_name(code):
    try:
        from ortools.sat.python import cp_model
        names = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "MODEL_INVALID",
            cp_model.UNKNOWN: "UNKNOWN",
        }
        return names.get(code, str(code))
    except Exception:
        return str(code)


def gap_percent(gap):
    if gap in ("", None):
        return ""
    try:
        return round(float(gap) * 100.0, 6)
    except Exception:
        return ""


def load_solver_copy(src_path, module_name):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    dst = WORK_DIR / f"{module_name}.py"
    text = Path(src_path).read_text(encoding="utf-8")
    text = text.replace("solver.parameters.random_seed = 7", f"solver.parameters.random_seed = {RANDOM_SEED}")
    if "hierarchical" in module_name:
        text = text.replace(
            "    task_y = []\n\n    for task in tasks:",
            "    task_y = []\n    task_origin_names = []\n    task_dest_names = []\n\n    for task in tasks:",
        )
        text = text.replace(
            "        task_y.append(y_vars)\n\n    # Pairwise sequencing constraints on each AGV.",
            "        task_y.append(y_vars)\n        task_origin_names.append(src_name)\n        task_dest_names.append(dst_name)\n\n    # Pairwise sequencing constraints on each AGV.",
        )
        text = text.replace("        src_i = locs[task_origin[i].Proto().domain[0]]", "        src_i = task_origin_names[i]")
        text = text.replace("        dst_i = locs[task_dest[i].Proto().domain[0]]", "        dst_i = task_dest_names[i]")
        text = text.replace("            src_j = locs[task_origin[j].Proto().domain[0]]", "            src_j = task_origin_names[j]")
        text = text.replace("            dst_j = locs[task_dest[j].Proto().domain[0]]", "            dst_j = task_dest_names[j]")
    dst.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(module_name, dst)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def peak_memory_mb(pid):
    if os.name != "nt":
        return ""
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    handle = windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, int(pid))
    if not handle:
        return ""
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = sizeof(PROCESS_MEMORY_COUNTERS)
        ok = windll.psapi.GetProcessMemoryInfo(handle, byref(counters), counters.cb)
        if not ok:
            return ""
        return round(float(counters.PeakWorkingSetSize) / (1024.0 * 1024.0), 3)
    finally:
        windll.kernel32.CloseHandle(handle)


def kill_process_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


def instance_path(instance):
    path = INSTANCES_DIR / f"{instance}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def dimensions(data):
    instance = data.get("Instance", "")
    parts = instance.split("_")
    base = "_".join(parts[:2]) if len(parts) >= 2 else instance
    topology = parts[2] if len(parts) >= 3 else ""
    return {
        "Base_Instance": base,
        "Topology": topology,
        "Seed": INSTANCE_SEEDS.get(instance, ""),
        "Operations": data.get("Total_Ops", sum(len(j) for j in data.get("Proc_Data", []))),
        "Jobs": data.get("N_Jobs", len(data.get("Proc_Data", []))),
        "Machines": data.get("M_Count", ""),
        "AGVs": data.get("AGV_Count", ""),
    }


def parse_op(name):
    if not isinstance(name, str) or not name.startswith("J") or "_O" not in name:
        return None
    left, right = name[1:].split("_O", 1)
    return int(left), int(right)


def op_name(op):
    return f"J{op[0]}_O{op[1]}"


def normalize_dag(data):
    n = int(data.get("N_Jobs", len(data["Proc_Data"])))
    raw = data.get("DAG", {})
    return {j: [int(v) for v in raw.get(str(j), raw.get(j, []))] for j in range(1, n + 1)}


def root_jobs(data):
    if data.get("Root_Jobs"):
        return [int(j) for j in data["Root_Jobs"]]
    dag = normalize_dag(data)
    preds = set()
    for vals in dag.values():
        preds.update(vals)
    return [j for j in dag if j not in preds]


def source_jobs(data):
    return [j for j, preds in normalize_dag(data).items() if not preds]


def transport_task_map(schedule):
    return {row.get("Task"): row for row in schedule}


def loc_in(row, key):
    val = row.get(key)
    return "L/U" if val == "W" else val


def nearly(a, b, tol=1e-5):
    return abs(float(a) - float(b)) <= tol


def check_feasibility(data, result):
    if not result:
        return False, "No solution JSON was produced."
    try:
        ops = [r for r in result.get("Operations_Schedule", []) if parse_op(r.get("Operation"))]
        agv = result.get("AGV_Schedule", [])
        expected_ops = int(data.get("Total_Ops", sum(len(j) for j in data.get("Proc_Data", []))))
        if len(ops) != expected_ops:
            return False, f"Operation count mismatch: {len(ops)} != {expected_ops}."

        by_op = {}
        by_machine = {}
        for row in ops:
            op = parse_op(row["Operation"])
            by_op[op] = row
            by_machine.setdefault(row["Machine"], []).append(row)
            candidates = data["Proc_Data"][op[0] - 1][op[1] - 1]
            if row["Machine"] not in candidates:
                return False, f"{row['Operation']} uses illegal machine {row['Machine']}."
            if not nearly(float(row["End"]) - float(row["Start"]), float(candidates[row["Machine"]])):
                return False, f"{row['Operation']} processing time mismatch."

        for machine, rows in by_machine.items():
            rows = sorted(rows, key=lambda r: (float(r["Start"]), float(r["End"]), r["Operation"]))
            for a, b in zip(rows, rows[1:]):
                if float(a["End"]) > float(b["Start"]) + 1e-5:
                    return False, f"Machine overlap on {machine}: {a['Operation']} and {b['Operation']}."

        tasks = transport_task_map(agv)

        def check_transport(task, src_op, dst_op, src_fixed=None, dst_fixed=None):
            row = tasks.get(task)
            if row is None:
                if src_op and dst_op:
                    return float(by_op[src_op]["End"]) <= float(by_op[dst_op]["Start"]) + 1e-5
                return True
            if src_op:
                src_row = by_op[src_op]
                if float(row["PickUp_Time"]) + 1e-5 < float(src_row["End"]):
                    return False
                if loc_in(row, "From_Machine") != src_row["Machine"]:
                    return False
            elif src_fixed and loc_in(row, "From_Machine") != src_fixed:
                return False
            if dst_op:
                dst_row = by_op[dst_op]
                if float(row["DropOff_Time"]) > float(dst_row["Start"]) + 1e-5:
                    return False
                if loc_in(row, "To_Machine") != dst_row["Machine"]:
                    return False
            elif dst_fixed and loc_in(row, "To_Machine") != dst_fixed:
                return False
            src = loc_in(row, "From_Machine")
            dst = loc_in(row, "To_Machine")
            loaded = float(data["Distance"][src][dst]) / float(data["Speeds"]["S_L"])
            if not nearly(float(row["DropOff_Time"]) - float(row["PickUp_Time"]), loaded):
                return False
            return True

        for j, job in enumerate(data["Proc_Data"], start=1):
            if j in source_jobs(data):
                ok = check_transport(f"Start -> J{j}_O1", None, (j, 1), src_fixed="L/U")
                if ok is not True:
                    return False, f"Invalid source transport for J{j}_O1."
            for o in range(2, len(job) + 1):
                ok = check_transport(f"J{j}_O{o - 1} -> J{j}_O{o}", (j, o - 1), (j, o))
                if ok is not True:
                    return False, f"Invalid intra-job transport J{j}_O{o - 1}->J{j}_O{o}."

        for child, preds in normalize_dag(data).items():
            for pred in preds:
                pred_last = len(data["Proc_Data"][pred - 1])
                ok = check_transport(f"J{pred}_O{pred_last} -> J{child}_O1", (pred, pred_last), (child, 1))
                if ok is not True:
                    return False, f"Invalid assembly transport J{pred}->J{child}."

        for root in root_jobs(data):
            last = len(data["Proc_Data"][root - 1])
            ok = check_transport(f"J{root}_O{last} -> End", (root, last), None, dst_fixed="L/U")
            if ok is not True:
                return False, f"Invalid final return for J{root}."

        by_agv = {}
        for row in agv:
            by_agv.setdefault(row["AGV"], []).append(row)
        for agv_name, rows in by_agv.items():
            rows = sorted(rows, key=lambda r: (float(r["PickUp_Time"]), float(r["DropOff_Time"]), r["Task"]))
            prev_drop = 0.0
            prev_to = "L/U"
            for row in rows:
                curr_from = loc_in(row, "From_Machine")
                empty = float(data["Distance"][prev_to][curr_from]) / float(data["Speeds"]["S_U"])
                if float(row["PickUp_Time"]) + 1e-5 < prev_drop + empty:
                    return False, f"AGV overlap/reposition violation on {agv_name}."
                prev_drop = float(row["DropOff_Time"])
                prev_to = loc_in(row, "To_Machine")

        cmax = float(result["Metrics"]["Objective_Cmax"])
        max_end = max(float(r["End"]) for r in ops)
        max_drop = max([float(r["DropOff_Time"]) for r in agv] or [0.0])
        if cmax + 1e-5 < max(max_end, max_drop):
            return False, "Reported Cmax is earlier than schedule completion."
        return True, "PASS"
    except Exception as exc:
        return False, f"Checker error: {exc}"


def solve_child(method, instance, output_dir, time_limit=None):
    ensure_dirs()
    data_path = instance_path(instance)
    data = read_json(data_path)
    output_dir = Path(output_dir)
    sol_path = output_dir / method / "Solutions" / f"{instance}_{method}_solution.json"
    started = time.perf_counter()

    if method == "ICP":
        mod = load_solver_copy(ICP_SOLVER, "integrated_solver_seed1")
        ctx = mod.build_and_solve(data, time_limit=float(time_limit or ICP_TIME_LIMIT), workers=WORKERS, log=False)
        result = mod.extract_results(data_path, data, ctx)
        raw_status = ctx.get("status_name", status_name(ctx.get("status")))
        solver = ctx.get("solver")
        proto = ctx.get("proto")
        if result:
            write_json(sol_path, result)
            feasible, message = check_feasibility(data, result)
            meta = result["Meta_Data"]
            metrics = result["Metrics"]
            row = {
                **dimensions(data),
                "Instance": instance,
                "Method": method,
                "Random_Seed": RANDOM_SEED,
                "Solve_Status": meta.get("Solve_Status"),
                "Raw_CP_Status": meta.get("Raw_CP_Status", raw_status),
                "Cmax": metrics.get("Objective_Cmax"),
                "Best_Bound": metrics.get("Best_Bound"),
                "Gap_Percent": gap_percent(metrics.get("MIP_Gap")),
                "Solver_Time_Limit_s": float(time_limit or ICP_TIME_LIMIT),
                "Actual_Solver_WallTime_s": metrics.get("Wall_Clock_Time_s"),
                "End_to_End_Time_s": round(time.perf_counter() - started, 6),
                "Num_Search_Workers": meta.get("CP_WORKERS"),
                "Branches": meta.get("Branches"),
                "Conflicts": meta.get("Conflicts"),
                "Feasibility_Check_Passed": feasible,
                "Feasibility_Check_Message": message,
                "Solution_File": str(sol_path),
            }
        else:
            row = {
                **dimensions(data),
                "Instance": instance,
                "Method": method,
                "Random_Seed": RANDOM_SEED,
                "Solve_Status": raw_status,
                "Raw_CP_Status": raw_status,
                "Cmax": "",
                "Best_Bound": round(float(solver.BestObjectiveBound()) / float(mod.TIME_SCALE), 6) if solver else "",
                "Gap_Percent": "",
                "Solver_Time_Limit_s": float(time_limit or ICP_TIME_LIMIT),
                "Actual_Solver_WallTime_s": round(solver.WallTime(), 6) if solver else "",
                "End_to_End_Time_s": round(time.perf_counter() - started, 6),
                "Num_Search_Workers": WORKERS,
                "Branches": solver.NumBranches() if solver else "",
                "Conflicts": solver.NumConflicts() if solver else "",
                "Feasibility_Check_Passed": False,
                "Feasibility_Check_Message": "No FEASIBLE/OPTIMAL complete solution.",
                "Solution_File": "",
            }
    else:
        mod = load_solver_copy(HCP_SOLVER, "hierarchical_solver_seed1")
        upper_time = float(time_limit or HCP_UPPER_TIME_LIMIT)
        p1_ctx = mod.build_phase1_afjsp(data, time_limit=upper_time, workers=WORKERS, log=False)
        p1 = mod.extract_phase1_solution(data, p1_ctx)
        upper_actual = round(p1_ctx["solver"].WallTime(), 6)
        lower_time = max(0.0, float(ICP_TIME_LIMIT if time_limit is None else time_limit * 4.0) - upper_actual)
        if time_limit is not None:
            lower_time = max(0.0, float(time_limit) - upper_actual)
        if p1 is None:
            raw_status = p1_ctx.get("status_name", status_name(p1_ctx.get("status")))
            row = {
                **dimensions(data),
                "Instance": instance,
                "Method": method,
                "Random_Seed": RANDOM_SEED,
                "Solve_Status": raw_status,
                "Raw_CP_Status": raw_status,
                "Cmax": "",
                "Best_Bound": "",
                "Gap_Percent": "",
                "Solver_Time_Limit_s": float(ICP_TIME_LIMIT if time_limit is None else time_limit),
                "Actual_Solver_WallTime_s": upper_actual,
                "End_to_End_Time_s": round(time.perf_counter() - started, 6),
                "Num_Search_Workers": WORKERS,
                "Branches": p1_ctx["solver"].NumBranches(),
                "Conflicts": p1_ctx["solver"].NumConflicts(),
                "Feasibility_Check_Passed": False,
                "Feasibility_Check_Message": "Upper phase produced no FEASIBLE/OPTIMAL solution.",
                "Solution_File": "",
                "Upper_Status": raw_status,
                "Upper_Best_Bound": round(float(p1_ctx["solver"].BestObjectiveBound()) / float(mod.TIME_SCALE), 6),
                "Upper_Time_Limit_s": upper_time,
                "Upper_Actual_Time_s": upper_actual,
                "Lower_Time_Limit_s": lower_time,
            }
        else:
            p2_ctx = mod.build_phase2_agv_fixed_order(data, p1, time_limit=lower_time, workers=WORKERS, log=False, phase2_mode="retime")
            result = mod.extract_results(data_path, data, p1_ctx, p1, p2_ctx)
            raw_status = p2_ctx.get("status_name", status_name(p2_ctx.get("status")))
            lower_actual = round(p2_ctx["solver"].WallTime(), 6)
            if result:
                write_json(sol_path, result)
                feasible, message = check_feasibility(data, result)
                meta = result["Meta_Data"]
                metrics = result["Metrics"]
                row = {
                    **dimensions(data),
                    "Instance": instance,
                    "Method": method,
                    "Random_Seed": RANDOM_SEED,
                    "Solve_Status": meta.get("Solve_Status"),
                    "Raw_CP_Status": meta.get("Raw_CP_Status", raw_status),
                    "Cmax": metrics.get("Objective_Cmax"),
                    "Best_Bound": metrics.get("Best_Bound"),
                    "Gap_Percent": gap_percent(metrics.get("MIP_Gap")),
                    "Solver_Time_Limit_s": float(ICP_TIME_LIMIT if time_limit is None else time_limit),
                    "Actual_Solver_WallTime_s": metrics.get("Wall_Clock_Time_s"),
                    "End_to_End_Time_s": round(time.perf_counter() - started, 6),
                    "Num_Search_Workers": meta.get("CP_WORKERS"),
                    "Branches": meta.get("Branches"),
                    "Conflicts": meta.get("Conflicts"),
                    "Feasibility_Check_Passed": feasible,
                    "Feasibility_Check_Message": message,
                    "Solution_File": str(sol_path),
                    "Upper_Status": meta.get("Phase1_Solve_Status"),
                    "Upper_Cmax": meta.get("Phase1_Objective_Cmax"),
                    "Upper_Best_Bound": meta.get("Phase1_Best_Bound"),
                    "Upper_Gap_Percent": gap_percent(meta.get("Phase1_Gap")),
                    "Upper_Time_Limit_s": upper_time,
                    "Upper_Actual_Time_s": meta.get("Phase1_Wall_Clock_Time_s"),
                    "Lower_Status": meta.get("Solve_Status"),
                    "Lower_Cmax": metrics.get("Objective_Cmax"),
                    "Lower_Best_Bound": metrics.get("Best_Bound"),
                    "Lower_Gap_Percent": gap_percent(metrics.get("MIP_Gap")),
                    "Lower_Time_Limit_s": lower_time,
                    "Lower_Actual_Time_s": meta.get("Phase2_Wall_Clock_Time_s"),
                    "HCP_Total_Solver_Time_s": metrics.get("Wall_Clock_Time_s"),
                }
            else:
                row = {
                    **dimensions(data),
                    "Instance": instance,
                    "Method": method,
                    "Random_Seed": RANDOM_SEED,
                    "Solve_Status": raw_status,
                    "Raw_CP_Status": raw_status,
                    "Cmax": "",
                    "Best_Bound": round(float(p2_ctx["solver"].BestObjectiveBound()) / float(mod.TIME_SCALE), 6),
                    "Gap_Percent": "",
                    "Solver_Time_Limit_s": float(ICP_TIME_LIMIT if time_limit is None else time_limit),
                    "Actual_Solver_WallTime_s": round(upper_actual + lower_actual, 6),
                    "End_to_End_Time_s": round(time.perf_counter() - started, 6),
                    "Num_Search_Workers": WORKERS,
                    "Branches": p1_ctx["solver"].NumBranches() + p2_ctx["solver"].NumBranches(),
                    "Conflicts": p1_ctx["solver"].NumConflicts() + p2_ctx["solver"].NumConflicts(),
                    "Feasibility_Check_Passed": False,
                    "Feasibility_Check_Message": "Lower phase produced no FEASIBLE/OPTIMAL complete solution.",
                    "Solution_File": "",
                    "Upper_Status": p1.get("solve_status"),
                    "Upper_Cmax": p1.get("cmax"),
                    "Upper_Best_Bound": p1.get("best_bound"),
                    "Upper_Gap_Percent": gap_percent(p1.get("gap")),
                    "Upper_Time_Limit_s": upper_time,
                    "Upper_Actual_Time_s": upper_actual,
                    "Lower_Status": raw_status,
                    "Lower_Best_Bound": round(float(p2_ctx["solver"].BestObjectiveBound()) / float(mod.TIME_SCALE), 6),
                    "Lower_Time_Limit_s": lower_time,
                    "Lower_Actual_Time_s": lower_actual,
                    "HCP_Total_Solver_Time_s": round(upper_actual + lower_actual, 6),
                }

    return row


def child_main(args):
    try:
        row = solve_child(args.method, args.instance, args.results_dir, args.time_limit)
        write_json(args.result_file, {"ok": True, "row": row})
        return 0
    except MemoryError as exc:
        write_json(args.result_file, {"ok": False, "status": "OOM", "error": str(exc), "traceback": traceback.format_exc()})
        return 99
    except Exception as exc:
        write_json(args.result_file, {"ok": False, "status": "PROCESS_CRASH", "error": str(exc), "traceback": traceback.format_exc()})
        traceback.print_exc()
        return 98


def existing_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_completed(row):
    return bool(row and row.get("Instance") and row.get("Method"))


def is_successful(row):
    if not row:
        return False
    if row.get("Solve_Status") in ("HARD_TIMEOUT", "OOM", "PROCESS_CRASH", "ERROR"):
        return False
    return bool(row.get("Instance")) and bool(row.get("Method"))


def run_child_process(instance, method, hard_timeout, time_limit=None):
    ensure_dirs()
    run_root = SMOKE_DIR if time_limit is not None else NEW_SOLVE_DIR
    log_file = run_root / method / "Logs" / f"{instance}_{method}.log"
    result_file = run_root / method / "Logs" / f"{instance}_{method}_child_result.json"
    ensure_ortools_available(log_file)
    if result_file.exists():
        result_file.unlink()
    cmd = [
        str(PYTHON_EXE), "-u", str(Path(__file__).resolve()),
        "--child", "--instance", instance, "--method", method,
        "--results-dir", str(run_root), "--result-file", str(result_file),
    ]
    if time_limit is not None:
        cmd += ["--time-limit", str(time_limit)]
    with log_file.open("w", encoding="utf-8") as log:
        log.write(f"[{now_iso()}] Starting {method} {instance}\n")
        log.write(f"Command: {' '.join(cmd)}\n")
        log.flush()
        p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(BASE_DIR), text=True)
        peak = 0.0
        deadline = time.monotonic() + hard_timeout
        while True:
            rc = p.poll()
            mem = peak_memory_mb(p.pid)
            if mem not in ("", None):
                peak = max(peak, float(mem))
            if rc is not None:
                break
            if time.monotonic() > deadline:
                log.write(f"[{now_iso()}] HARD_TIMEOUT after {hard_timeout} seconds. Killing child tree.\n")
                log.flush()
                kill_process_tree(p.pid)
                try:
                    rc = p.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    log.write(f"[{now_iso()}] Child tree did not exit within 30 seconds after kill request.\n")
                    log.flush()
                    kill_process_tree(p.pid)
                    try:
                        rc = p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        rc = "KILL_TIMEOUT"
                row = {
                    **dimensions(read_json(instance_path(instance))),
                    "Instance": instance,
                    "Method": method,
                    "Random_Seed": RANDOM_SEED,
                    "Solve_Status": "HARD_TIMEOUT",
                    "Raw_CP_Status": "HARD_TIMEOUT",
                    "Solver_Time_Limit_s": time_limit if time_limit is not None else ICP_TIME_LIMIT,
                    "Peak_Memory_MB": peak,
                    "Child_Process_Exit_Code": rc,
                    "Feasibility_Check_Passed": False,
                    "Feasibility_Check_Message": "No complete solution due to OS hard timeout.",
                    "Log_File": str(log_file),
                    "Error_Message": "HARD_TIMEOUT",
                }
                return row
            time.sleep(2.0)

    if result_file.exists():
        payload = read_json(result_file)
        if payload.get("ok"):
            row = payload["row"]
        else:
            row = {
                **dimensions(read_json(instance_path(instance))),
                "Instance": instance,
                "Method": method,
                "Random_Seed": RANDOM_SEED,
                "Solve_Status": payload.get("status", "PROCESS_CRASH"),
                "Raw_CP_Status": payload.get("status", "PROCESS_CRASH"),
                "Peak_Memory_MB": peak,
                "Child_Process_Exit_Code": rc,
                "Feasibility_Check_Passed": False,
                "Feasibility_Check_Message": "No complete solution due to child error.",
                "Log_File": str(log_file),
                "Error_Message": payload.get("error", ""),
            }
            append_log(log_file, payload.get("traceback", ""))
    else:
        row = {
            **dimensions(read_json(instance_path(instance))),
            "Instance": instance,
            "Method": method,
            "Random_Seed": RANDOM_SEED,
            "Solve_Status": "PROCESS_CRASH" if rc else "ERROR",
            "Raw_CP_Status": "PROCESS_CRASH" if rc else "ERROR",
            "Peak_Memory_MB": peak,
            "Child_Process_Exit_Code": rc,
            "Feasibility_Check_Passed": False,
            "Feasibility_Check_Message": "Child did not write result JSON.",
            "Log_File": str(log_file),
            "Error_Message": f"Missing result file; exit code {rc}",
        }
    row["Peak_Memory_MB"] = peak
    row["Child_Process_Exit_Code"] = rc
    row["Log_File"] = str(log_file)
    row["Result_Source"] = "SMOKE_TEST" if time_limit is not None else "NEW_FORMAL_RUN"
    return row


def compare_pair(instance, run_rows):
    hcp = next((r for r in run_rows if r.get("Instance") == instance and r.get("Method") == "HCP"), None)
    icp = next((r for r in run_rows if r.get("Instance") == instance and r.get("Method") == "ICP"), None)
    data = read_json(instance_path(instance))
    row = {**dimensions(data), "Instance": instance}
    if not hcp or not icp:
        row.update({"Comparison_Category": "INVALID", "Comparison_Valid": False, "Note": "Missing HCP or ICP run."})
        return row
    row.update({
        "HCP_Status": hcp.get("Solve_Status"),
        "HCP_Cmax": hcp.get("Cmax"),
        "HCP_Upper_Time_s": hcp.get("Upper_Actual_Time_s"),
        "HCP_Lower_Time_s": hcp.get("Lower_Actual_Time_s"),
        "HCP_Total_Time_s": hcp.get("HCP_Total_Solver_Time_s") or hcp.get("Actual_Solver_WallTime_s"),
        "HCP_Peak_Memory_MB": hcp.get("Peak_Memory_MB"),
        "HCP_Feasibility_Passed": hcp.get("Feasibility_Check_Passed"),
        "ICP_Status": icp.get("Solve_Status"),
        "ICP_Cmax": icp.get("Cmax"),
        "ICP_Best_Bound": icp.get("Best_Bound"),
        "ICP_Gap_Percent": icp.get("Gap_Percent"),
        "ICP_Time_s": icp.get("Actual_Solver_WallTime_s"),
        "ICP_Peak_Memory_MB": icp.get("Peak_Memory_MB"),
        "ICP_Feasibility_Passed": icp.get("Feasibility_Check_Passed"),
        "ICP_Failure_Type": "" if icp.get("Cmax") not in ("", None) else icp.get("Solve_Status"),
        "ICP_Feasible_Not_Optimal": str(icp.get("Solve_Status")) == "FEASIBLE" and float(icp.get("Gap_Percent") or 0) > 0,
        "HCP_Result_Source": hcp.get("Result_Source"),
        "ICP_Result_Source": icp.get("Result_Source"),
        "HCP_Upper_Status": hcp.get("Upper_Status"),
        "HCP_Lower_Status": hcp.get("Lower_Status") or hcp.get("Solve_Status"),
        "HCP_Time_s": hcp.get("HCP_Total_Solver_Time_s") or hcp.get("Actual_Solver_WallTime_s"),
    })
    h_ok = str(hcp.get("Feasibility_Check_Passed")).lower() == "true" and hcp.get("Cmax") not in ("", None)
    i_ok = str(icp.get("Feasibility_Check_Passed")).lower() == "true" and icp.get("Cmax") not in ("", None)
    if h_ok and i_ok:
        h = float(hcp["Cmax"])
        i = float(icp["Cmax"])
        delta = h - i
        rd = delta / h * 100.0 if abs(h) > 1e-12 else ""
        row["Delta_Cmax"] = round(delta, 6)
        row["RD_Percent"] = round(rd, 6) if rd != "" else ""
        row["Comparison_Valid"] = True
        if delta < -1e-6:
            row["Comparison_Category"] = "HCP_BETTER"
        elif delta > 1e-6:
            row["Comparison_Category"] = "ICP_BETTER"
        else:
            row["Comparison_Category"] = "EQUAL"
        row["Note"] = "Both methods have feasible schedules that passed the checker."
    elif h_ok:
        row.update({"Comparison_Category": "HCP_ONLY_FEASIBLE", "Comparison_Valid": False, "Note": "Only HCP has a checked feasible solution."})
    elif i_ok:
        row.update({"Comparison_Category": "ICP_ONLY_FEASIBLE", "Comparison_Valid": False, "Note": "Only ICP has a checked feasible solution."})
    elif hcp.get("Solve_Status") in ("HARD_TIMEOUT", "OOM", "PROCESS_CRASH") or icp.get("Solve_Status") in ("HARD_TIMEOUT", "OOM", "PROCESS_CRASH"):
        row.update({"Comparison_Category": "ERROR", "Comparison_Valid": False, "Note": "At least one run ended by timeout, OOM, or crash."})
    else:
        row.update({"Comparison_Category": "NO_FEASIBLE_SOLUTION", "Comparison_Valid": False, "Note": "Neither method has a checked feasible solution."})
    return row


def update_all_csv(run_rows):
    run_rows = sorted(run_rows, key=lambda r: (INSTANCES.index(r["Instance"]) if r.get("Instance") in INSTANCES else 999, 0 if r.get("Method") == "HCP" else 1))
    write_csv(RESULTS_DIR / "run_level_results.csv", run_rows, RUN_LEVEL_FIELDS)
    pairs = []
    for instance in INSTANCES:
        if any(r.get("Instance") == instance and r.get("Method") == "HCP" for r in run_rows) and any(r.get("Instance") == instance and r.get("Method") == "ICP" for r in run_rows):
            pairs.append(compare_pair(instance, run_rows))
    write_csv(RESULTS_DIR / "paired_comparison.csv", pairs, PAIRED_FIELDS)
    write_csv(RESULTS_DIR / "final_60_results.csv", pairs, PAIRED_FIELDS)
    better = []
    for row in pairs:
        if str(row.get("Comparison_Valid")).lower() == "true" and row.get("Comparison_Category") == "HCP_BETTER":
            better.append({
                "Instance": row.get("Instance"),
                "Base_Instance": row.get("Base_Instance"),
                "Topology": row.get("Topology"),
                "Operations": row.get("Operations"),
                "Jobs": row.get("Jobs"),
                "Machines": row.get("Machines"),
                "AGVs": row.get("AGVs"),
                "ICP_Status": row.get("ICP_Status"),
                "ICP_Cmax": row.get("ICP_Cmax"),
                "ICP_Best_Bound": row.get("ICP_Best_Bound"),
                "ICP_Gap_Percent": row.get("ICP_Gap_Percent"),
                "ICP_Time_s": row.get("ICP_Time_s"),
                "HCP_Status": row.get("HCP_Lower_Status"),
                "HCP_Cmax": row.get("HCP_Cmax"),
                "HCP_Time_s": row.get("HCP_Total_Time_s"),
                "Delta_Cmax": row.get("Delta_Cmax"),
                "RD_Percent": row.get("RD_Percent"),
            })
    write_csv(RESULTS_DIR / "hcp_better_cases.csv", better, HCP_BETTER_FIELDS)
    hcp_only = []
    for row in pairs:
        if row.get("Comparison_Category") == "HCP_ONLY_FEASIBLE":
            hcp_only.append({
                "Instance": row.get("Instance"),
                "Topology": row.get("Topology"),
                "Operations": row.get("Operations"),
                "Jobs": row.get("Jobs"),
                "Machines": row.get("Machines"),
                "AGVs": row.get("AGVs"),
                "HCP_Status": row.get("HCP_Status"),
                "HCP_Cmax": row.get("HCP_Cmax"),
                "HCP_Time_s": row.get("HCP_Total_Time_s"),
                "ICP_Status": row.get("ICP_Status"),
                "ICP_Failure_Type": row.get("ICP_Failure_Type"),
                "ICP_Best_Bound": row.get("ICP_Best_Bound"),
                "ICP_Time_s": row.get("ICP_Time_s"),
                "ICP_Peak_Memory_MB": row.get("ICP_Peak_Memory_MB"),
            })
    write_csv(RESULTS_DIR / "hcp_only_feasible_cases.csv", hcp_only, HCP_ONLY_FIELDS)

    icp_feas_not_opt = []
    for row in pairs:
        if str(row.get("ICP_Feasible_Not_Optimal")).lower() == "true":
            icp_feas_not_opt.append({
                "Instance": row.get("Instance"),
                "Base_Instance": row.get("Base_Instance"),
                "Topology": row.get("Topology"),
                "Operations": row.get("Operations"),
                "Jobs": row.get("Jobs"),
                "Machines": row.get("Machines"),
                "AGVs": row.get("AGVs"),
                "ICP_Status": row.get("ICP_Status"),
                "ICP_Cmax": row.get("ICP_Cmax"),
                "ICP_Best_Bound": row.get("ICP_Best_Bound"),
                "ICP_Gap_Percent": row.get("ICP_Gap_Percent"),
                "ICP_Time_s": row.get("ICP_Time_s"),
                "ICP_Peak_Memory_MB": row.get("ICP_Peak_Memory_MB"),
                "HCP_Status": row.get("HCP_Status"),
                "HCP_Cmax": row.get("HCP_Cmax"),
                "HCP_Time_s": row.get("HCP_Total_Time_s"),
                "Delta_Cmax": row.get("Delta_Cmax"),
                "RD_Percent": row.get("RD_Percent"),
                "Comparison_Category": row.get("Comparison_Category"),
            })
    write_csv(RESULTS_DIR / "icp_feasible_not_optimal_cases.csv", icp_feas_not_opt, ICP_FEASIBLE_NOT_OPTIMAL_FIELDS)

    topo_rows = []
    for base in INSTANCE_BASES:
        bt = next((row for row in pairs if row.get("Base_Instance") == base and row.get("Topology") == "BT"), None)
        dct = next((row for row in pairs if row.get("Base_Instance") == base and row.get("Topology") == "DCT"), None)
        if not bt or not dct:
            continue
        bt_delta = bt.get("Delta_Cmax")
        dct_delta = dct.get("Delta_Cmax")
        more = ""
        if bt_delta not in ("", None) and dct_delta not in ("", None):
            more = float(dct_delta) < float(bt_delta)
        topo_rows.append({
            "Base_Instance": base,
            "Operations": bt.get("Operations"),
            "Jobs": bt.get("Jobs"),
            "Machines": bt.get("Machines"),
            "AGVs": bt.get("AGVs"),
            "BT_ICP_Status": bt.get("ICP_Status"),
            "BT_ICP_Cmax": bt.get("ICP_Cmax"),
            "BT_ICP_Best_Bound": bt.get("ICP_Best_Bound"),
            "BT_ICP_Gap": bt.get("ICP_Gap_Percent"),
            "BT_HCP_Status": bt.get("HCP_Status"),
            "BT_HCP_Cmax": bt.get("HCP_Cmax"),
            "BT_Delta": bt_delta,
            "BT_RD": bt.get("RD_Percent"),
            "BT_Category": bt.get("Comparison_Category"),
            "DCT_ICP_Status": dct.get("ICP_Status"),
            "DCT_ICP_Cmax": dct.get("ICP_Cmax"),
            "DCT_ICP_Best_Bound": dct.get("ICP_Best_Bound"),
            "DCT_ICP_Gap": dct.get("ICP_Gap_Percent"),
            "DCT_HCP_Status": dct.get("HCP_Status"),
            "DCT_HCP_Cmax": dct.get("HCP_Cmax"),
            "DCT_Delta": dct_delta,
            "DCT_RD": dct.get("RD_Percent"),
            "DCT_Category": dct.get("Comparison_Category"),
            "DCT_More_Favorable_To_HCP": more,
        })
    write_csv(RESULTS_DIR / "topology_paired_results.csv", topo_rows, TOPOLOGY_PAIRED_FIELDS)
    write_csv(RESULTS_DIR / "final_30_base_pair_results.csv", topo_rows, TOPOLOGY_PAIRED_FIELDS)

    scale_rows = []
    scale_summary = []
    topology_summary = []
    for scale in SCALES:
        scale_subset = [row for row in pairs if str(row.get("Instance", "")).startswith(f"LS{scale}_")]
        n_scale = len(scale_subset)
        scale_summary.append({
            "Scale": scale,
            "Number_of_Instances": n_scale,
            "HCP_BETTER_Count": sum(1 for row in scale_subset if row.get("Comparison_Category") == "HCP_BETTER"),
            "HCP_ONLY_FEASIBLE_Count": sum(1 for row in scale_subset if row.get("Comparison_Category") == "HCP_ONLY_FEASIBLE"),
            "ICP_BETTER_Count": sum(1 for row in scale_subset if row.get("Comparison_Category") == "ICP_BETTER"),
            "EQUAL_Count": sum(1 for row in scale_subset if row.get("Comparison_Category") == "EQUAL"),
            "ICP_ONLY_FEASIBLE_Count": sum(1 for row in scale_subset if row.get("Comparison_Category") == "ICP_ONLY_FEASIBLE"),
            "NO_FEASIBLE_Count": sum(1 for row in scale_subset if row.get("Comparison_Category") in ("NO_FEASIBLE_SOLUTION", "ERROR")),
            "HCP_Feasible_Count": sum(1 for row in scale_subset if str(row.get("HCP_Feasibility_Passed")).lower() == "true"),
            "ICP_Feasible_Count": sum(1 for row in scale_subset if str(row.get("ICP_Feasibility_Passed")).lower() == "true"),
            "HCP_Feasible_Rate": round(sum(1 for row in scale_subset if str(row.get("HCP_Feasibility_Passed")).lower() == "true") / n_scale, 6) if n_scale else "",
            "ICP_Feasible_Rate": round(sum(1 for row in scale_subset if str(row.get("ICP_Feasibility_Passed")).lower() == "true") / n_scale, 6) if n_scale else "",
        })
        for topology in ["BT", "DCT"]:
            subset = [row for row in pairs if row.get("Topology") == topology and str(row.get("Instance", "")).startswith(f"LS{scale}_")]
            n = len(subset)
            scale_rows.append({
                "Scale": scale,
                "Topology": topology,
                "Number_of_Instances": n,
                "ICP_Optimal_Count": sum(1 for row in subset if row.get("ICP_Status") == "OPTIMAL"),
                "ICP_Feasible_Not_Optimal_Count": sum(1 for row in subset if str(row.get("ICP_Feasible_Not_Optimal")).lower() == "true"),
                "ICP_No_Feasible_Count": sum(1 for row in subset if str(row.get("ICP_Feasibility_Passed")).lower() != "true"),
                "HCP_Feasible_Count": sum(1 for row in subset if str(row.get("HCP_Feasibility_Passed")).lower() == "true"),
                "HCP_No_Feasible_Count": sum(1 for row in subset if str(row.get("HCP_Feasibility_Passed")).lower() != "true"),
                "HCP_BETTER_Count": sum(1 for row in subset if row.get("Comparison_Category") == "HCP_BETTER"),
                "HCP_ONLY_FEASIBLE_Count": sum(1 for row in subset if row.get("Comparison_Category") == "HCP_ONLY_FEASIBLE"),
                "ICP_BETTER_Count": sum(1 for row in subset if row.get("Comparison_Category") == "ICP_BETTER"),
                "EQUAL_Count": sum(1 for row in subset if row.get("Comparison_Category") == "EQUAL"),
                "ICP_ONLY_FEASIBLE_Count": sum(1 for row in subset if row.get("Comparison_Category") == "ICP_ONLY_FEASIBLE"),
                "NO_FEASIBLE_Count": sum(1 for row in subset if row.get("Comparison_Category") == "NO_FEASIBLE_SOLUTION"),
            })
    write_csv(RESULTS_DIR / "scale_topology_summary.csv", scale_rows, SCALE_TOPOLOGY_SUMMARY_FIELDS)
    write_csv(RESULTS_DIR / "final_60_scale_topology_summary.csv", scale_rows, SCALE_TOPOLOGY_SUMMARY_FIELDS)
    write_csv(RESULTS_DIR / "final_60_scale_summary.csv", scale_summary, list(scale_summary[0].keys()) if scale_summary else ["Scale"])
    for topology in ["BT", "DCT"]:
        subset = [row for row in pairs if row.get("Topology") == topology]
        n = len(subset)
        topology_summary.append({
            "Topology": topology,
            "Number_of_Instances": n,
            "HCP_BETTER_Count": sum(1 for row in subset if row.get("Comparison_Category") == "HCP_BETTER"),
            "HCP_ONLY_FEASIBLE_Count": sum(1 for row in subset if row.get("Comparison_Category") == "HCP_ONLY_FEASIBLE"),
            "ICP_BETTER_Count": sum(1 for row in subset if row.get("Comparison_Category") == "ICP_BETTER"),
            "EQUAL_Count": sum(1 for row in subset if row.get("Comparison_Category") == "EQUAL"),
            "ICP_ONLY_FEASIBLE_Count": sum(1 for row in subset if row.get("Comparison_Category") == "ICP_ONLY_FEASIBLE"),
            "NO_FEASIBLE_Count": sum(1 for row in subset if row.get("Comparison_Category") in ("NO_FEASIBLE_SOLUTION", "ERROR")),
            "HCP_Feasible_Count": sum(1 for row in subset if str(row.get("HCP_Feasibility_Passed")).lower() == "true"),
            "ICP_Feasible_Count": sum(1 for row in subset if str(row.get("ICP_Feasibility_Passed")).lower() == "true"),
            "HCP_Feasible_Rate": round(sum(1 for row in subset if str(row.get("HCP_Feasibility_Passed")).lower() == "true") / n, 6) if n else "",
            "ICP_Feasible_Rate": round(sum(1 for row in subset if str(row.get("ICP_Feasibility_Passed")).lower() == "true") / n, 6) if n else "",
        })
    write_csv(RESULTS_DIR / "final_60_topology_summary.csv", topology_summary, list(topology_summary[0].keys()) if topology_summary else ["Topology"])
    return pairs, better


def write_progress(current_instance="", current_method="", status="IDLE", last_error="", last_completed="", retry_mode=False, retry_completed="", retry_total=""):
    run_rows = existing_rows(RESULTS_DIR / "run_level_results.csv")
    pair_rows = existing_rows(RESULTS_DIR / "paired_comparison.csv")
    completed_methods = len([r for r in run_rows if is_completed(r)])
    hcp_better = len([r for r in pair_rows if r.get("Comparison_Category") == "HCP_BETTER"])
    hcp_only = len([r for r in pair_rows if r.get("Comparison_Category") == "HCP_ONLY_FEASIBLE"])
    icp_feas_not_opt = len([r for r in pair_rows if str(r.get("ICP_Feasible_Not_Optimal")).lower() == "true"])
    icp_better = len([r for r in pair_rows if r.get("Comparison_Category") == "ICP_BETTER"])
    row = {
        "Timestamp": now_iso(),
        "Background_PID": os.getpid(),
        "Current_Instance": current_instance,
        "Current_Method": current_method,
        "Current_Status": status,
        "Completed_Method_Runs": completed_methods,
        "Total_Method_Runs": TOTAL_METHOD_RUNS,
        "Completed_Pairs": len(pair_rows),
        "Total_Pairs": len(INSTANCES),
        "HCP_Better_Count": hcp_better,
        "HCP_Only_Feasible_Count": hcp_only,
        "ICP_Feasible_Not_Optimal_Count": icp_feas_not_opt,
        "ICP_Better_Count": icp_better,
        "Last_Completed_Instance": last_completed,
        "Last_Error": last_error,
    }
    if retry_mode:
        row["Completed_Retry_Method_Runs"] = retry_completed
        row["Total_Retry_Method_Runs"] = retry_total
        write_csv(RESULTS_DIR / "retry_progress.csv", [row], RETRY_PROGRESS_FIELDS)
        (RESULTS_DIR / "retry_heartbeat.txt").write_text(now_iso(), encoding="utf-8")
    else:
        write_csv(RESULTS_DIR / "progress.csv", [row], PROGRESS_FIELDS)
        (RESULTS_DIR / "heartbeat.txt").write_text(now_iso(), encoding="utf-8")


def heartbeat_loop(stop_event, state):
    while not stop_event.wait(30.0):
        write_progress(
            state.get("instance", ""),
            state.get("method", ""),
            state.get("status", "RUNNING"),
            state.get("last_error", ""),
            state.get("last_completed", ""),
            state.get("retry_mode", False),
            state.get("retry_completed", ""),
            state.get("retry_total", ""),
        )


def run_smoke_tests():
    ensure_dirs()
    smoke_rows = []
    for instance in ["LS220_01_BT", "LS220_01_DCT"]:
        for method in ["HCP", "ICP"]:
            row = run_child_process(instance, method, hard_timeout=SMOKE_HARD_TIMEOUT, time_limit=SMOKE_TIME_LIMIT)
            row["Log_File"] = str(Path(row.get("Log_File", "")).resolve()) if row.get("Log_File") else ""
            smoke_rows.append(row)
    write_csv(SMOKE_DIR / "smoke_results.csv", smoke_rows, RUN_LEVEL_FIELDS)
    ok = all(r.get("Solve_Status") not in ("PROCESS_CRASH", "ERROR", "MODEL_INVALID") for r in smoke_rows)
    return ok, smoke_rows


def write_summary(run_rows, pairs):
    lines = [
        "# Final Large Scale 60-Instance Solve Summary",
        "",
        f"Generated: {now_iso()}",
        f"Python: {sys.version.split()[0]}",
    ]
    try:
        import ortools
        lines.append(f"OR-Tools: {ortools.__version__}")
    except Exception as exc:
        lines.append(f"OR-Tools: unavailable ({exc})")
    lines += [
        f"Instances: {len(INSTANCES)}",
        f"Completed method runs: {len(run_rows)} / {TOTAL_METHOD_RUNS}",
        f"Completed paired comparisons: {len(pairs)} / {len(INSTANCES)}",
        f"HCP_BETTER: {sum(1 for p in pairs if p.get('Comparison_Category') == 'HCP_BETTER')}",
        f"HCP_ONLY_FEASIBLE: {sum(1 for p in pairs if p.get('Comparison_Category') == 'HCP_ONLY_FEASIBLE')}",
        f"ICP_BETTER: {sum(1 for p in pairs if p.get('Comparison_Category') == 'ICP_BETTER')}",
        f"EQUAL: {sum(1 for p in pairs if p.get('Comparison_Category') == 'EQUAL')}",
        f"ICP_ONLY_FEASIBLE: {sum(1 for p in pairs if p.get('Comparison_Category') == 'ICP_ONLY_FEASIBLE')}",
        f"NO_FEASIBLE: {sum(1 for p in pairs if p.get('Comparison_Category') == 'NO_FEASIBLE_SOLUTION')}",
        f"ICP OPTIMAL: {sum(1 for r in run_rows if r.get('Method') == 'ICP' and r.get('Solve_Status') == 'OPTIMAL')}",
        f"ICP FEASIBLE_NOT_OPTIMAL: {sum(1 for p in pairs if str(p.get('ICP_Feasible_Not_Optimal')).lower() == 'true')}",
        f"ICP no-feasible: {sum(1 for p in pairs if str(p.get('ICP_Feasibility_Passed')).lower() != 'true')}",
        "",
        "By scale:",
    ]
    for scale in SCALES:
        subset = [p for p in pairs if str(p.get("Instance", "")).startswith(f"LS{scale}_")]
        lines.append(
            f"{scale}: HCP_BETTER={sum(1 for p in subset if p.get('Comparison_Category') == 'HCP_BETTER')}, "
            f"HCP_ONLY_FEASIBLE={sum(1 for p in subset if p.get('Comparison_Category') == 'HCP_ONLY_FEASIBLE')}, "
            f"ICP_BETTER={sum(1 for p in subset if p.get('Comparison_Category') == 'ICP_BETTER')}, "
            f"ICP_FEASIBLE_NOT_OPTIMAL={sum(1 for p in subset if str(p.get('ICP_Feasible_Not_Optimal')).lower() == 'true')}"
        )
    lines.append("")
    lines.append("By topology:")
    for topology in ["BT", "DCT"]:
        subset = [p for p in pairs if p.get("Topology") == topology]
        lines.append(
            f"{topology}: HCP_BETTER={sum(1 for p in subset if p.get('Comparison_Category') == 'HCP_BETTER')}, "
            f"HCP_ONLY_FEASIBLE={sum(1 for p in subset if p.get('Comparison_Category') == 'HCP_ONLY_FEASIBLE')}, "
            f"ICP_BETTER={sum(1 for p in subset if p.get('Comparison_Category') == 'ICP_BETTER')}, "
            f"ICP_FEASIBLE_NOT_OPTIMAL={sum(1 for p in subset if str(p.get('ICP_Feasible_Not_Optimal')).lower() == 'true')}"
        )
    (RESULTS_DIR / "final_60_experiment_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_environment_retry(args, state):
    retry_plan = load_environment_retry_plan()
    retry_keys = {(row["Instance"], row["Method"]) for row in retry_plan}
    retry_meta = {(row["Instance"], row["Method"]): row for row in retry_plan}
    run_rows = existing_rows(RESULTS_DIR / "run_level_results.csv")
    # Keep completed retry rows on resume; only discard the original invalid environment rows.
    run_rows = [
        row for row in run_rows
        if (row.get("Instance"), row.get("Method")) not in retry_keys
        or row.get("Result_Source") == "ENVIRONMENT_RETRY_RUN"
    ]
    completed_keys = {(row.get("Instance"), row.get("Method")) for row in run_rows if is_completed(row) and not row_has_environment_error(row)}
    completed_retry = len([key for key in retry_keys if key in completed_keys])
    state.update({"retry_mode": True, "retry_total": len(retry_plan), "retry_completed": completed_retry})
    update_all_csv(run_rows)
    write_progress(status="RETRY_STARTING", retry_mode=True, retry_completed=completed_retry, retry_total=len(retry_plan))

    for idx, plan_row in enumerate(retry_plan, start=1):
        instance = plan_row["Instance"]
        method = plan_row["Method"]
        if (instance, method) in completed_keys:
            continue
        completed_retry = len([key for key in retry_keys if key in completed_keys])
        state.update({"instance": instance, "method": method, "status": "RETRY_RUNNING", "retry_completed": completed_retry})
        write_progress(instance, method, "RETRY_RUNNING", retry_mode=True, retry_completed=completed_retry, retry_total=len(retry_plan))
        row = run_child_process(instance, method, hard_timeout=HARD_TIMEOUT)
        original = retry_meta[(instance, method)]
        row["Was_Rerun"] = True
        row["Original_Status"] = original.get("Original_Status")
        row["Original_Error"] = original.get("Original_Error_Message")
        row["Rerun_Reason"] = "PYTHON_ENVIRONMENT_ORTOOLS_MISSING"
        row["Result_Source"] = "ENVIRONMENT_RETRY_RUN"
        run_rows = [r for r in run_rows if not (r.get("Instance") == instance and r.get("Method") == method)]
        run_rows.append(row)
        completed_keys.add((instance, method))
        pairs, _ = update_all_csv(run_rows)
        completed_retry = len([key for key in retry_keys if key in completed_keys])
        state.update({"last_completed": f"{instance} {method}", "last_error": row.get("Error_Message", ""), "retry_completed": completed_retry})
        write_progress(instance, method, "RETRY_COMPLETED", row.get("Error_Message", ""), f"{instance} {method}", True, completed_retry, len(retry_plan))

    pairs, _ = update_all_csv(run_rows)
    write_summary(run_rows, pairs)
    write_progress(status="RETRY_DONE", retry_mode=True, retry_completed=len(retry_plan), retry_total=len(retry_plan))
    return 0


def run_parent(args):
    ensure_dirs()
    state = {"status": "STARTING", "instance": "", "method": "", "retry_mode": args.retry_environment_failures}
    stop_event = threading.Event()
    hb = threading.Thread(target=heartbeat_loop, args=(stop_event, state), daemon=True)
    hb.start()
    write_progress(status="RETRY_STARTING" if args.retry_environment_failures else "STARTING", retry_mode=args.retry_environment_failures)

    if args.retry_environment_failures:
        try:
            return run_environment_retry(args, state)
        finally:
            stop_event.set()

    if args.smoke_first:
        state.update({"instance": "LS220_01_BT/LS220_01_DCT", "method": "HCP/ICP", "status": "SMOKE_TEST"})
        write_progress("LS220_01_BT/LS220_01_DCT", "HCP/ICP", "SMOKE_TEST")
        ok, smoke_rows = run_smoke_tests()
        if not ok:
            write_progress(status="SMOKE_FAILED", last_error="Smoke test failed.")
            stop_event.set()
            print("Smoke test failed; formal run not started.", flush=True)
            return 2
        if args.smoke_only:
            write_progress(status="SMOKE_PASSED")
            return 0

    try:
        imported_rows = existing_rows(EXP_ROOT / "Previous_Results_Imported" / "imported_run_level_results.csv")
        run_rows = existing_rows(RESULTS_DIR / "run_level_results.csv") if args.resume else imported_rows
        if args.force_rerun:
            run_rows = imported_rows
        completed_keys = {(r.get("Instance"), r.get("Method")) for r in run_rows if is_completed(r)}
        if args.retry_failed:
            reusable_keys = {(r.get("Instance"), r.get("Method")) for r in imported_rows if is_completed(r)}
            run_rows = [r for r in run_rows if (r.get("Instance"), r.get("Method")) in reusable_keys or is_successful(r)]
            completed_keys = {(r.get("Instance"), r.get("Method")) for r in run_rows if is_completed(r)}
        update_all_csv(run_rows)

        plan_rows = target_run_plan()
        for plan_row in plan_rows:
            instance = plan_row["Instance"]
            method = plan_row["Method"]
            if (instance, method) in completed_keys:
                continue
            state.update({"instance": instance, "method": method, "status": "RUNNING"})
            write_progress(instance, method, "RUNNING")
            row = run_child_process(instance, method, hard_timeout=HARD_TIMEOUT)
            run_rows = [r for r in run_rows if not (r.get("Instance") == instance and r.get("Method") == method)]
            run_rows.append(row)
            completed_keys.add((instance, method))
            pairs, _ = update_all_csv(run_rows)
            state.update({"last_completed": f"{instance} {method}", "last_error": row.get("Error_Message", "")})
            write_progress(instance, method, "COMPLETED", row.get("Error_Message", ""), f"{instance} {method}")
        pairs, _ = update_all_csv(run_rows)
        write_summary(run_rows, pairs)
        write_progress(status="DONE")
        return 0
    except KeyboardInterrupt:
        write_progress(state.get("instance", ""), state.get("method", ""), "USER_STOPPED")
        return 130
    finally:
        stop_event.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", default=True)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-environment-failures", action="store_true")
    parser.add_argument("--force-rerun", action="store_true")
    parser.add_argument("--no-smoke-first", dest="smoke_first", action="store_false")
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--instance")
    parser.add_argument("--method", choices=["HCP", "ICP"])
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    parser.add_argument("--result-file")
    parser.add_argument("--time-limit", type=float)
    args = parser.parse_args()
    if args.child:
        return child_main(args)
    return run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
