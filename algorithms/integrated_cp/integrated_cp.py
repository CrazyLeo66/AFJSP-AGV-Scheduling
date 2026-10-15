import argparse
import csv
import json
import math
import os
import traceback
from pathlib import Path

from ortools.sat.python import cp_model


LOC_WH = "L/U"
OUT_WH = "W"
TIME_SCALE = 2


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
    return int(round(TIME_SCALE * distance / speed))


def real_loaded_time(data, src, dst):
    return data["Distance"][src][dst] / data["Speeds"]["S_L"]


def real_empty_time(data, src, dst):
    return data["Distance"][src][dst] / data["Speeds"]["S_U"]


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


def simple_horizon(data):
    proc = 0
    for job in data["Proc_Data"]:
        for op in job:
            proc += max(op.values()) * TIME_SCALE
    max_dist = max(v for row in data["Distance"].values() for v in row.values())
    transport_count = data["Total_Ops"] + sum(len(v) for v in data["DAG"].values()) + len(data["Root_Jobs"])
    return proc + transport_count * max_dist * TIME_SCALE * 3 + 100


def build_and_solve(data, time_limit, workers, log=False):
    model = cp_model.CpModel()
    machines = [f"M{i}" for i in range(1, data["M_Count"] + 1)]
    locs = [LOC_WH] + machines
    loc_to_i = {name: i for i, name in enumerate(locs)}
    i_to_loc = {i: name for name, i in loc_to_i.items()}
    horizon = simple_horizon(data)

    loaded_table = []
    empty_table = []
    for a in locs:
        for b in locs:
            loaded_table.append([loc_to_i[a], loc_to_i[b], scaled_time(data["Distance"][a][b], data["Speeds"]["S_L"])])
            empty_table.append([loc_to_i[a], loc_to_i[b], scaled_time(data["Distance"][a][b], data["Speeds"]["S_U"])])

    op_start = {}
    op_end = {}
    op_x = {}
    machine_intervals = {m: [] for m in machines}

    for j, job in enumerate(data["Proc_Data"], start=1):
        for o, candidates in enumerate(job, start=1):
            s = model.NewIntVar(0, horizon, f"s_j{j}_o{o}")
            e = model.NewIntVar(0, horizon, f"e_j{j}_o{o}")
            op_start[j, o] = s
            op_end[j, o] = e
            choices = []
            for m, p in candidates.items():
                x = model.NewBoolVar(f"x_j{j}_o{o}_{m}")
                op_x[j, o, m] = x
                interval = model.NewOptionalIntervalVar(s, p * TIME_SCALE, e, x, f"iv_j{j}_o{o}_{m}")
                machine_intervals[m].append(interval)
                choices.append(x)
            model.AddExactlyOne(choices)

    for m in machines:
        model.AddNoOverlap(machine_intervals[m])

    tasks = []

    def add_task(name, source_op, dest_op, source_fixed=None, dest_fixed=None):
        tasks.append({
            "name": name,
            "source_op": source_op,
            "dest_op": dest_op,
            "source_fixed": source_fixed,
            "dest_fixed": dest_fixed,
        })

    dag = {int(k): [int(v) for v in vals] for k, vals in data["DAG"].items()}
    source_jobs = [j for j, preds in dag.items() if not preds]

    for j, job in enumerate(data["Proc_Data"], start=1):
        if j in source_jobs:
            add_task(f"in_j{j}", None, (j, 1), source_fixed=LOC_WH)
        for o in range(2, len(job) + 1):
            add_task(f"move_j{j}_o{o - 1}_to_o{o}", (j, o - 1), (j, o))

    for child, preds in dag.items():
        for pred in preds:
            pred_last = len(data["Proc_Data"][pred - 1])
            add_task(f"asm_j{pred}_to_j{child}", (pred, pred_last), (child, 1))

    for root_job in data["Root_Jobs"]:
        root_last = len(data["Proc_Data"][root_job - 1])
        add_task(f"out_j{root_job}", (root_job, root_last), None, dest_fixed=LOC_WH)

    task_start = []
    task_end = []
    task_origin = []
    task_dest = []
    task_dur = []
    task_has_agv = []
    task_y = []

    init_empty_table = [[loc_to_i[b], scaled_time(data["Distance"][LOC_WH][b], data["Speeds"]["S_U"])] for b in locs]

    for task in tasks:
        s = model.NewIntVar(0, horizon, f"ts_{task['name']}")
        e = model.NewIntVar(0, horizon, f"te_{task['name']}")
        origin = model.NewIntVar(0, len(locs) - 1, f"orig_{task['name']}")
        dest = model.NewIntVar(0, len(locs) - 1, f"dest_{task['name']}")
        dur = model.NewIntVar(0, horizon, f"dur_{task['name']}")
        has_agv = model.NewBoolVar(f"has_agv_{task['name']}")
        modes = []
        nonzero_modes = []

        if task["source_fixed"] is not None:
            source_options = [(task["source_fixed"], None)]
        else:
            sj, so = task["source_op"]
            source_options = [(m, op_x[sj, so, m]) for m in data["Proc_Data"][sj - 1][so - 1]]

        if task["dest_fixed"] is not None:
            dest_options = [(task["dest_fixed"], None)]
        else:
            dj, do = task["dest_op"]
            dest_options = [(m, op_x[dj, do, m]) for m in data["Proc_Data"][dj - 1][do - 1]]

        for src_name, src_x in source_options:
            for dst_name, dst_x in dest_options:
                mode = model.NewBoolVar(f"mode_{task['name']}_{src_name}_{dst_name}")
                modes.append(mode)
                if src_x is not None and dst_x is not None:
                    model.Add(mode <= src_x)
                    model.Add(mode <= dst_x)
                    model.Add(mode >= src_x + dst_x - 1)
                elif src_x is not None:
                    model.Add(mode == src_x)
                elif dst_x is not None:
                    model.Add(mode == dst_x)
                else:
                    model.Add(mode == 1)

                src_i = loc_to_i[src_name]
                dst_i = loc_to_i[dst_name]
                model.Add(origin == src_i).OnlyEnforceIf(mode)
                model.Add(dest == dst_i).OnlyEnforceIf(mode)
                if src_i != dst_i:
                    nonzero_modes.append(mode)

        model.AddExactlyOne(modes)
        if nonzero_modes:
            model.Add(has_agv == sum(nonzero_modes))
        else:
            model.Add(has_agv == 0)

        model.AddAllowedAssignments([origin, dest, dur], loaded_table)
        model.Add(e == s + dur)

        if task["source_op"] is not None:
            model.Add(s >= op_end[task["source_op"]])
        if task["dest_op"] is not None:
            model.Add(op_start[task["dest_op"]] >= e)

        y_vars = []
        for a in range(data["AGV_Count"]):
            y = model.NewBoolVar(f"y_{task['name']}_a{a + 1}")
            y_vars.append(y)
        model.Add(sum(y_vars) == has_agv)

        init_gap = model.NewIntVar(0, horizon, f"init_gap_{task['name']}")
        model.AddAllowedAssignments([origin, init_gap], init_empty_table)
        model.Add(s >= init_gap).OnlyEnforceIf(has_agv)

        task_start.append(s)
        task_end.append(e)
        task_origin.append(origin)
        task_dest.append(dest)
        task_dur.append(dur)
        task_has_agv.append(has_agv)
        task_y.append(y_vars)

    for i in range(len(tasks)):
        for j in range(i + 1, len(tasks)):
            gap_ij = model.NewIntVar(0, horizon, f"gap_{i}_to_{j}")
            gap_ji = model.NewIntVar(0, horizon, f"gap_{j}_to_{i}")
            model.AddAllowedAssignments([task_dest[i], task_origin[j], gap_ij], empty_table)
            model.AddAllowedAssignments([task_dest[j], task_origin[i], gap_ji], empty_table)
            for a in range(data["AGV_Count"]):
                before = model.NewBoolVar(f"before_t{i}_t{j}_a{a + 1}")
                model.Add(task_start[j] >= task_end[i] + gap_ij).OnlyEnforceIf([task_y[i][a], task_y[j][a], before])
                model.Add(task_start[i] >= task_end[j] + gap_ji).OnlyEnforceIf([task_y[i][a], task_y[j][a], before.Not()])

    cmax = model.NewIntVar(0, horizon, "C_max")
    root_return_tasks = [i for i, t in enumerate(tasks) if t["name"].startswith("out_")]
    model.AddMaxEquality(cmax, [task_end[i] for i in root_return_tasks])
    model.Minimize(cmax)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = workers
    solver.parameters.log_search_progress = log
    solver.parameters.cp_model_presolve = True
    solver.parameters.symmetry_level = 2
    solver.parameters.random_seed = 7

    status = solver.Solve(model)
    proto = model.Proto()

    context = {
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
        "op_x": op_x,
        "task_start": task_start,
        "task_end": task_end,
        "task_origin": task_origin,
        "task_dest": task_dest,
        "task_dur": task_dur,
        "task_has_agv": task_has_agv,
        "task_y": task_y,
        "cmax": cmax,
    }
    return context


