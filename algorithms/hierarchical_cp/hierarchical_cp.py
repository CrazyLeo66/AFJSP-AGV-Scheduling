import argparse
import csv
import json
import math
import time
import traceback
from pathlib import Path

from ortools.sat.python import cp_model


LOC_WH = "L/U"
OUT_WH = "W"
TIME_SCALE = 2


# =============================================================================
# Basic utilities: kept compatible with the previous CP-SAT/MILP-style solver
# =============================================================================


def to_real_time(value):
    return round(float(value) / TIME_SCALE, 6)


def out_loc(name):
    return OUT_WH if name == LOC_WH else name


def op_name(op):
    if op is None:
        return None
    j, o = op
    return f"J{j}_O{o}"


def task_name(task):
    left = "Start" if task["source_op"] is None else op_name(task["source_op"])
    right = "End" if task["dest_op"] is None else op_name(task["dest_op"])
    return f"{left} -> {right}"


def scaled_time(distance, speed):
    return int(round(TIME_SCALE * float(distance) / float(speed)))


def scaled_proc_time(proc_time):
    return int(round(TIME_SCALE * float(proc_time)))


def real_loaded_time(data, src, dst):
    return float(data["Distance"][src][dst]) / float(data["Speeds"]["S_L"])


def real_empty_time(data, src, dst):
    return float(data["Distance"][src][dst]) / float(data["Speeds"]["S_U"])


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_instances(root, scales=None, suffix=None):
    root = Path(root)
    if scales:
        search_roots = [root / scale for scale in scales]
    else:
        search_roots = [root]

    for search_root in search_roots:
        if not search_root.exists():
            print(f"Warning: instance directory not found: {search_root}", flush=True)
            continue
        for path in sorted(search_root.rglob("*.json")):
            data = read_json(path)
            instance_name = data.get("Instance", path.stem)
            if suffix and not instance_name.endswith(suffix):
                continue
            yield path, data


def normalize_dag(data):
    """Return DAG as {child_job: [predecessor_jobs]} for all jobs.

    The old solver assumes data['DAG'] has all jobs as keys. This function is
    slightly more robust: missing jobs are treated as source jobs.
    """
    n_jobs = int(data.get("N_Jobs", len(data["Proc_Data"])))
    raw = data.get("DAG", {})
    dag = {}
    for j in range(1, n_jobs + 1):
        vals = raw.get(str(j), raw.get(j, []))
        dag[j] = [int(v) for v in vals]
    return dag


def source_jobs_from_dag(data):
    dag = normalize_dag(data)
    return [j for j, preds in dag.items() if not preds]


def root_jobs_from_data(data):
    if data.get("Root_Jobs"):
        return [int(j) for j in data["Root_Jobs"]]

    # Fallback: jobs that are not predecessors of any other job.
    dag = normalize_dag(data)
    pred_set = set()
    for preds in dag.values():
        pred_set.update(preds)
    return [j for j in dag if j not in pred_set]


def simple_horizon(data):
    proc = 0
    for job in data["Proc_Data"]:
        for op in job:
            proc += max(scaled_proc_time(p) for p in op.values())

    max_dist = max(float(v) for row in data["Distance"].values() for v in row.values())
    max_loaded = scaled_time(max_dist, data["Speeds"]["S_L"])
    max_empty = scaled_time(max_dist, data["Speeds"]["S_U"])

    dag = normalize_dag(data)
    transport_count = (
        int(data.get("Total_Ops", sum(len(job) for job in data["Proc_Data"])))
        + sum(len(v) for v in dag.values())
        + len(root_jobs_from_data(data))
    )
    return int(proc + transport_count * (max_loaded + max_empty + 10) + 100)


