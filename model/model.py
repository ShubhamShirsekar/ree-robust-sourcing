"""Quarterly rolling robust sourcing model (progress/model_revision.md).

Units: tonnes, weeks, thousand USD (kUSD). All inputs come from model/inputs/.
The planning LP is solved at the start of each quarter for the next four quarters;
only the current quarter's flexible orders (and, in the first quarter of a year,
the annual contract volumes) are implemented. The realised quarter is then settled
with a small recourse LP.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pulp

HERE = Path(__file__).resolve().parent
INP = HERE / "inputs"
TYPES = ["P", "C", "E", "R"]
QW = 13.0          # weeks per quarter
HORIZON = 4        # planning quarters
CHINA = "China"
FAR = ["Europe", "North America and other"]   # regions hit harder by route shocks


# ----------------------------------------------------------------------------- inputs
@dataclass
class Inputs:
    com: pd.DataFrame
    reg: pd.DataFrame
    tiers: pd.DataFrame
    shocks: pd.DataFrame
    gen: dict
    I: list = field(default_factory=list)
    J: list = field(default_factory=list)

    @classmethod
    def load(cls):
        x = cls(pd.read_csv(INP / "commodities.csv").set_index("commodity"),
                pd.read_csv(INP / "regions.csv").set_index("region"),
                pd.read_csv(INP / "tiers.csv").set_index("tier"),
                pd.read_csv(INP / "shocks.csv"),
                json.loads((INP / "general.json").read_text(encoding="utf-8")))
        x.I, x.J = list(x.com.index), list(x.reg.index)
        return x

    def shock(self, typ, sev, parameter, commodity="all"):
        s = self.shocks
        row = s[(s.shock == typ) & (s.severity == sev) & (s.parameter == parameter) & (s.commodity == commodity)]
        assert len(row) == 1, (typ, sev, parameter, commodity)
        return float(row.value.iloc[0])


@dataclass
class TierSet:
    """Tier settings per commodity; `uniform` gives every commodity the Tier 2 settings."""
    cap: dict
    floor_t: dict
    pen_mult: dict
    alpha: dict
    extra: dict

    @classmethod
    def build(cls, inp: Inputs, uniform=False):
        t = {i: (2 if uniform else int(inp.com.loc[i, "tier"])) for i in inp.I}
        T = inp.tiers
        d = inp.com.demand_t
        return cls({i: T.loc[t[i], "cap_U"] for i in inp.I},
                   {i: T.loc[t[i], "floor_weeks"] * d[i] / 52 for i in inp.I},
                   {i: T.loc[t[i], "penalty_mult"] for i in inp.I},
                   {i: T.loc[t[i], "fill_target"] for i in inp.I},
                   {i: int(T.loc[t[i], "extra_budget"]) for i in inp.I})


# ----------------------------------------------------------------------------- scenario parameters
def params(inp: Inputs, active: frozenset, sev: dict) -> dict:
    """Quarter-level parameters for a set of active shock types at given severities."""
    I, J, g = inp.I, inp.J, inp.gen
    c0 = inp.com.base_cost_usd_t / 1000.0                      # kUSD per tonne
    prem = inp.reg.cost_premium
    p = {"price": {}, "cprice": {}, "spot": {}, "K": {}, "lost": {}, "lam": {}, "elim": {}}
    for i in I:
        for j in J:
            base = c0[i] * (1 + prem[j])
            pf = 1.0
            if "P" in active:
                inc = inp.shock("P", sev["P"], "China price increase", i)
                spill = inp.shock("P", sev["P"], "spillover to other regions (share of China increase)")
                pf = 1 + inc if j == CHINA else 1 + spill * inc
            rf, lead = 1.0, 0.0
            if "R" in active:
                far = j in FAR
                rf = 1 + inp.shock("R", sev["R"], "Europe and North America landed cost increase" if far
                                   else "China and Asia landed cost increase")
                lead += inp.shock("R", sev["R"], "Europe and North America lead time added (weeks)" if far
                                  else "China and Asia lead time added (weeks)")
            if "E" in active and j == CHINA:
                lead += inp.shock("E", sev["E"], "China lead time added (weeks)")
            k0 = inp.reg.loc[j, f"cap_{i}"] * inp.com.demand_t[i] / 4      # tonnes per quarter
            cut = inp.shock("C", sev["C"], "China capacity reduction") if ("C" in active and j == CHINA) else 0.0
            p["price"][i, j] = base * pf * rf
            p["cprice"][i, j] = base * (1 + g["contract_premium"]) * rf
            p["K"][i, j] = k0 * (1 - cut)
            p["lost"][i, j] = k0 * cut
            p["lam"][i, j] = min(lead / QW, 1.0)
        p["spot"][i] = g["spot_price_multiplier"] * max(p["price"][i, j] for j in J)
        p["elim"][i] = g["spot_limit_share_of_demand"] * inp.com.demand_t[i] / 4
    return p


def expected_params(inp: Inputs, sev: dict, forced: frozenset = frozenset()) -> dict:
    """Probability-weighted parameters: each shock type occurs independently with probability q
    (probability 1 for the `forced` types, used by the emergency re-plan)."""
    q = inp.gen["ev_shock_probability"]
    acc = None
    for r in range(5):
        for A in itertools.combinations(TYPES, r):
            w = 1.0
            for t in TYPES:
                qt = 1.0 if t in forced else q
                w *= qt if t in A else (1 - qt)
            if w == 0:
                continue
            p = params(inp, frozenset(A), sev)
            if acc is None:
                acc = {k: {kk: w * vv for kk, vv in v.items()} for k, v in p.items()}
            else:
                for k, v in p.items():
                    for kk, vv in v.items():
                        acc[k][kk] += w * vv
    return acc


def scenarios(gamma: int):
    return [frozenset(A) for r in range(gamma + 1) for A in itertools.combinations(TYPES, r)]


# ----------------------------------------------------------------------------- state
@dataclass
class State:
    stock: dict           # tonnes on hand
    pipe: dict            # tonnes in transit, arriving next quarter, by (i, j)
    contract: dict        # tonnes per quarter, by (i, j)


def common(inp: Inputs):
    c0 = inp.com.base_cost_usd_t / 1000.0
    cbar = {i: float(np.mean([c0[i] * (1 + inp.reg.cost_premium[j]) for j in inp.J])) for i in inp.I}
    d4 = {i: inp.com.demand_t[i] / 4 for i in inp.I}
    hold = {i: inp.gen["holding_cost_rate"] * cbar[i] / 4 for i in inp.I}
    pen = {i: inp.gen["shortage_penalty_multiple_of_cost"] * cbar[i] for i in inp.I}
    W = sum(cbar[i] * inp.com.demand_t[i] for i in inp.I) * inp.gen["working_capital_weeks_of_demand_value"] / 52
    k0 = {(i, j): inp.reg.loc[j, f"cap_{i}"] * inp.com.demand_t[i] / 4 for i in inp.I for j in inp.J}
    return cbar, d4, hold, pen, W, k0


# ----------------------------------------------------------------------------- planning LP
def plan(inp: Inputs, ts: TierSet, st: State, paths: dict, cost_keys: list, service: dict,
         decide_contract: bool, use_contract: bool, fixed_orders=None):
    """Rolling planning LP.

    paths: {key: [params for quarter 0..H-1]}  (a persistent scenario repeats one params dict)
    cost_keys: keys whose cost enters the worst-case epigraph
    service: {i: set of keys where the fill-rate target applies}
    Returns (orders now {(i,j): t}, contract {(i,j): t}, status, objective)
    """
    I, J = inp.I, inp.J
    cbar, d4, hold, pen, W, k0 = common(inp)
    big = inp.gen["service_slack_penalty_multiple_of_spot"]
    tie = inp.gen["tie_break_weight_no_shock_cost"]
    m = pulp.LpProblem("plan", pulp.LpMinimize)
    V = lambda name: pulp.LpVariable(name, lowBound=0)

    if decide_contract and use_contract:
        g = {(i, j): V(f"g_{i}_{j}".replace(" ", "_").replace(".", "")) for i in I for j in J}
    else:
        g = {(i, j): (st.contract[i, j] if use_contract else 0.0) for i in I for j in J}
    o0 = {(i, j): V(f"o0_{i}_{j}".replace(" ", "_").replace(".", "")) for i in I for j in J}
    t = pulp.LpVariable("t")
    cost = {}
    for kn, (key, path) in enumerate(paths.items()):
        tot = 0
        prev_ship = None
        prev_stock = None
        for r, p in enumerate(path):
            tag = f"{kn}_{r}"
            if r == 0:
                order = o0
            else:
                order = {(i, j): V(f"o_{tag}_{ii}_{jj}") for ii, i in enumerate(I) for jj, j in enumerate(J)}
            shc = {(i, j): V(f"shc_{tag}_{ii}_{jj}") for ii, i in enumerate(I) for jj, j in enumerate(J)}
            sho = {(i, j): V(f"sho_{tag}_{ii}_{jj}") for ii, i in enumerate(I) for jj, j in enumerate(J)}
            e = {i: V(f"e_{tag}_{ii}") for ii, i in enumerate(I)}
            z = {i: V(f"z_{tag}_{ii}") for ii, i in enumerate(I)}
            s = {i: V(f"s_{tag}_{ii}") for ii, i in enumerate(I)}
            sl = {i: V(f"sl_{tag}_{ii}") for ii, i in enumerate(I)}
            for i in I:
                for j in J:
                    m += shc[i, j] <= g[i, j]
                    m += sho[i, j] <= order[i, j]
                    m += shc[i, j] + sho[i, j] <= p["K"][i, j]
                    m += shc[i, j] + sho[i, j] >= g[i, j] + order[i, j] - p["lost"][i, j]
                    if r > 0 or kn == 0:
                        m += g[i, j] + order[i, j] <= k0[i, j]
                    m += g[i, j] + order[i, j] <= ts.cap[i] * pulp.lpSum(g[i, jj] + order[i, jj] for jj in J)
                arrive = pulp.lpSum((1 - p["lam"][i, j]) * (shc[i, j] + sho[i, j]) for j in J)
                if r == 0:
                    arrive += sum(st.pipe[i, j] for j in J)
                    before = st.stock[i]
                else:
                    arrive += pulp.lpSum(prev_lam[i, j] * prev_ship[i, j] for j in J)
                    before = prev_stock[i]
                m += s[i] == before + arrive + e[i] + z[i] - d4[i]
                m += e[i] <= p["elim"][i]
                m += z[i] <= d4[i]
                if key in service[i]:
                    m += z[i] <= (1 - ts.alpha[i]) * d4[i] + sl[i]
                else:
                    m += sl[i] == 0
                if len(key) == 0:
                    m += s[i] >= ts.floor_t[i]
            m += pulp.lpSum(cbar[i] * s[i] for i in I) <= W
            tot += pulp.lpSum(p["cprice"][i, j] * shc[i, j] + p["price"][i, j] * sho[i, j] for i in I for j in J)
            tot += pulp.lpSum(hold[i] * s[i] + p["spot"][i] * e[i] + ts.pen_mult[i] * pen[i] * z[i]
                              + big * p["spot"][i] * sl[i] for i in I)
            prev_ship = {(i, j): shc[i, j] + sho[i, j] for i in I for j in J}
            prev_lam = p["lam"]
            prev_stock = s
        # End-of-horizon stock and goods in transit keep their value (correction 2026-11-22), credited at the
        # normal China price, the cheapest source, so that holding stock can never earn a profit.
        c0 = inp.com.base_cost_usd_t / 1000.0
        tot -= pulp.lpSum(c0[i] * (prev_stock[i] + pulp.lpSum(prev_lam[i, j] * prev_ship[i, j] for j in J)) for i in I)
        cost[key] = tot
        if key in cost_keys:
            m += t >= tot
    if fixed_orders is not None:
        for ij, v in fixed_orders.items():
            m += o0[ij] == v
    nominal = cost[frozenset()] if frozenset() in cost else 0
    m += t + tie * nominal
    status = m.solve(pulp.PULP_CBC_CMD(msg=False))
    st_name = pulp.LpStatus[status]
    orders = {ij: max(0.0, o0[ij].value() or 0.0) for ij in o0}
    contract = {ij: (max(0.0, g[ij].value() or 0.0) if isinstance(g[ij], pulp.LpVariable) else g[ij]) for ij in g}
    return orders, contract, st_name, pulp.value(m.objective)


# ----------------------------------------------------------------------------- policies
@dataclass
class Policy:
    name: str
    kind: str                 # robust | ev | fixed
    gamma: int = 0
    contracts: bool = True
    uniform: bool = False
    shares: dict | None = None
    stock_weeks: float | None = None


def decide(inp, pol: Policy, st: State, sev: dict, first_q: bool, forced: frozenset = frozenset()):
    """forced: shock types treated as already happening (emergency re-plan); they are added to every scenario."""
    ts = TierSet.build(inp, pol.uniform)
    cbar, d4, hold, pen, W, k0 = common(inp)
    if pol.kind == "fixed":
        orders = {}
        for i in inp.I:
            # stock_weeks=None means each commodity's tier floor
            target = ts.floor_t[i] if pol.stock_weeks is None else pol.stock_weeks * inp.com.demand_t[i] / 52
            need = max(0.0, d4[i] + target - st.stock[i] - sum(st.pipe[i, j] for j in inp.J))
            for j in inp.J:
                orders[i, j] = min(need * pol.shares.get(j, 0.0), k0[i, j])
        return orders, {ij: 0.0 for ij in orders}, "Heuristic"
    dur = min(int(inp.gen.get("shock_duration_quarters", HORIZON)), HORIZON)
    if pol.kind == "ev":
        p = expected_params(inp, sev)
        key = frozenset()
        if forced:   # the triggered shock is certain for its duration; other types keep their expectation
            pf = expected_params(inp, sev, forced)
            paths = {key: [pf] * dur + [p] * (HORIZON - dur)}
        else:
            paths = {key: [p] * HORIZON}
        service = {i: {key} for i in inp.I}
        o, g, s, _ = plan(inp, ts, st, paths, [key], service, first_q, pol.contracts)
        return o, g, s
    gmax = min(pol.gamma + max(ts.extra.values()), 4)
    keys = scenarios(gmax)
    # a planned shock lasts `shock_duration_quarters` from now, then the quarters revert to no shock
    calm = params(inp, frozenset(), sev)
    paths = {A: [params(inp, A | forced, sev)] * dur + [calm] * (HORIZON - dur) for A in keys}
    cost_keys = [A for A in keys if len(A) <= pol.gamma]
    service = {i: {A for A in keys if len(A) <= min(pol.gamma + ts.extra[i], 4)} for i in inp.I}
    o, g, s, _ = plan(inp, ts, st, paths, cost_keys, service, first_q, pol.contracts)
    return o, g, s


# ----------------------------------------------------------------------------- realised quarter
def settle(inp, ts: TierSet, st: State, orders: dict, p: dict, lam_order: dict | None = None):
    """Recourse LP for the realised quarter. Returns cost breakdown, fills and the next state.

    lam_order: share of flexible-order shipments delayed to the next quarter, by (i, j); defaults to p["lam"].
    It differs only when part of the quarter's orders was placed mid-quarter by an emergency re-plan.
    """
    lam_o = p["lam"] if lam_order is None else lam_order
    I, J = inp.I, inp.J
    cbar, d4, hold, pen, W, k0 = common(inp)
    # Solver tolerance can leave contract + order a few 1e-7 t above normal capacity; trim only that.
    trimmed = {}
    for ij, o in orders.items():
        excess = st.contract[ij] + o - k0[ij]
        assert excess < 1e-4, f"order above capacity by {excess} t for {ij}: not a rounding issue"
        trimmed[ij] = max(0.0, o - max(0.0, excess))
    orders = trimmed
    m = pulp.LpProblem("settle", pulp.LpMinimize)
    V = lambda n: pulp.LpVariable(n, lowBound=0)
    shc = {(i, j): V(f"shc_{a}_{b}") for a, i in enumerate(I) for b, j in enumerate(J)}
    sho = {(i, j): V(f"sho_{a}_{b}") for a, i in enumerate(I) for b, j in enumerate(J)}
    e = {i: V(f"e_{a}") for a, i in enumerate(I)}
    z = {i: V(f"z_{a}") for a, i in enumerate(I)}
    s = {i: V(f"s_{a}") for a, i in enumerate(I)}
    for i in I:
        for j in J:
            m += shc[i, j] <= st.contract[i, j]
            m += sho[i, j] <= orders[i, j]
            m += shc[i, j] + sho[i, j] <= p["K"][i, j]
            m += shc[i, j] + sho[i, j] >= st.contract[i, j] + orders[i, j] - p["lost"][i, j]
        arrive = pulp.lpSum((1 - p["lam"][i, j]) * shc[i, j] + (1 - lam_o[i, j]) * sho[i, j] for j in J) + \
            sum(st.pipe[i, j] for j in J)
        m += s[i] == st.stock[i] + arrive + e[i] + z[i] - d4[i]
        m += e[i] <= p["elim"][i]
        m += z[i] <= d4[i]
    purchase = pulp.lpSum(p["cprice"][i, j] * shc[i, j] + p["price"][i, j] * sho[i, j] for i in I for j in J)
    other = pulp.lpSum(hold[i] * s[i] + p["spot"][i] * e[i] + ts.pen_mult[i] * pen[i] * z[i] for i in I)
    # stock left over keeps its value, so the firm does not refuse deliveries it has paid for
    m += purchase + other - pulp.lpSum(cbar[i] * s[i] for i in I)
    m.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus[m.status] == "Optimal", pulp.LpStatus[m.status]
    val = lambda x: max(0.0, x.value() or 0.0)
    out = {
        "contract_cost": sum(p["cprice"][i, j] * val(shc[i, j]) for i in I for j in J),
        "order_cost": sum(p["price"][i, j] * val(sho[i, j]) for i in I for j in J),
        "holding_cost": sum(hold[i] * val(s[i]) for i in I),
        "spot_cost": sum(p["spot"][i] * val(e[i]) for i in I),
        "shortage_penalty": sum(ts.pen_mult[i] * pen[i] * val(z[i]) for i in I),
    }
    for i in I:
        out[f"fill_{i}"] = 1 - val(z[i]) / d4[i]
        out[f"stock_{i}"] = val(s[i])
        out[f"spot_t_{i}"] = val(e[i])
        out[f"short_t_{i}"] = val(z[i])
        for j in J:
            out[f"shc_{i}_{j}"] = val(shc[i, j])
            out[f"sho_{i}_{j}"] = val(sho[i, j])
    nxt = State({i: val(s[i]) for i in I},
                {(i, j): p["lam"][i, j] * val(shc[i, j]) + lam_o[i, j] * val(sho[i, j]) for i in I for j in J},
                dict(st.contract))
    return out, nxt


def initial_state(inp, ts: TierSet) -> State:
    return State({i: ts.floor_t[i] for i in inp.I}, {(i, j): 0.0 for i in inp.I for j in inp.J},
                 {(i, j): 0.0 for i in inp.I for j in inp.J})


def run_path(inp, pol: Policy, path: list, signals: list, triggers: list | None = None, replan: bool = False):
    """Simulate a policy along a realised path of quarters.

    path: list of (active frozenset, severity dict) actually realised each quarter
    signals: list of severity dicts the policy sees when deciding each quarter
    triggers: per quarter, None or (phi, types): an emergency trigger fired after share phi of the quarter
              for the shock types `types` (C and/or E)
    replan: if True, the policy re-plans at a trigger; if False, triggers are ignored

    Emergency re-plan: contracts stay fixed; the policy re-solves with the triggered types treated as
    certain at high severity. The share (1 - phi) of the quarter's flexible orders not yet placed is
    replaced by the new plan, and those orders face their lead time from the trigger date, so a further
    share phi of them arrives only next quarter.
    """
    ts_eval = TierSet.build(inp, uniform=False)   # performance is always judged with the real tier targets
    st = initial_state(inp, ts_eval)
    triggers = triggers or [None] * len(path)
    rows = []
    for q, ((active, sev_real), sev_sig, trig) in enumerate(zip(path, signals, triggers)):
        first_q = q % 4 == 0
        orders, contract, status = decide(inp, pol, st, sev_sig, first_q)
        if first_q:
            st = State(st.stock, st.pipe, contract)
        p = params(inp, active, sev_real)
        lam_order = None
        replanned = False
        if replan and trig is not None:
            phi, types = trig
            sev_hi = dict(sev_sig)
            for t in types:
                sev_hi[t] = "high"
            new, _, status2 = decide(inp, pol, st, sev_hi, False, forced=frozenset(types))
            # the unplaced share (1 - phi) of each order moves to the new plan; only an INCREASE is placed
            # at the trigger date and so waits a further phi of a quarter; unchanged or cut volumes keep timing
            eff = {ij: orders[ij] + (1 - phi) * (new[ij] - orders[ij]) for ij in orders}
            lam_order = {}
            for ij in orders:
                extra = max(0.0, eff[ij] - orders[ij])
                lam_new = min(p["lam"][ij] + phi, 1.0)
                lam_order[ij] = (((eff[ij] - extra) * p["lam"][ij] + extra * lam_new) / eff[ij]
                                 if eff[ij] > 1e-12 else p["lam"][ij])
            orders, status, replanned = eff, f"{status}+{status2}", True
        res, st = settle(inp, ts_eval, st, orders, p, lam_order)
        res.update({"policy": pol.name, "quarter": q, "active": "".join(sorted(active)) or "none", "status": status,
                    "replanned": replanned})
        for i in inp.I:
            tot = sum(orders[i, j] + st.contract[i, j] for j in inp.J)
            for j in inp.J:
                res[f"share_{i}_{j}"] = (orders[i, j] + st.contract[i, j]) / tot if tot > 0 else 0.0
                res[f"contract_{i}_{j}"] = st.contract[i, j]
                res[f"order_{i}_{j}"] = orders[i, j]
        rows.append(res)
    cbar, *_ = common(inp)
    df = pd.DataFrame(rows)
    df["cost"] = df[["contract_cost", "order_cost", "holding_cost", "spot_cost", "shortage_penalty"]].sum(axis=1)
    # credited at the normal China price, as in the planning model (was cbar in run1_prelim and run2)
    c0 = inp.com.base_cost_usd_t / 1000.0
    end_credit = sum(c0[i] * st.stock[i] for i in inp.I) + sum(c0[i] * st.pipe[i, j] for i in inp.I for j in inp.J)
    return df, end_credit


def perfect_foresight(inp, path: list):
    """Lowest cost achievable on a known path, using the same tier settings and starting state."""
    ts = TierSet.build(inp)
    st = initial_state(inp, ts)
    seq = [params(inp, a, s) for a, s in path]
    key = ("pf",)
    o, g, status, obj = plan(inp, ts, st, {key: seq}, [key], {i: set() for i in inp.I}, True, True)
    return obj, status