def extract_results(path, data, ctx):
    solver = ctx["solver"]
    status = ctx["status"]
    status_name = ctx["status_name"]
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    proto = ctx["proto"]
    num_vars = len(proto.variables)
    num_binary_vars = sum(1 for v in proto.variables if list(v.domain) == [0, 1])
    num_constraints = len(proto.constraints)

    cmax_scaled = int(round(solver.Value(ctx["cmax"])))
    cmax_val = to_real_time(cmax_scaled)
    raw_bound_scaled = solver.BestObjectiveBound()
    best_bound = round(float(raw_bound_scaled) / TIME_SCALE, 6)
    if status == cp_model.OPTIMAL:
        gap = 0.0
        solve_status = "OPTIMAL"
    else:
        gap = max(0.0, (float(cmax_scaled) - float(raw_bound_scaled)) / max(1.0, abs(float(cmax_scaled))))
        solve_status = "FEASIBLE"

    ops_schedule = []
    for j, job in enumerate(data["Proc_Data"], start=1):
        for o, candidates in enumerate(job, start=1):
            chosen_machine = None
            proc_time = None
            for m, p in candidates.items():
                if solver.Value(ctx["op_x"][j, o, m]) == 1:
                    chosen_machine = m
                    proc_time = p
                    break
            ops_schedule.append({
                "Operation": op_name((j, o)),
                "Machine": chosen_machine,
                "Sequence": None,
                "Start": to_real_time(solver.Value(ctx["op_start"][j, o])),
                "End": to_real_time(solver.Value(ctx["op_end"][j, o])),
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

    agv_schedule_raw = {f"AGV{i + 1}": [] for i in range(data["AGV_Count"])}
    total_loaded_time = 0.0
    active_agvs = set()

    for idx, task in enumerate(ctx["tasks"]):
        if solver.Value(ctx["task_has_agv"][idx]) == 0:
            continue
        agv_idx = next(a for a, y in enumerate(ctx["task_y"][idx]) if solver.Value(y) == 1)
        agv = f"AGV{agv_idx + 1}"
        active_agvs.add(agv)
        src = ctx["i_to_loc"][solver.Value(ctx["task_origin"][idx])]
        dst = ctx["i_to_loc"][solver.Value(ctx["task_dest"][idx])]
        loaded_dist = data["Distance"][src][dst]
        loaded_time = real_loaded_time(data, src, dst)
        total_loaded_time += loaded_time
        agv_schedule_raw[agv].append({
            "Task": task_name(task),
            "From_Machine": out_loc(src),
            "To_Machine": out_loc(dst),
            "PickUp_Time": to_real_time(solver.Value(ctx["task_start"][idx])),
            "DropOff_Time": to_real_time(solver.Value(ctx["task_end"][idx])),
            "Loaded_Dist": round(float(loaded_dist), 6),
            "Loaded_Time": round(float(loaded_time), 6),
        })

    agv_schedule_final = []
    total_empty_time = 0.0
    for agv in sorted(active_agvs):
        prev_loc = LOC_WH
        tasks_for_agv = sorted(agv_schedule_raw[agv], key=lambda row: (row["PickUp_Time"], row["DropOff_Time"], row["Task"]))
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

    result = {
        "Meta_Data": {
            "Instance": data.get("Instance", Path(path).stem),
            "Scale": data.get("Scale", "Unknown"),
            "Solver": "OR-Tools CP-SAT",
            "Solve_Status": solve_status,
            "Raw_CP_Status": status_name,
            "Time_Scale": TIME_SCALE,
            "CP_WORKERS": solver.parameters.num_search_workers,
            "LINEARIZATION_LEVEL": solver.parameters.linearization_level,
            "PRESOLVE": solver.parameters.cp_model_presolve,
            "SYMMETRY_LEVEL": solver.parameters.symmetry_level,
            "RANDOM_SEED": solver.parameters.random_seed,
            "Num_Integer_Vars": num_vars,
            "Num_Continuous_Vars": 0,
            "Num_Binary_Vars": num_binary_vars,
            "Num_Constraints": num_constraints,
            "Branches": solver.NumBranches(),
            "Conflicts": solver.NumConflicts(),
        },
        "Metrics": {
            "Objective_Cmax": round(cmax_val, 6),
            "Best_Bound": round(best_bound, 6),
            "MIP_Gap": round(gap, 8),
            "CP_Gap": round(gap, 8),
            "Wall_Clock_Time_s": round(solver.WallTime(), 6),
            "Total_Loaded_Trans_Time": round(total_loaded_time, 6),
            "Total_Empty_Trans_Time": round(total_empty_time, 6),
            "Total_Trans_Time": round(total_loaded_time + total_empty_time, 6),
            "Active_AGVs": len(active_agvs),
            "Total_AGVs": data["AGV_Count"],
        },
        "Operations_Schedule": sorted(ops_schedule, key=lambda x: (x["Machine"], x["Start"], x["Sequence"])),
        "AGV_Schedule": sorted(agv_schedule_final, key=lambda x: (x["AGV"], x["PickUp_Time"], x["DropOff_Time"], x["Task"])),
    }
    return result


def solve_instance(input_json_path, output_json_path=None, time_limit=180.0, workers=8, log=False):
    path = Path(input_json_path)
    data = read_json(path)
    ctx = build_and_solve(data, time_limit=time_limit, workers=workers, log=log)
    result = extract_results(path, data, ctx)
    if result and output_json_path:
        out = Path(output_json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=4, ensure_ascii=False), encoding="utf-8")
    return result


def summary_row(instance_name, raw_data, result):
    if result is None:
        return [
            instance_name, raw_data.get("Scale", "Unknown"), raw_data.get("N_Jobs", 0),
            raw_data.get("M_Count", 0), raw_data.get("AGV_Count", 0),
            "N/A", "N/A", "N/A", "FAILED_OR_INFEASIBLE",
        ] + ["N/A"] * 7

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
    ]