def build_tasks(data):
    """Build transport task list using the same structure as the previous solver.

    Task types:
      1) source job: warehouse -> first operation
      2) within each job: operation o -> operation o+1
      3) assembly: predecessor job's last operation -> child job's first operation
      4) final root job: last operation -> warehouse
    """
    tasks = []

    def add_task(name, source_op, dest_op, source_fixed=None, dest_fixed=None):
        tasks.append({
            "name": name,
            "source_op": source_op,
            "dest_op": dest_op,
            "source_fixed": source_fixed,
            "dest_fixed": dest_fixed,
        })

    dag = normalize_dag(data)
    source_jobs = source_jobs_from_dag(data)

    for j, job in enumerate(data["Proc_Data"], start=1):
        if j in source_jobs:
            add_task(f"in_j{j}", None, (j, 1), source_fixed=LOC_WH)
        for o in range(2, len(job) + 1):
            add_task(f"move_j{j}_o{o - 1}_to_o{o}", (j, o - 1), (j, o))

    for child, preds in dag.items():
        for pred in preds:
            pred_last = len(data["Proc_Data"][pred - 1])
            add_task(f"asm_j{pred}_to_j{child}", (pred, pred_last), (child, 1))

    for root_job in root_jobs_from_data(data):
        root_last = len(data["Proc_Data"][root_job - 1])
        add_task(f"out_j{root_job}", (root_job, root_last), None, dest_fixed=LOC_WH)

    return tasks


def solver_gap(solver, objective_scaled, status):
    raw_bound_scaled = solver.BestObjectiveBound()
    best_bound = round(float(raw_bound_scaled) / TIME_SCALE, 6)
    if status == cp_model.OPTIMAL:
        gap = 0.0
    else:
        gap = max(
            0.0,
            (float(objective_scaled) - float(raw_bound_scaled)) / max(1.0, abs(float(objective_scaled)))
        )
    return best_bound, round(gap, 8)


def count_model_vars(proto):
    num_vars = len(proto.variables)
    num_binary_vars = sum(1 for v in proto.variables if list(v.domain) == [0, 1])
    num_constraints = len(proto.constraints)
    return num_vars, num_binary_vars, num_constraints


def set_solver_params(solver, time_limit, workers, log=False):
    solver.parameters.max_time_in_seconds = float(time_limit)
    solver.parameters.num_search_workers = int(workers)
    solver.parameters.log_search_progress = bool(log)
    solver.parameters.cp_model_presolve = True
    solver.parameters.symmetry_level = 2
    solver.parameters.random_seed = 7
    # 0 is often faster for scheduling-heavy CP-SAT models; change to 1/2 if you need stronger bounds.
    solver.parameters.linearization_level = 0


# =============================================================================
# Phase 1: exact CP-SAT for AFJSP with assembly precedence, no AGV capacity
# =============================================================================


def build_phase1_afjsp(data, time_limit, workers, log=False):
    model = cp_model.CpModel()
    machines = [f"M{i}" for i in range(1, int(data["M_Count"]) + 1)]
    horizon = simple_horizon(data)

    op_start = {}
    op_end = {}
    op_x = {}
    machine_intervals = {m: [] for m in machines}

    for j, job in enumerate(data["Proc_Data"], start=1):
        for o, candidates in enumerate(job, start=1):
            s = model.NewIntVar(0, horizon, f"p1_s_j{j}_o{o}")
            e = model.NewIntVar(0, horizon, f"p1_e_j{j}_o{o}")
            op_start[j, o] = s
            op_end[j, o] = e
            choices = []
            for m, p in candidates.items():
                x = model.NewBoolVar(f"p1_x_j{j}_o{o}_{m}")
                op_x[j, o, m] = x
                interval = model.NewOptionalIntervalVar(
                    s, scaled_proc_time(p), e, x, f"p1_iv_j{j}_o{o}_{m}"
                )
                machine_intervals[m].append(interval)
                choices.append(x)
            model.AddExactlyOne(choices)

    # Machine capacity.
    for m in machines:
        model.AddNoOverlap(machine_intervals[m])

    # Within-job process precedence.
    for j, job in enumerate(data["Proc_Data"], start=1):
        for o in range(1, len(job)):
            model.Add(op_start[j, o + 1] >= op_end[j, o])

    # Assembly precedence: predecessor last operation must finish before child first operation starts.
    dag = normalize_dag(data)
    for child, preds in dag.items():
        for pred in preds:
            pred_last = len(data["Proc_Data"][pred - 1])
            model.Add(op_start[child, 1] >= op_end[pred, pred_last])

    cmax = model.NewIntVar(0, horizon, "P1_C_max")
    root_ends = []
    for root_job in root_jobs_from_data(data):
        root_last = len(data["Proc_Data"][root_job - 1])
        root_ends.append(op_end[root_job, root_last])
    model.AddMaxEquality(cmax, root_ends)
    model.Minimize(cmax)

    solver = cp_model.CpSolver()
    set_solver_params(solver, time_limit, workers, log=log)
    status = solver.Solve(model)
    proto = model.Proto()

    ctx = {
        "model": model,
        "solver": solver,
        "status": status,
        "status_name": solver.StatusName(status),
        "proto": proto,
        "machines": machines,
        "horizon": horizon,
        "op_start": op_start,
        "op_end": op_end,
        "op_x": op_x,
        "cmax": cmax,
    }
    return ctx


