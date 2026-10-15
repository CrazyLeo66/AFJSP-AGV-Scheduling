import argparse
import json
import time
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB


WAREHOUSE = "L/U"


def op_key(job_id, op_id):
    return (int(job_id), int(op_id))


def op_name(op):
    return f"J{op[0]}O{op[1]}"


def read_instance(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    jobs = list(range(1, data["N_Jobs"] + 1))
    machines = [f"M{i}" for i in range(1, data["M_Count"] + 1)]
    locations = [WAREHOUSE] + machines

    proc = {}
    ops_by_job = {}
    for j, job_ops in enumerate(data["Proc_Data"], start=1):
        ops_by_job[j] = []
        for k, machine_times in enumerate(job_ops, start=1):
            o = op_key(j, k)
            ops_by_job[j].append(o)
            proc[o] = {m: float(t) for m, t in machine_times.items()}

    predecessors = {int(j): [int(p) for p in preds] for j, preds in data["DAG"].items()}
    for j in jobs:
        predecessors.setdefault(j, [])

    children = {j: [] for j in jobs}
    for child, preds in predecessors.items():
        for pred in preds:
            children[pred].append(child)

    root_jobs = [int(j) for j in data["Root_Jobs"]]

    return {
        "raw": data,
        "jobs": jobs,
        "machines": machines,
        "locations": locations,
        "ops_by_job": ops_by_job,
        "operations": [o for j in jobs for o in ops_by_job[j]],
        "proc": proc,
        "predecessors": predecessors,
        "children": children,
        "root_jobs": root_jobs,
        "distance": data["Distance"],
        "loaded_speed": float(data["Speeds"]["S_L"]),
        "unloaded_speed": float(data["Speeds"]["S_U"]),
        "agvs": list(range(1, data["AGV_Count"] + 1)),
    }


def build_transport_tasks(inst):
    tasks = []
    preds = inst["predecessors"]
    ops_by_job = inst["ops_by_job"]

    def add(kind, source_op=None, dest_op=None, source_fixed=None, dest_fixed=None, release_op=None, delivers_op=None):
        idx = len(tasks) + 1
        if kind == "raw":
            tid = f"T{idx:02d}_raw_to_{op_name(dest_op)}"
        elif kind == "internal":
            tid = f"T{idx:02d}_{op_name(source_op)}_to_{op_name(dest_op)}"
        elif kind == "assembly":
            tid = f"T{idx:02d}_asm_{op_name(source_op)}_to_{op_name(dest_op)}"
        elif kind == "return":
            tid = f"T{idx:02d}_{op_name(source_op)}_to_WH"
        else:
            tid = f"T{idx:02d}_{kind}"
        tasks.append(
            {
                "id": tid,
                "kind": kind,
                "source_op": source_op,
                "dest_op": dest_op,
                "source_fixed": source_fixed,
                "dest_fixed": dest_fixed,
                "release_op": release_op,
                "delivers_op": delivers_op,
            }
        )

    for j in inst["jobs"]:
        first = ops_by_job[j][0]
        if not preds[j]:
            add("raw", source_fixed=WAREHOUSE, dest_op=first, delivers_op=first)

    for j in inst["jobs"]:
        ops = ops_by_job[j]
        for prev_op, next_op in zip(ops[:-1], ops[1:]):
            add("internal", source_op=prev_op, dest_op=next_op, release_op=prev_op, delivers_op=next_op)

    for child, pred_jobs in preds.items():
        child_first = ops_by_job[child][0]
        for pred in pred_jobs:
            pred_last = ops_by_job[pred][-1]
            add("assembly", source_op=pred_last, dest_op=child_first, release_op=pred_last, delivers_op=child_first)

    for j in inst["root_jobs"]:
        last = ops_by_job[j][-1]
        add("return", source_op=last, dest_fixed=WAREHOUSE, release_op=last)

    return tasks


def safe_name(name):
    return name.replace("/", "")


def solve(instance_path, output_path, time_limit, mip_gap, log_to_console, write_lp=True, print_result=True):
    wall_start = time.perf_counter()
    inst = read_instance(instance_path)
    tasks = build_transport_tasks(inst)
    locations = inst["locations"]
    machines = inst["machines"]
    operations = inst["operations"]
    agvs = inst["agvs"]
    proc = inst["proc"]
    dist = inst["distance"]

    def loaded_time(a, b):
        return float(dist[a][b]) / inst["loaded_speed"]

    def empty_time(a, b):
        return float(dist[a][b]) / inst["unloaded_speed"]

    max_proc_sum = sum(max(proc[o].values()) for o in operations)
    max_loaded = max(loaded_time(a, b) for a in locations for b in locations)
    max_empty = max(empty_time(a, b) for a in locations for b in locations)
    horizon = max_proc_sum + len(tasks) * max_loaded + len(tasks) * max_empty + 1

    model = gp.Model("AFJSP_AGV_Assembly_MILP_PDF_TightM")
    model.Params.TimeLimit = time_limit
    model.Params.MIPGap = mip_gap
    model.Params.LogToConsole = 1 if log_to_console else 0

    x = {}
    for o in operations:
        for m in proc[o]:
            x[o, m] = model.addVar(vtype=GRB.BINARY, name=f"x_{op_name(o)}_{m}")

    s = {o: model.addVar(lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name=f"S_{op_name(o)}") for o in operations}
    c = {o: model.addVar(lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name=f"C_{op_name(o)}") for o in operations}

    ts = {t["id"]: model.addVar(lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name=f"TS_{t['id']}") for t in tasks}
    te = {t["id"]: model.addVar(lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name=f"TE_{t['id']}") for t in tasks}
    need_agv = {t["id"]: model.addVar(vtype=GRB.BINARY, name=f"need_agv_{t['id']}") for t in tasks}
    alpha = {(t["id"], r): model.addVar(vtype=GRB.BINARY, name=f"a_{t['id']}_V{r}") for t in tasks for r in agvs}
    pair_loaded = {
        (t["id"], a, b): model.addVar(vtype=GRB.BINARY, name=safe_name(f"load_{t['id']}_{a}_{b}"))
        for t in tasks
        for a in locations
        for b in locations
    }
    y = {
        (ta["id"], tb["id"], r): model.addVar(vtype=GRB.BINARY, name=f"y_{ta['id']}_before_{tb['id']}_V{r}")
        for ta in tasks
        for tb in tasks
        if ta["id"] != tb["id"]
        for r in agvs
    }
    cmax = model.addVar(lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name="Cmax")

    model.update()

    def assign_expr(o, loc):
        if loc == WAREHOUSE:
            return 0.0
        return x[o, loc] if (o, loc) in x else 0.0

    def src_expr(t, loc):
        if t["source_fixed"] is not None:
            return 1.0 if loc == t["source_fixed"] else 0.0
        return assign_expr(t["source_op"], loc)

    def dst_expr(t, loc):
        if t["dest_fixed"] is not None:
            return 1.0 if loc == t["dest_fixed"] else 0.0
        return assign_expr(t["dest_op"], loc)

    for o in operations:
        model.addConstr(gp.quicksum(x[o, m] for m in proc[o]) == 1, name=f"assign_{op_name(o)}")
        model.addConstr(c[o] == s[o] + gp.quicksum(proc[o][m] * x[o, m] for m in proc[o]), name=f"dur_{op_name(o)}")
        model.addConstr(cmax >= c[o], name=f"cmax_proc_{op_name(o)}")

    for t in tasks:
        tid = t["id"]

        for a in locations:
            for b in locations:
                w = pair_loaded[tid, a, b]
                model.addConstr(w <= src_expr(t, a), name=safe_name(f"load_src_{tid}_{a}_{b}"))
                model.addConstr(w <= dst_expr(t, b), name=safe_name(f"load_dst_{tid}_{a}_{b}"))
                model.addConstr(w >= src_expr(t, a) + dst_expr(t, b) - 1, name=safe_name(f"load_pair_{tid}_{a}_{b}"))

        model.addConstr(
            gp.quicksum(pair_loaded[tid, a, b] for a in locations for b in locations) == 1,
            name=f"one_load_pair_{tid}",
        )
        model.addConstr(
            need_agv[tid] == gp.quicksum(pair_loaded[tid, a, b] for a in locations for b in locations if a != b),
            name=f"need_agv_{tid}",
        )
        model.addConstr(gp.quicksum(alpha[tid, r] for r in agvs) == need_agv[tid], name=f"agv_assign_{tid}")
        model.addConstr(
            te[tid] == ts[tid] + gp.quicksum(loaded_time(a, b) * pair_loaded[tid, a, b] for a in locations for b in locations),
            name=f"transport_duration_{tid}",
        )
        if t["release_op"] is not None:
            model.addConstr(ts[tid] >= c[t["release_op"]], name=f"transport_release_{tid}")
        if t["delivers_op"] is not None:
            model.addConstr(s[t["delivers_op"]] >= te[tid], name=f"operation_delivery_{tid}")
        if t["kind"] == "return":
            model.addConstr(cmax >= te[tid], name=f"cmax_return_{tid}")

    for t in tasks:
        tid = t["id"]
        for r in agvs:
            for a in locations:
                m_initial = empty_time(WAREHOUSE, a)
                model.addConstr(
                    ts[tid] >= m_initial - m_initial * (1 - alpha[tid, r]) - m_initial * (1 - src_expr(t, a)),
                    name=safe_name(f"initial_empty_{tid}_V{r}_{a}"),
                )

    machine_order = {}
    m_machine = horizon
    for i, oi in enumerate(operations):
        for oj in operations[i + 1 :]:
            for m in machines:
                if (oi, m) in x and (oj, m) in x:
                    z = model.addVar(vtype=GRB.BINARY, name=f"mo_{op_name(oi)}_before_{op_name(oj)}_{m}")
                    machine_order[oi, oj, m] = z
                    model.addConstr(
                        s[oj] >= c[oi] - m_machine * (1 - z) - m_machine * (2 - x[oi, m] - x[oj, m]),
                        name=f"machine_seq1_{op_name(oi)}_{op_name(oj)}_{m}",
                    )
                    model.addConstr(
                        s[oi] >= c[oj] - m_machine * z - m_machine * (2 - x[oi, m] - x[oj, m]),
                        name=f"machine_seq2_{op_name(oi)}_{op_name(oj)}_{m}",
                    )

    task_ids = [t["id"] for t in tasks]
    for i, ti in enumerate(task_ids):
        for tj in task_ids[i + 1 :]:
            for r in agvs:
                model.addConstr(y[ti, tj, r] + y[tj, ti, r] <= alpha[ti, r], name=f"agv_pair_a1_{ti}_{tj}_V{r}")
                model.addConstr(y[ti, tj, r] + y[tj, ti, r] <= alpha[tj, r], name=f"agv_pair_a2_{ti}_{tj}_V{r}")
                model.addConstr(
                    y[ti, tj, r] + y[tj, ti, r] >= alpha[ti, r] + alpha[tj, r] - 1,
                    name=f"agv_pair_order_{ti}_{tj}_V{r}",
                )

    for ti in tasks:
        for tj in tasks:
            if ti["id"] == tj["id"]:
                continue
            for r in agvs:
                for end_loc in locations:
                    for start_loc in locations:
                        reposition_time = empty_time(end_loc, start_loc)
                        m_agv = horizon + reposition_time
                        model.addConstr(
                            ts[tj["id"]]
                            >= te[ti["id"]]
                            + reposition_time
                            - m_agv * (1 - y[ti["id"], tj["id"], r])
                            - m_agv * (1 - dst_expr(ti, end_loc))
                            - m_agv * (1 - src_expr(tj, start_loc)),
                            name=safe_name(f"empty_{ti['id']}_{tj['id']}_V{r}_{end_loc}_{start_loc}"),
                        )

    model.setObjective(cmax, GRB.MINIMIZE)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lp_path = output_path.with_suffix(".lp")
    if write_lp:
        model.write(str(lp_path))

    model.optimize()
    wall_time = time.perf_counter() - wall_start

    status_name = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.UNBOUNDED: "UNBOUNDED",
    }.get(model.Status, str(model.Status))

    result = {
        "instance": inst["raw"]["Instance"],
        "model_variant": "pdf_tight_big_m",
        "status": status_name,
        "objective_Cmax": None,
        "mip_gap": None,
        "solve_runtime_sec": model.Runtime,
        "wall_time_sec": wall_time,
        "horizon": horizon,
        "big_m": {
            "initial_empty": {a: empty_time(WAREHOUSE, a) for a in locations},
            "machine": m_machine,
            "agv_reposition": "horizon + empty_time(end_location, start_location)",
        },
        "lp_file": str(lp_path) if write_lp else None,
        "operations": [],
        "transport_tasks": [],
        "agv_routes": {},
    }

    if model.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT) and model.SolCount > 0:
        result["objective_Cmax"] = cmax.X
        result["mip_gap"] = model.MIPGap if model.IsMIP else 0.0

        for o in operations:
            chosen_machine = max(proc[o], key=lambda m: x[o, m].X)
            result["operations"].append(
                {
                    "operation": op_name(o),
                    "job": o[0],
                    "op_index": o[1],
                    "machine": chosen_machine,
                    "start": s[o].X,
                    "finish": c[o].X,
                    "processing_time": proc[o][chosen_machine],
                }
            )

        for t in tasks:
            tid = t["id"]
            chosen_agv = max(agvs, key=lambda r: alpha[tid, r].X)
            chosen_pair = max(((a, b) for a in locations for b in locations), key=lambda ab: pair_loaded[tid, ab[0], ab[1]].X)
            uses_agv = need_agv[tid].X > 0.5
            item = {
                "task": tid,
                "kind": t["kind"],
                "agv": f"V{chosen_agv}" if uses_agv else None,
                "uses_agv": uses_agv,
                "source": chosen_pair[0],
                "destination": chosen_pair[1],
                "start": ts[tid].X,
                "finish": te[tid].X,
                "loaded_time": loaded_time(chosen_pair[0], chosen_pair[1]),
            }
            if t["source_op"] is not None:
                item["source_op"] = op_name(t["source_op"])
            if t["dest_op"] is not None:
                item["dest_op"] = op_name(t["dest_op"])
            result["transport_tasks"].append(item)

        for r in agvs:
            route = [t for t in result["transport_tasks"] if t["agv"] == f"V{r}"]
            route.sort(key=lambda item: (item["start"], item["finish"], item["task"]))
            result["agv_routes"][f"V{r}"] = route

        result["operations"].sort(key=lambda item: (item["start"], item["finish"], item["operation"]))
        result["transport_tasks"].sort(key=lambda item: (item["start"], item["finish"], item["task"]))
    elif model.Status in (GRB.INFEASIBLE, GRB.INF_OR_UNBD):
        model.computeIIS()
        iis_path = output_path.with_suffix(".iis.ilp")
        model.write(str(iis_path))
        result["iis_file"] = str(iis_path)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    if print_result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description="PDF-version MILP solver for assembly FJSP with AGV transportation.")
    parser.add_argument("instance", help="Path to instance JSON.")
    parser.add_argument("--output", default="solution_pdf_milp.json", help="Output JSON path.")
    parser.add_argument("--time-limit", type=float, default=300.0, help="Gurobi time limit in seconds.")
    parser.add_argument("--mip-gap", type=float, default=0.0, help="Target relative MIP gap.")
    parser.add_argument("--log", action="store_true", help="Show Gurobi log.")
    parser.add_argument("--no-lp", action="store_true", help="Do not export the LP model file.")
    args = parser.parse_args()

    solve(args.instance, args.output, args.time_limit, args.mip_gap, args.log, write_lp=not args.no_lp)


if __name__ == "__main__":
    main()
