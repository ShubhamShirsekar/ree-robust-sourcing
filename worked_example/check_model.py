"""Model checks for Section 5.7.

1. Independent recomputation: prices are rebuilt from inputs/*.csv without model.params; logged
   quantities from simulated paths are checked against every settlement constraint, the stock balance
   is re-traced quarter by quarter, and each cost component is recomputed.
2. Hand-checked cases: two single-quarter settlements whose results were worked out by hand.
3. Benchmark checks on outputs/run3: regret against perfect foresight, reproducibility.

Output: outputs/checks/check_report.json (and printed). Exit code 1 if any check fails.
Run: D:/DAB/Thesis/venv/Scripts/python.exe -I worked_example/check_model.py
"""
import itertools
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as M  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs" / "checks"
OUT.mkdir(parents=True, exist_ok=True)
TOL = 1e-5
report, failures = {}, []


def ok(name, cond, detail=""):
    report[name] = {"pass": bool(cond), "detail": detail}
    if not cond:
        failures.append(name)


# ------------------------------------------------------------------ independent price rebuild (no M.params)
com = pd.read_csv(HERE / "inputs" / "commodities.csv").set_index("commodity")
reg = pd.read_csv(HERE / "inputs" / "regions.csv").set_index("region")
shk = pd.read_csv(HERE / "inputs" / "shocks.csv")
gen = json.loads((HERE / "inputs" / "general.json").read_text(encoding="utf-8"))
I, J = list(com.index), list(reg.index)


def sv(shock, sev, par, c="all"):
    r = shk[(shk.shock == shock) & (shk.severity == sev) & (shk.parameter == par) & (shk.commodity == c)]
    return float(r.value.iloc[0])


def my_params(active, sev):
    out = {}
    for i in I:
        c0 = com.loc[i, "base_cost_usd_t"] / 1000
        for j in J:
            base = c0 * (1 + reg.loc[j, "cost_premium"])
            pf, rf, lead, cut = 1.0, 1.0, 0.0, 0.0
            if "P" in active:
                inc = sv("P", sev, "China price increase", i)
                pf = 1 + inc if j == "China" else 1 + sv("P", sev, "spillover to other regions (share of China increase)") * inc
            if "R" in active:
                far = j in ("Europe", "North America and other")
                rf = 1 + sv("R", sev, "Europe and North America landed cost increase" if far else "China and Asia landed cost increase")
                lead += sv("R", sev, "Europe and North America lead time added (weeks)" if far else "China and Asia lead time added (weeks)")
            if "E" in active and j == "China":
                lead += sv("E", sev, "China lead time added (weeks)")
            if "C" in active and j == "China":
                cut = sv("C", sev, "China capacity reduction")
            k0 = reg.loc[j, f"cap_{i}"] * com.loc[i, "demand_t"] / 4
            out[i, j] = {"price": base * pf * rf, "cprice": base * (1 + gen["contract_premium"]) * rf,
                         "K": k0 * (1 - cut), "lost": k0 * cut, "lam": min(lead / 13, 1.0)}
    return out


# ------------------------------------------------------------------ 1. recompute simulated paths
inp = M.Inputs.load()
MED = {t: "medium" for t in M.TYPES}
cbar = {i: sum(com.loc[i, "base_cost_usd_t"] / 1000 * (1 + reg.loc[j, "cost_premium"]) for j in J) / len(J) for i in I}
d4 = {i: com.loc[i, "demand_t"] / 4 for i in I}
tier = pd.read_csv(HERE / "inputs" / "tiers.csv").set_index("tier")
penm = {i: tier.loc[int(com.loc[i, "tier"]), "penalty_mult"] for i in I}
floor = {i: tier.loc[int(com.loc[i, "tier"]), "floor_weeks"] * com.loc[i, "demand_t"] / 52 for i in I}
run3 = pd.read_csv(HERE / "outputs" / "run3" / "stress_paths.csv")

pols = [M.Policy("Robust G2", "robust", 2), M.Policy("Robust G4", "robust", 4), M.Policy("Expected value", "ev"),
        M.Policy("Status quo", "fixed", contracts=False, shares=inp.gen["status_quo_shares"], stock_weeks=None)]