def extract_phase1_solution(data, ctx):
    solver = ctx["solver"]
    status = ctx["status"]
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    machine_of = {}
    proc_scaled = {}
    proc_real = {}
    op_start_val = {}
    op_end_val = {}

    for j, job in enumerate(data["Proc_Data"], start=1):
        for o, candidates in enumerate(job, start=1):
            chosen_machine = None
            chosen_proc = None
            for m, p in candidates.items():
                if solver.Value(ctx["op_x"][j, o, m]) == 1:
                    chosen_machine = m
                    chosen_proc = p
                    break
            if chosen_machine is None:
                raise RuntimeError(f"Phase 1 did not select a machine for J{j}_O{o}.")
            machine_of[j, o] = chosen_machine
            proc_real[j, o] = float(chosen_proc)
            proc_scaled[j, o] = scaled_proc_time(chosen_proc)
            op_start_val[j, o] = int(solver.Value(ctx["op_start"][j, o]))
            op_end_val[j, o] = int(solver.Value(ctx["op_end"][j, o]))

    machine_order = {m: [] for m in ctx["machines"]}
    for op, m in machine_of.items():
        machine_order[m].append(op)
    for m in machine_order:
        machine_order[m].sort(key=lambda op: (op_start_val[op], op_end_val[op], op[0], op[1]))

    p1_cmax_scaled = int(round(solver.Value(ctx["cmax"])))
    p1_best_bound, p1_gap = solver_gap(solver, p1_cmax_scaled, status)
    p1_vars, p1_bin, p1_cons = count_model_vars(ctx["proto"])

    return {
        "status": status,
        "status_name": ctx["status_name"],
        "solve_status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
        "cmax_scaled": p1_cmax_scaled,
        "cmax": to_real_time(p1_cmax_scaled),
        "best_bound": p1_best_bound,
        "gap": p1_gap,
        "wall_time": round(ctx["solver"].WallTime(), 6),
        "branches": ctx["solver"].NumBranches(),
        "conflicts": ctx["solver"].NumConflicts(),
        "num_vars": p1_vars,
        "num_binary_vars": p1_bin,
        "num_constraints": p1_cons,
        "machine_of": machine_of,
        "proc_real": proc_real,
        "proc_scaled": proc_scaled,
        "op_start": op_start_val,
        "op_end": op_end_val,
        "machine_order": machine_order,
    }


# =============================================================================
# Phase 2: exact CP-SAT for AGV scheduling with fixed machine assignment/order
# =============================================================================


