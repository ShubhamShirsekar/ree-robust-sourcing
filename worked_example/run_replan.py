"""Value of the emergency re-plan in the stress setting.

Paths: [none, A, A, none] with every shock type in A at HIGH severity, for every A containing C or E
(the trigger covers capacity and licensing events only). The trigger fires in the first shock quarter,
after a quarter share PHI (assumption: about three weeks in, the time for a second article to confirm
the event). Policies decide from medium-severity signals, as in the stress test, so the re-plan is the
only way they learn the shock is severe. Every policy is run with and without the re-plan.

Output: outputs/<run>/replan_paths.csv, replan_summary.csv, run_info.json
Run: D:/DAB/Thesis/venv/Scripts/python.exe -I worked_example/run_replan.py replan1
"""
import itertools
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as M  # noqa: E402

RUN = sys.argv[1] if len(sys.argv) > 1 else "replan1"
OUT = M.HERE / "outputs" / RUN
OUT.mkdir(parents=True, exist_ok=True)
PHI = 0.25
inp = M.Inputs.load()
MED = {t: "medium" for t in M.TYPES}
HIGH = {t: "high" for t in M.TYPES}

policies = [M.Policy(f"Robust G{g}", "robust", g) for g in range(5)]
policies += [M.Policy(f"Robust no contracts G{g}", "robust", g, contracts=False) for g in (2, 3)]
policies += [
    M.Policy("Expected value", "ev"),
    M.Policy("Dual sourcing", "fixed", contracts=False,
             shares={"China": 0.8, "Asia excl. China": 0.2}, stock_weeks=inp.gen["dual_sourcing"]["stock_weeks"]),
    M.Policy("Status quo", "fixed", contracts=False, shares=inp.gen["status_quo_shares"], stock_weeks=None),
]
sets = [frozenset(A) for r in range(1, 5) for A in itertools.combinations(M.TYPES, r) if {"C", "E"} & set(A)]

rows, t0 = [], time.time()
for pol in policies:
    for A in sets:
        path = [(frozenset(), MED), (A, HIGH), (A, HIGH), (frozenset(), MED)]
        trig = [None, (PHI, tuple(sorted({"C", "E"} & set(A)))), None, None]
        for rp in (False, True):
            df, credit = M.run_path(inp, pol, path, [MED] * 4, trig, replan=rp)
            rows.append({"policy": pol.name, "scenario": "".join(sorted(A)), "replan": rp,
                         "net_cost_kusd": df.cost.sum() - credit,
                         "shortage_t": sum(df[f"short_t_{i}"].sum() for i in inp.I),
                         "spot_t": sum(df[f"spot_t_{i}"].sum() for i in inp.I),
                         "min_fill_Dy": df.fill_Dy.min(), "min_fill_Tb": df.fill_Tb.min(),
                         "min_fill_NdPr": df.fill_NdPr.min()})
    print(f"{pol.name}: done ({time.time() - t0:.0f} s)", flush=True)

d = pd.DataFrame(rows)
d.to_csv(OUT / "replan_paths.csv", index=False)
w = d.pivot_table(index=["policy", "scenario"], columns="replan", values="net_cost_kusd")
w["saving"] = w[False] - w[True]
s = w.groupby("policy").agg(worst_without=(False, "max"), worst_with=(True, "max"),
                            mean_saving=("saving", "mean"), max_saving=("saving", "max"),
                            min_saving=("saving", "min")).reset_index()
s.to_csv(OUT / "replan_summary.csv", index=False)
(OUT / "run_info.json").write_text(json.dumps({
    "run": RUN, "seconds": round(time.time() - t0), "phi": PHI, "severity": "high for every active type",
    "scenarios": ["".join(sorted(A)) for A in sets], "signals": "medium (as stress test)",
    "inputs": "same as outputs/run3"}, indent=2), encoding="utf-8")
print(s.round(1).to_string())