sets = [frozenset(A) for r in range(5) for A in itertools.combinations(M.TYPES, r)]
worst_cost_err = worst_bal_err = worst_con_err = worst_repro = 0.0
n_quarters = 0
for pol in pols:
    for A in sets:
        path = [(frozenset(), MED), (A, MED), (A, MED), (frozenset(), MED)]
        df, credit = M.run_path(inp, pol, path, [MED] * 4)
        stock = dict(floor)
        pipe = {(i, j): 0.0 for i in I for j in J}
        contract = {(i, j): df.iloc[0][f"contract_{i}_{j}"] for i in I for j in J}
        for _, row in df.iterrows():
            active = frozenset() if row.active == "none" else frozenset(row.active)
            pr = my_params(active, "medium")
            cost = 0.0
            for i in I:
                arrive = sum(pipe[i, j] for j in J)
                for j in J:
                    shc, sho, o = row[f"shc_{i}_{j}"], row[f"sho_{i}_{j}"], row[f"order_{i}_{j}"]
                    q = pr[i, j]
                    worst_con_err = max(worst_con_err, shc - contract[i, j], sho - o, shc + sho - q["K"],
                                        contract[i, j] + o - q["lost"] - shc - sho)
                    cost += q["cprice"] * shc + q["price"] * sho
                    arrive += (1 - q["lam"]) * (shc + sho)
                    pipe[i, j] = q["lam"] * (shc + sho)
                e, z, s = row[f"spot_t_{i}"], row[f"short_t_{i}"], row[f"stock_{i}"]
                spot_price = gen["spot_price_multiplier"] * max(pr[i, j]["price"] for j in J)
                worst_con_err = max(worst_con_err, e - gen["spot_limit_share_of_demand"] * d4[i], z - d4[i], -s)
                expected_s = stock[i] + arrive + e + z - d4[i]
                worst_bal_err = max(worst_bal_err, abs(expected_s - s), abs((1 - z / d4[i]) - row[f"fill_{i}"]))
                stock[i] = s
                cost += gen["holding_cost_rate"] * cbar[i] / 4 * s + spot_price * e \
                    + penm[i] * gen["shortage_penalty_multiple_of_cost"] * cbar[i] * z
            worst_cost_err = max(worst_cost_err, abs(cost - row.cost))
            n_quarters += 1
        c0 = {i: com.loc[i, "base_cost_usd_t"] / 1000 for i in I}
        my_net = df.cost.sum() - sum(c0[i] * (stock[i] + sum(pipe[i, j] for j in J)) for i in I)
        ref = run3[(run3.policy == pol.name) & (run3.scenario == ("".join(sorted(A)) or "none"))].net_cost_kusd.iloc[0]
        worst_repro = max(worst_repro, abs(my_net - ref))

ok("recompute_costs", worst_cost_err < 1e-3, f"{n_quarters} quarters; max abs cost difference {worst_cost_err:.2e} kUSD")
ok("constraints_hold", worst_con_err < 1e-5, f"max constraint violation {worst_con_err:.2e} t")
ok("stock_balance_and_fill", worst_bal_err < 1e-5, f"max stock balance / fill difference {worst_bal_err:.2e}")
ok("reproduces_run3", worst_repro < 1e-3, f"max net cost difference to run3 {worst_repro:.2e} kUSD")

# ------------------------------------------------------------------ 2. hand-checked single-quarter cases
# Dysprosium: demand 30 t/yr -> 7.5 t per quarter; China capacity 7.5 t; base cost 271.778 kUSD/t (China).
# cbar = 271.778 x mean(1, 1.10, 1.20, 1.25) = 271.778 x 1.1375 = 309.148 kUSD/t.
# Other commodities start with one quarter's demand in stock and order nothing, so they add no cost.
ts = M.TierSet.build(inp)
c0dy = com.loc["Dy", "base_cost_usd_t"] / 1000
cbar_dy = c0dy * 1.1375
zero = {(i, j): 0.0 for i in I for j in J}