def build_phase2_agv_fixed_order(data, phase1, time_limit, workers, log=False, phase2_mode="retime"):
    """Phase 2 model.

    phase2_mode='retime':
        Fix machine assignment and operation order only; re-optimize operation timing and AGV schedule.
        Recommended for robustness.

    phase2_mode='strict':
        Also fix operation start/end times from Phase 1. This is faster but often infeasible because
        Phase 1 ignored AGV capacity and travel time.
    """
    if phase2_mode not in ("retime", "strict"):
        raise ValueError("phase2_mode must be 'retime' or 'strict'.")

    model = cp_model.CpModel()
    machines = [f"M{i}" for i in range(1, int(data["M_Count"]) + 1)]
    locs = [LOC_WH] + machines
    loc_to_i = {name: i for i, name in enumerate(locs)}
    i_to_loc = {i: name for name, i in loc_to_i.items()}
    horizon = simple_horizon(data)

    op_start = {}
    op_end = {}
    fixed_machine = phase1["machine_of"]

    # Operation intervals are fixed to Phase-1 machine choices.
    for j, job in enumerate(data["Proc_Data"], start=1):
        for o, _ in enumerate(job, start=1):
            s = model.NewIntVar(0, horizon, f"p2_s_j{j}_o{o}")
            e = model.NewIntVar(0, horizon, f"p2_e_j{j}_o{o}")
            op_start[j, o] = s
            op_end[j, o] = e
            model.Add(e == s + phase1["proc_scaled"][j, o])
            if phase2_mode == "strict":
                model.Add(s == phase1["op_start"][j, o])
                model.Add(e == phase1["op_end"][j, o])

    # Preserve Phase-1 machine order. This replaces NoOverlap with deterministic precedence chains.
    for m in machines:
        order = phase1["machine_order"].get(m, [])
        for prev_op, next_op in zip(order, order[1:]):
            model.Add(op_start[next_op] >= op_end[prev_op])

    # Transport tasks with fixed origin/destination based on Phase-1 machine assignments.
    tasks = build_tasks(data)
    task_start = []
    task_end = []
    task_origin = []
    task_dest = []
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
        loaded_dur = scaled_time(data["Distance"][src_name][dst_name], data["Speeds"]["S_L"])
        has_agv_const = 1 if src_i != dst_i else 0

        s = model.NewIntVar(0, horizon, f"p2_ts_{task['name']}")
        e = model.NewIntVar(0, horizon, f"p2_te_{task['name']}")
        origin = model.NewIntVar(src_i, src_i, f"p2_orig_{task['name']}")
        dest = model.NewIntVar(dst_i, dst_i, f"p2_dest_{task['name']}")
        dur = model.NewIntVar(loaded_dur, loaded_dur, f"p2_dur_{task['name']}")
        has_agv = model.NewBoolVar(f"p2_has_agv_{task['name']}")
        model.Add(has_agv == has_agv_const)
        model.Add(e == s + dur)

        # Material availability and delivery constraints.
        if task["source_op"] is not None:
            model.Add(s >= op_end[task["source_op"]])
        if task["dest_op"] is not None:
            model.Add(op_start[task["dest_op"]] >= e)

        y_vars = []
        for a in range(int(data["AGV_Count"])):
            y = model.NewBoolVar(f"p2_y_{task['name']}_a{a + 1}")
            y_vars.append(y)
        model.Add(sum(y_vars) == has_agv)

        # Each AGV initially waits at warehouse. If it executes this as its first task, its pickup
        # cannot be earlier than empty travel from warehouse to this task's origin. This lower bound
        # is valid for every task assigned to any AGV.
        init_gap = scaled_time(data["Distance"][LOC_WH][src_name], data["Speeds"]["S_U"])
        if has_agv_const == 1:
            model.Add(s >= init_gap)

        task_start.append(s)
        task_end.append(e)
        task_origin.append(origin)
        task_dest.append(dest)
        task_dur.append(dur)
        task_has_agv.append(has_agv)
        task_y.append(y_vars)

    # Pairwise sequencing constraints on each AGV.
    # Because origin/destination are fixed in Phase 2, empty travel gaps are constants.
    for i in range(len(tasks)):
        src_i = locs[task_origin[i].Proto().domain[0]]
        dst_i = locs[task_dest[i].Proto().domain[0]]
        for j in range(i + 1, len(tasks)):
            src_j = locs[task_origin[j].Proto().domain[0]]
            dst_j = locs[task_dest[j].Proto().domain[0]]
            gap_ij = scaled_time(data["Distance"][dst_i][src_j], data["Speeds"]["S_U"])
            gap_ji = scaled_time(data["Distance"][dst_j][src_i], data["Speeds"]["S_U"])
            for a in range(int(data["AGV_Count"])):
                before = model.NewBoolVar(f"p2_before_t{i}_t{j}_a{a + 1}")
                model.Add(task_start[j] >= task_end[i] + gap_ij).OnlyEnforceIf([task_y[i][a], task_y[j][a], before])
                model.Add(task_start[i] >= task_end[j] + gap_ji).OnlyEnforceIf([task_y[i][a], task_y[j][a], before.Not()])

    cmax = model.NewIntVar(0, horizon, "P2_C_max")
    root_return_tasks = [i for i, t in enumerate(tasks) if t["name"].startswith("out_")]
    if root_return_tasks:
        model.AddMaxEquality(cmax, [task_end[i] for i in root_return_tasks])
    else:
        root_ends = []
        for root_job in root_jobs_from_data(data):
            root_last = len(data["Proc_Data"][root_job - 1])
            root_ends.append(op_end[root_job, root_last])
        model.AddMaxEquality(cmax, root_ends)
    model.Minimize(cmax)

    solver = cp_model.CpSolver()
    set_solver_params(solver, time_limit, workers, log=log)
    status = solver.Solve(model)
    proto = model.Proto()

    ctx = {
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
    return ctx


# =============================================================================
# Result extraction: same output structure as previous CP solver
# =============================================================================


def extract_results(path, data, phase1_ctx, phase1, phase2_ctx):
    if phase1 is None:
        return None

    solver = phase2_ctx["solver"]
    status = phase2_ctx["status"]
    status_name = phase2_ctx["status_name"]
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    p1_vars, p1_bin, p1_cons = count_model_vars(phase1_ctx["proto"])
    p2_vars, p2_bin, p2_cons = count_model_vars(phase2_ctx["proto"])

    cmax_scaled = int(round(solver.Value(phase2_ctx["cmax"])))
    cmax_val = to_real_time(cmax_scaled)
    best_bound, gap = solver_gap(solver, cmax_scaled, status)
    solve_status = "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE"

    ops_schedule = []
    for j, job in enumerate(data["Proc_Data"], start=1):
        for o, _ in enumerate(job, start=1):
            chosen_machine = phase1["machine_of"][j, o]
            proc_time = phase1["proc_real"][j, o]
            ops_schedule.append({
                "Operation": op_name((j, o)),
                "Machine": chosen_machine,
                "Sequence": None,
                "Start": to_real_time(solver.Value(phase2_ctx["op_start"][j, o])),
                "End": to_real_time(solver.Value(phase2_ctx["op_end"][j, o])),
                "Proc_Time": round(float(proc_time), 6),
            })

    ops_schedule.append({
        "Operation": "Start",
        "Machine": OUT_WH,
        "Sequence": None,
        "Start": 0.0,
        "End": 0.0,
        "Proc_Time": 0.0,
    })
    ops_schedule.append({
        "Operation": "End",
        "Machine": OUT_WH,
        "Sequence": None,
        "Start": cmax_val,
        "End": cmax_val,
        "Proc_Time": 0.0,
    })

    ops_schedule.sort(key=lambda row: (row["Machine"], row["Start"], row["End"], row["Operation"]))
    seq_counter = {}
    for row in ops_schedule:
        m = row["Machine"]
        seq_counter[m] = seq_counter.get(m, 0) + 1
        row["Sequence"] = seq_counter[m]

    agv_schedule_raw = {f"AGV{i + 1}": [] for i in range(int(data["AGV_Count"]))}
    total_loaded_time = 0.0
    active_agvs = set()

    for idx, task in enumerate(phase2_ctx["tasks"]):
        if solver.Value(phase2_ctx["task_has_agv"][idx]) == 0:
            continue
        agv_idx = next(a for a, y in enumerate(phase2_ctx["task_y"][idx]) if solver.Value(y) == 1)
        agv = f"AGV{agv_idx + 1}"
        active_agvs.add(agv)
        src = phase2_ctx["i_to_loc"][solver.Value(phase2_ctx["task_origin"][idx])]
        dst = phase2_ctx["i_to_loc"][solver.Value(phase2_ctx["task_dest"][idx])]
        loaded_dist = data["Distance"][src][dst]
        loaded_time = real_loaded_time(data, src, dst)
        total_loaded_time += loaded_time
        agv_schedule_raw[agv].append({
            "Task": task_name(task),
            "From_Machine": out_loc(src),
            "To_Machine": out_loc(dst),
            "PickUp_Time": to_real_time(solver.Value(phase2_ctx["task_start"][idx])),
            "DropOff_Time": to_real_time(solver.Value(phase2_ctx["task_end"][idx])),
            "Loaded_Dist": round(float(loaded_dist), 6),
            "Loaded_Time": round(float(loaded_time), 6),
        })

    agv_schedule_final = []
    total_empty_time = 0.0
    for agv in sorted(active_agvs):
        prev_loc = LOC_WH
        tasks_for_agv = sorted(
            agv_schedule_raw[agv],
            key=lambda row: (row["PickUp_Time"], row["DropOff_Time"], row["Task"])
        )
        for row in tasks_for_agv:
            curr_from = LOC_WH if row["From_Machine"] == OUT_WH else row["From_Machine"]
            empty_dist = data["Distance"][prev_loc][curr_from]
            empty_time = real_empty_time(data, prev_loc, curr_from)
            total_empty_time += empty_time
            row["Prev_Empty_Dist"] = round(float(empty_dist), 6)
            row["Prev_Empty_Time"] = round(float(empty_time), 6)
            row["AGV"] = agv
            agv_schedule_final.append(row)
            prev_loc = LOC_WH if row["To_Machine"] == OUT_WH else row["To_Machine"]

    total_wall_time = round(float(phase1["wall_time"]) + float(solver.WallTime()), 6)

    result = {
        "Meta_Data": {
            "Instance": data.get("Instance", Path(path).stem),
            "Scale": data.get("Scale", "Unknown"),
            "Solver": "Hierarchical OR-Tools CP-SAT",
            "Algorithm": "Two-Stage Exact Decomposition: AFJSP first, AGV second",
            "Phase2_Mode": phase2_ctx["phase2_mode"],
            "Solve_Status": solve_status,
            "Raw_CP_Status": status_name,
            "Time_Scale": TIME_SCALE,
            "CP_WORKERS": solver.parameters.num_search_workers,
            "LINEARIZATION_LEVEL": solver.parameters.linearization_level,
            "PRESOLVE": solver.parameters.cp_model_presolve,
            "SYMMETRY_LEVEL": solver.parameters.symmetry_level,
            "RANDOM_SEED": solver.parameters.random_seed,
            "Num_Integer_Vars": p1_vars + p2_vars,
            "Num_Continuous_Vars": 0,
            "Num_Binary_Vars": p1_bin + p2_bin,
            "Num_Constraints": p1_cons + p2_cons,
            "Branches": phase1["branches"] + solver.NumBranches(),
            "Conflicts": phase1["conflicts"] + solver.NumConflicts(),
            "Phase1_Solve_Status": phase1["solve_status"],
            "Phase1_Raw_CP_Status": phase1["status_name"],
            "Phase1_Objective_Cmax": phase1["cmax"],
            "Phase1_Best_Bound": phase1["best_bound"],
            "Phase1_Gap": phase1["gap"],
            "Phase1_Wall_Clock_Time_s": phase1["wall_time"],
            "Phase1_Num_Integer_Vars": p1_vars,
            "Phase1_Num_Binary_Vars": p1_bin,
            "Phase1_Num_Constraints": p1_cons,
            "Phase2_Wall_Clock_Time_s": round(solver.WallTime(), 6),
            "Phase2_Num_Integer_Vars": p2_vars,
            "Phase2_Num_Binary_Vars": p2_bin,
            "Phase2_Num_Constraints": p2_cons,
            "Bound_Note": "Best_Bound/MIP_Gap/CP_Gap are for the Phase-2 fixed-assignment/fixed-order subproblem, not a global bound for the fully integrated problem.",
        },
        "Metrics": {
            "Objective_Cmax": round(cmax_val, 6),
            "Best_Bound": round(best_bound, 6),
            "MIP_Gap": round(gap, 8),
            "CP_Gap": round(gap, 8),
            "Wall_Clock_Time_s": total_wall_time,
            "Total_Loaded_Trans_Time": round(total_loaded_time, 6),
            "Total_Empty_Trans_Time": round(total_empty_time, 6),
            "Total_Trans_Time": round(total_loaded_time + total_empty_time, 6),
            "Active_AGVs": len(active_agvs),
            "Total_AGVs": int(data["AGV_Count"]),
        },
        "Operations_Schedule": sorted(ops_schedule, key=lambda x: (x["Machine"], x["Start"], x["Sequence"])),
        "AGV_Schedule": sorted(agv_schedule_final, key=lambda x: (x["AGV"], x["PickUp_Time"], x["DropOff_Time"], x["Task"])),
    }
    return result


# =============================================================================
# Public API: same style as previous solver
# =============================================================================


def solve_instance(
    input_json_path,
    output_json_path=None,
    time_limit=180.0,
    workers=8,
    log=False,
    phase1_time_limit=None,
    phase2_time_limit=None,
    phase1_ratio=0.5,
    phase2_mode="retime",
):
    path = Path(input_json_path)
    data = read_json(path)

    if phase1_time_limit is None or phase2_time_limit is None:
        total = float(time_limit)
        p1_time = max(1.0, total * float(phase1_ratio))
        p2_time = max(1.0, total - p1_time)
    else:
        p1_time = float(phase1_time_limit)
        p2_time = float(phase2_time_limit)

    phase1_ctx = build_phase1_afjsp(data, time_limit=p1_time, workers=workers, log=log)
    phase1 = extract_phase1_solution(data, phase1_ctx)
    if phase1 is None:
        return None

    phase2_ctx = build_phase2_agv_fixed_order(
        data,
        phase1,
        time_limit=p2_time,
        workers=workers,
        log=log,
        phase2_mode=phase2_mode,
    )

    result = extract_results(path, data, phase1_ctx, phase1, phase2_ctx)
    if result and output_json_path:
        out = Path(output_json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=4, ensure_ascii=False), encoding="utf-8")
    return result


