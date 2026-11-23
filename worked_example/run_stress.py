"""Stress test: every policy over 16 one-year paths (shock in quarters 2 and 3).

Writes to outputs/<run>/: stress_quarters.csv, stress_paths.csv, policy_summary.csv, frontier.csv
Run: D:/DAB/Thesis/venv/Scripts/python.exe -I worked_example/run_stress.py run1
"""
import itertools
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as M  # noqa: E402

RUN = sys.argv[1] if len(sys.argv) > 1 else "run1"
OUT = M.HERE / "outputs" / RUN
OUT.mkdir(parents=True, exist_ok=True)
inp = M.Inputs.load()
MED = {t: "medium" for t in M.TYPES}

policies = [M.Policy(f"Robust G{g}", "robust", g) for g in range(5)]
policies += [M.Policy(f"Robust no contracts G{g}", "robust", g, contracts=False) for g in range(5)]
policies += [M.Policy(f"Uniform G{g}", "robust", g, uniform=True) for g in range(5)]
policies += [
    M.Policy("Expected value", "ev"),
    M.Policy("Dual sourcing", "fixed", contracts=False,
             shares={"China": 0.8, "Asia excl. China": 0.2}, stock_weeks=inp.gen["dual_sourcing"]["stock_weeks"]),
    M.Policy("Status quo", "fixed", contracts=False, shares=inp.gen["status_quo_shares"],
             stock_weeks=None),
]

shock_sets = [frozenset(A) for r in range(5) for A in itertools.combinations(M.TYPES, r)]
quarters, paths = [], []
pf = {}
t0 = time.time()
for A in shock_sets:
    path = [(frozenset(), MED), (A, MED), (A, MED), (frozenset(), MED)]
    pf[A] = M.perfect_foresight(inp, path)[0]

for pol in policies:
    for A in shock_sets:
        path = [(frozenset(), MED), (A, MED), (A, MED), (frozenset(), MED)]
        df, credit = M.run_path(inp, pol, path, [MED] * 4)
        key = "".join(sorted(A)) or "none"
        df["scenario"] = key
        quarters.append(df)
        alpha = {i: inp.tiers.loc[int(inp.com.loc[i, "tier"]), "fill_target"] for i in inp.I}
        row = {"policy": pol.name, "scenario": key, "n_shocks": len(A),
               "net_cost_kusd": df.cost.sum() - credit, "pf_cost_kusd": pf[A]}
        row["regret_kusd"] = row["net_cost_kusd"] - row["pf_cost_kusd"]
        for i in inp.I:
            row[f"min_fill_{i}"] = df[f"fill_{i}"].min()
            row[f"survive_{i}"] = bool((df[f"fill_{i}"] >= alpha[i] - 1e-9).all())
        row["survive_all"] = all(row[f"survive_{i}"] for i in inp.I)
        row["shortage_t"] = sum(df[f"short_t_{i}"].sum() for i in inp.I)
        row["spot_t"] = sum(df[f"spot_t_{i}"].sum() for i in inp.I)
        paths.append(row)
    print(f"{pol.name}: done ({time.time() - t0:.0f} s)", flush=True)

q = pd.concat(quarters, ignore_index=True)
q.to_csv(OUT / "stress_quarters.csv", index=False)
p = pd.DataFrame(paths)
p.to_csv(OUT / "stress_paths.csv", index=False)

summary = p.groupby("policy").agg(
    no_shock_cost=("net_cost_kusd", lambda s: s[p.loc[s.index, "scenario"] == "none"].iloc[0]),
    worst_case_cost=("net_cost_kusd", "max"),
    max_regret=("regret_kusd", "max"),
    scenarios_survived=("survive_all", "sum"),
    worst_min_fill_Dy=("min_fill_Dy", "min"),
    worst_min_fill_Tb=("min_fill_Tb", "min"),
    worst_min_fill_NdPr=("min_fill_NdPr", "min"),
).reset_index()
summary.to_csv(OUT / "policy_summary.csv", index=False)
front = summary[summary.policy.str.match(r"^Robust G\d$")].copy()
front["gamma"] = front.policy.str[-1].astype(int)
front.sort_values("gamma")[["gamma", "no_shock_cost", "worst_case_cost", "max_regret", "scenarios_survived"]] \
    .to_csv(OUT / "frontier.csv", index=False)
(OUT / "run_info.json").write_text(json.dumps({
    "run": RUN, "seconds": round(time.time() - t0), "inputs": "inputs/ approved 2026-11-21, changed 2026-11-22 by author decision",
    "changes_since_run1_prelim": [
        "P shock sizes calibrated on USGS 2010-2024 (2010-2015 yearend range midpoints) instead of 2017-2024",
        "planning scenarios: shocks last shock_duration_quarters (2) instead of the whole 4-quarter horizon",
        "end-of-horizon stock and transit credited in planning, perfect foresight and evaluation at the normal China price (evaluation used the regional mean price before)"],
    "note": "C and E magnitudes checked against 2010-2025 Comtrade data on 2026-11-22: unchanged",
    "stress_paths": "shock in quarters 2 and 3 at medium severity",
    "status_quo_stock": "tier floor per commodity", "pulp": M.pulp.__version__}, indent=2), encoding="utf-8")
print(summary.round(1).to_string())