def run_batch(input_root, output_detail_root, summary_csv_path, time_limit, workers, only=None, scales=None, suffix=None, log=False):
    headers = [
        "Instance_Name", "Scale", "N_Jobs", "M_Count", "AGV_Count",
        "Vars_Binary", "Vars_Continuous", "Constraints",
        "Solve_Status", "Cmax", "Best_Bound", "MIP_Gap", "Wall_Clock_Time_s",
        "Total_Trans_Time", "Loaded_Trans_Time", "Empty_Trans_Time",
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
                result = solve_instance(input_path, output_json_path, time_limit=time_limit, workers=workers, log=log)
                writer.writerow(summary_row(instance_name, raw_data, result))
                f.flush()
                if result:
                    success += 1
                    print(
                        f"OK {instance_name}: status={result['Meta_Data']['Solve_Status']}, "
                        f"Cmax={result['Metrics']['Objective_Cmax']}, "
                        f"Gap={result['Metrics']['MIP_Gap']}, "
                        f"WallTime={result['Metrics']['Wall_Clock_Time_s']}s",
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
    parser = argparse.ArgumentParser(description="Compact CP-SAT solver with MILP-style JSON outputs.")
    parser.add_argument("--root", default="instances/main_51")
    parser.add_argument("--input", help="Solve a single JSON instance instead of a root directory.")
    parser.add_argument("--details-dir", default="Results_CP_SAT_Details")
    parser.add_argument("--summary", default="CP_SAT_Summary_Results.csv")
    parser.add_argument("--time-limit", type=float, default=180.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--only", help="Substring filter for instance name.")
    parser.add_argument("--scales", nargs="*", help="Scale directory names to include, e.g. Small Medium Large \"Very Large\".")
    parser.add_argument("--suffix", help="Exact instance-name suffix filter, e.g. V1.")
    parser.add_argument("--log", action="store_true")
    args = parser.parse_args()

    if args.input:
        input_path = Path(args.input)
        raw_data = read_json(input_path)
        instance_name = raw_data.get("Instance", input_path.stem)
        output_json = Path(args.details_dir) / f"{instance_name}_result.json"
        result = solve_instance(input_path, output_json, time_limit=args.time_limit, workers=args.workers, log=args.log)
        if result:
            print(json.dumps({
                "Instance": instance_name,
                "Status": result["Meta_Data"]["Solve_Status"],
                "Cmax": result["Metrics"]["Objective_Cmax"],
                "Best_Bound": result["Metrics"]["Best_Bound"],
                "MIP_Gap": result["Metrics"]["MIP_Gap"],
                "Wall_Clock_Time_s": result["Metrics"]["Wall_Clock_Time_s"],
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
    )


if __name__ == "__main__":
    main()