def summary_row(instance_name, raw_data, result):
    if result is None:
        return [
            instance_name,
            raw_data.get("Scale", "Unknown"),
            raw_data.get("N_Jobs", 0),
            raw_data.get("M_Count", 0),
            raw_data.get("AGV_Count", 0),
            "N/A", "N/A", "N/A", "FAILED_OR_INFEASIBLE",
        ] + ["N/A"] * 10

    return [
        instance_name,
        raw_data.get("Scale", "Unknown"),
        raw_data.get("N_Jobs", 0),
        raw_data.get("M_Count", 0),
        raw_data.get("AGV_Count", 0),
        result["Meta_Data"]["Num_Binary_Vars"],
        result["Meta_Data"]["Num_Continuous_Vars"],
        result["Meta_Data"]["Num_Constraints"],
        result["Meta_Data"]["Solve_Status"],
        result["Metrics"]["Objective_Cmax"],
        result["Metrics"]["Best_Bound"],
        result["Metrics"]["MIP_Gap"],
        result["Metrics"]["Wall_Clock_Time_s"],
        result["Metrics"]["Total_Trans_Time"],
        result["Metrics"]["Total_Loaded_Trans_Time"],
        result["Metrics"]["Total_Empty_Trans_Time"],
        result["Meta_Data"]["Phase1_Solve_Status"],
        result["Meta_Data"]["Phase1_Objective_Cmax"],
        result["Meta_Data"]["Phase1_Wall_Clock_Time_s"],
    ]