def hand_case(start_dy, sev):
    st = M.State({"NdPr": d4["NdPr"], "Dy": start_dy, "Tb": d4["Tb"]}, dict(zero), dict(zero))
    orders = dict(zero)
    orders["Dy", "China"] = 7.5
    return M.settle(inp, ts, st, orders, M.params(inp, frozenset("C"), {t: sev for t in M.TYPES}))[0]

# Case A: 8 weeks of stock (4.615 t), medium capacity cut of 50%. China ships 3.75 t (no lead time).
# Available 4.615 + 3.75 = 8.365 >= 7.5: no spot, no shortage, end stock 0.865 t.
# Cost = 3.75 x 271.778 = 1,019.17 (order) + 0.865 x 0.20 x 309.148 / 4 = 13.37 (holding) = 1,032.54 kUSD.
a = hand_case(30 * 8 / 52, "medium")
expA = {"order_cost": 3.75 * c0dy, "holding_cost": (30 * 8 / 52 + 3.75 - 7.5) * 0.2 * cbar_dy / 4,
        "spot_cost": 0.0, "shortage_penalty": 0.0, "fill_Dy": 1.0, "short_t_Dy": 0.0}
errA = max(abs(a[k] - v) for k, v in expA.items())
ok("hand_case_A", errA < 1e-4, f"Dy, 8 weeks stock, 50% China cut: order {a['order_cost']:.2f}, holding "
   f"{a['holding_cost']:.2f} kUSD, fill {a['fill_Dy']:.3f}; max difference from hand result {errA:.1e}")

# Case B: no stock, high capacity cut of 80%. China ships 1.5 t; spot limit 10% x 30 / 4 = 0.75 t;
# shortage 7.5 - 1.5 - 0.75 = 5.25 t; fill 1 - 5.25 / 7.5 = 0.30.
# Spot price 1.5 x highest regional price = 1.5 x 271.778 x 1.25 = 509.58 kUSD/t -> 382.19 kUSD.
# Shortage penalty = tier multiplier 3.0 x 3 x cbar 309.148 x 5.25 t = 14,607.24 kUSD. Order 1.5 x 271.778 = 407.67.
b = hand_case(0.0, "high")
expB = {"order_cost": 1.5 * c0dy, "spot_cost": 0.75 * 1.5 * c0dy * 1.25, "short_t_Dy": 5.25, "fill_Dy": 0.30,
        "shortage_penalty": 3.0 * 3.0 * cbar_dy * 5.25, "holding_cost": 0.0}
errB = max(abs(b[k] - v) for k, v in expB.items())
ok("hand_case_B", errB < 1e-4, f"Dy, no stock, 80% China cut: shipped 1.5 t, spot 0.75 t, shortage "
   f"{b['short_t_Dy']:.2f} t, fill {b['fill_Dy']:.2f}, penalty {b['shortage_penalty']:,.2f} kUSD; "
   f"max difference from hand result {errB:.1e}")

# ------------------------------------------------------------------ 3. benchmark checks on run3
# Perfect foresight obeys the real tier caps, so it is a lower bound only for policies that obey them too
# (robust with real tiers, expected value). Uniform, dual sourcing and status quo use other China shares.
capped = run3.policy.str.match(r"^Robust (no contracts )?G\d$") | (run3.policy == "Expected value")
neg = run3[capped & (run3.regret_kusd < -1e-6)]
ok("regret_nonnegative", neg.empty,
   f"perfect foresight is a lower bound on all {int(capped.sum())} paths of the policies that obey the tier caps"
   if neg.empty else "; ".join(f"{r.policy}/{r.scenario} {r.regret_kusd:.1f}" for r in neg.itertuples()))
other = run3[~capped & (run3.regret_kusd < -1e-6)]
report["info_negative_regret_uncapped"] = {
    "pass": True, "detail": "policies that exceed the tier caps can beat the capped benchmark: " +
    ("; ".join(f"{r.policy}/{r.scenario} {r.regret_kusd:.1f} kUSD" for r in other.itertuples()) or "none")}

(OUT / "check_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
for k, v in report.items():
    print(f"{'PASS' if v['pass'] else 'FAIL'}  {k}: {v['detail']}")
sys.exit(1 if failures else 0)