def run_batch(
    input_root,
    output_detail_root,
    summary_csv_path,
    time_limit,
    workers,
    only=None,
    scales=None,
    suffix=None,
    log=False,
    phase1_time_limit=None,
    phase2_time_limit=None,
    phase1_ratio=0.5,
    phase2_mode="retime",
):
    headers = [
        "Instance_Name", "Scale", "N_Jobs", "M_Count", "AGV_Count",
        "Vars_Binary", "Vars_Continuous", "Constraints",
        "Solve_Status", "Cmax", "Best_Bound", "MIP_Gap", "Wall_Clock_Time_s",
        "Total_Trans_Time", "Loaded_Trans_Time", "Empty_Trans_Time",
        "Phase1_Status", "Phase1_Cmax", "Phase1_Wall_Clock_Time_s",
    ]
    input_root = Path(input_root)
    output_detail_root = Path(output_detail_root)
    summary_csv_path = Path(summary_csv_path)
    output_detail_root.mkdir(parents=True, exist_ok=True)
    summary_csv_path.parent.mkdir(parents=True, exist_ok=True)

    with summary_csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        success = 0
        failed = 0

        for input_path, raw_data in iter_instances(input_root, scales=scales, suffix=suffix):
            instance_name = raw_data.get("Instance", input_path.stem)
            if only and only not in instance_name:
                continue
            rel_dir = input_path.parent.relative_to(input_root)
            output_json_path = output_detail_root / rel_dir / f"{instance_name}_result.json"

            print(f"\n>>> Solving {instance_name} ...", flush=True)
            try:
                result = solve_instance(
                    input_path,
                    output_json_path,
                    time_limit=time_limit,
                    workers=workers,
                    log=log,
                    phase1_time_limit=phase1_time_limit,
                    phase2_time_limit=phase2_time_limit,
                    phase1_ratio=phase1_ratio,
                    phase2_mode=phase2_mode,
                )
                writer.writerow(summary_row(instance_name, raw_data, result))
                f.flush()
                if result:
                    success += 1
                    print(
                        f"OK {instance_name}: status={result['Meta_Data']['Solve_Status']}, "
                        f"Cmax={result['Metrics']['Objective_Cmax']}, "
                        f"Gap={result['Metrics']['MIP_Gap']}, "
                        f"WallTime={result['Metrics']['Wall_Clock_Time_s']}s, "
                        f"P1={result['Meta_Data']['Phase1_Solve_Status']}",
                        flush=True,
                    )
                else:
                    failed += 1
                    print(f"FAILED {instance_name}: no feasible solution.", flush=True)
            except Exception as exc:
                failed += 1
                traceback.print_exc()
                writer.writerow(summary_row(instance_name, raw_data, None))
                f.flush()
                print(f"ERROR {instance_name}: {exc}", flush=True)

    print(f"\nBatch finished. Success: {success}, Failed: {failed}")
    print(f"Summary CSV: {summary_csv_path.resolve()}")
    print(f"Detail JSON root: {output_detail_root.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="Hierarchical CP-SAT solver with MILP-style JSON outputs: Phase 1 AFJSP, Phase 2 AGV."
    )
    parser.add_argument("--root", default="instances/main_51")
    parser.add_argument("--input", help="Solve a single JSON instance instead of a root directory.")
    parser.add_argument("--details-dir", default="Results_HierCP_SAT_Details")
    parser.add_argument("--summary", default="HierCP_SAT_Summary_Results.csv")
    parser.add_argument("--time-limit", type=float, default=180.0,
                        help="Total time limit. Used only when phase-specific limits are not provided.")
    parser.add_argument("--phase1-time-limit", type=float,
                        help="Explicit Phase-1 time limit. If set, also set --phase2-time-limit.")
    parser.add_argument("--phase2-time-limit", type=float,
                        help="Explicit Phase-2 time limit. If set, also set --phase1-time-limit.")
    parser.add_argument("--phase1-ratio", type=float, default=0.5,
                        help="When using --time-limit only, assign this ratio to Phase 1 and the rest to Phase 2.")
    parser.add_argument("--phase2-mode", choices=["retime", "strict"], default="retime",
                        help="retime fixes machine assignment/order and re-times operations; strict also fixes Phase-1 operation times.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--only", help="Substring filter for instance name.")
    parser.add_argument("--scales", nargs="*", help="Scale directory names to include, e.g. Small Medium Large \"Very Large\".")
    parser.add_argument("--suffix", help="Exact instance-name suffix filter, e.g. V1.")
    parser.add_argument("--log", action="store_true")
    args = parser.parse_args()

    if (args.phase1_time_limit is None) != (args.phase2_time_limit is None):
        raise ValueError("Set both --phase1-time-limit and --phase2-time-limit, or set neither.")

    if args.input:
        input_path = Path(args.input)
        raw_data = read_json(input_path)
        instance_name = raw_data.get("Instance", input_path.stem)
        output_json = Path(args.details_dir) / f"{instance_name}_result.json"
        result = solve_instance(
            input_path,
            output_json,
            time_limit=args.time_limit,
            workers=args.workers,
            log=args.log,
            phase1_time_limit=args.phase1_time_limit,
            phase2_time_limit=args.phase2_time_limit,
            phase1_ratio=args.phase1_ratio,
            phase2_mode=args.phase2_mode,
        )
        if result:
            print(json.dumps({
                "Instance": instance_name,
                "Status": result["Meta_Data"]["Solve_Status"],
                "Cmax": result["Metrics"]["Objective_Cmax"],
                "Best_Bound": result["Metrics"]["Best_Bound"],
                "MIP_Gap": result["Metrics"]["MIP_Gap"],
                "Wall_Clock_Time_s": result["Metrics"]["Wall_Clock_Time_s"],
                "Phase1_Status": result["Meta_Data"]["Phase1_Solve_Status"],
                "Phase1_Cmax": result["Meta_Data"]["Phase1_Objective_Cmax"],
                "Phase2_Mode": result["Meta_Data"]["Phase2_Mode"],
                "Detail_JSON": str(output_json.resolve()),
            }, indent=4, ensure_ascii=False))
        else:
            print(f"No feasible solution for {instance_name}.")
        return

    run_batch(
        input_root=args.root,
        output_detail_root=args.details_dir,
        summary_csv_path=args.summary,
        time_limit=args.time_limit,
        workers=args.workers,
        only=args.only,
        scales=args.scales,
        suffix=args.suffix,
        log=args.log,
        phase1_time_limit=args.phase1_time_limit,
        phase2_time_limit=args.phase2_time_limit,
        phase1_ratio=args.phase1_ratio,
        phase2_mode=args.phase2_mode,
    )


if __name__ == "__main__":
    main()
