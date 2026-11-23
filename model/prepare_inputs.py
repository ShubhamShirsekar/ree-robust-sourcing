"""Build model inputs from public data.

Reads thesis.db (read-only) and the USGS Mineral Commodity Summaries PDFs,
writes data extracts to data/ and model inputs to inputs/.
Every assumed value is marked in inputs/*.csv with basis = "assumption".

Run: python -I model/prepare_inputs.py
"""
import json
import logging
import re
import sqlite3
from pathlib import Path

import pandas as pd
import pypdf

logging.disable(logging.CRITICAL)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
INPUTS = HERE / "inputs"
DB = "file:D:/DAB/Thesis/code/sql/thesis.db?mode=ro"
MCS = Path("D:/DAB/Thesis/datasets/Mineral Commodity Summaries")

# UN M49 partner codes found in raw_comtrade_imports, grouped into model regions
REGIONS = {
    "China": [156],
    "Asia excl. China": [699, 608, 704, 410, 764, 458, 490, 398, 116],
    "Europe": [380, 233, 40, 276, 251, 826, 579, 643, 792, 203, 56, 51, 757],
    "North America and other": [842, 124],
}
PARTNER_NAMES = {
    156: "China", 699: "India", 608: "Philippines", 704: "Viet Nam", 410: "Rep. of Korea",
    764: "Thailand", 458: "Malaysia", 490: "Other Asia, nes", 398: "Kazakhstan",
    116: "Cambodia", 380: "Italy", 233: "Estonia", 40: "Austria", 276: "Germany",
    251: "France", 826: "United Kingdom", 579: "Norway", 643: "Russian Federation",
    792: "Türkiye", 203: "Czechia", 56: "Belgium", 51: "Armenia", 757: "Switzerland",
    842: "USA", 124: "Canada",
}
COMMODITIES = ["NdPr", "Dy", "Tb"]
USGS_ELEMENT = {"NdPr": "Neodymium", "Dy": "Dysprosium", "Tb": "Terbium"}
# Latest USGS edition reporting each year (later editions revise earlier years)
EDITIONS = {2025: "mcs2025-rare-earths.pdf", 2024: "mcs2024-rare-earths.pdf",
            2023: "mcs2023-rare-earths.pdf", 2022: "mcs2022-rare-earths.pdf",
            2021: "mcs2021-rare-earths.pdf"}
# Older editions (downloaded from USGS, 2026-11-22) report YEAREND price RANGES, not annual averages.
# Used only for 2010-2015 (author decision 2026-11-22: extend price-shock calibration to 2010); midpoint taken.
MCS_OLD = HERE / "data" / "usgs_mcs_2012_2018"
OLD_EDITIONS = {2018: "mcs-2018-raree.pdf", 2017: "mcs-2017-raree.pdf",
                2016: "mcs-2016-raree.pdf", 2015: "mcs-2015-raree.pdf"}
BASE_COST_YEARS = (2016, 2024)   # no-shock cost: mean of annual averages (approved 2026-11-21)
CALIB_FIRST_YEAR = 2010          # price-shock calibration window starts here


def comtrade():
    con = sqlite3.connect(DB, uri=True)
    raw = pd.read_sql("select * from raw_comtrade_imports", con)
    bands = pd.read_sql("select commodity_id, filter_min_uv, filter_max_uv from dim_commodities "
                        "where hs_code_proxy = '284690'", con)
    con.close()

    world = raw[raw.partner_code == 0].groupby("period")[["trade_value_usd", "net_weight_kg"]].sum()
    d = raw[raw.partner_code != 0].copy()
    parts = d.groupby("period")[["trade_value_usd", "net_weight_kg"]].sum()
    world_check = float((world - parts.loc[world.index]).abs().max().max())

    region_of = {c: r for r, codes in REGIONS.items() for c in codes}
    d["region"] = d.partner_code.map(region_of)
    assert d.region.notna().all(), "unmapped partner code"
    d["partner"] = d.partner_code.map(PARTNER_NAMES)
    d["year"] = d.period.str[:4].astype(int)
    d["unit_value"] = d.trade_value_usd / d.net_weight_kg
    d.drop(columns=["ingestion_timestamp"]).to_csv(DATA / "comtrade_jpn_284690_partners.csv", index=False)

    ry = d.groupby(["year", "region"])[["trade_value_usd", "net_weight_kg"]].sum().reset_index()
    ry["tonnes"] = ry.net_weight_kg / 1000
    ry["unit_value_usd_kg"] = ry.trade_value_usd / ry.net_weight_kg
    ry["weight_share"] = ry.net_weight_kg / ry.groupby("year").net_weight_kg.transform("sum")
    ry["value_share"] = ry.trade_value_usd / ry.groupby("year").trade_value_usd.transform("sum")
    ry.to_csv(DATA / "comtrade_region_year.csv", index=False)

    total = d[["trade_value_usd", "net_weight_kg"]].sum()
    china = d[d.region == "China"][["trade_value_usd", "net_weight_kg"]].sum()

    rows = []
    for _, b in bands.iterrows():
        m = d.unit_value.between(b.filter_min_uv, b.filter_max_uv)
        rows.append({"commodity_id": b.commodity_id, "band_usd_kg": f"{b.filter_min_uv:g}-{b.filter_max_uv:g}",
                     "rows": int(m.sum()), "weight_share_retained": d.net_weight_kg[m].sum() / total.net_weight_kg,
                     "value_share_retained": d.trade_value_usd[m].sum() / total.trade_value_usd})
    any_band = pd.concat([d.unit_value.between(b.filter_min_uv, b.filter_max_uv) for _, b in bands.iterrows()], axis=1).any(axis=1)
    rows.append({"commodity_id": "ANY", "band_usd_kg": "", "rows": int(any_band.sum()),
                 "weight_share_retained": d.net_weight_kg[any_band].sum() / total.net_weight_kg,
                 "value_share_retained": d.trade_value_usd[any_band].sum() / total.trade_value_usd})
    pd.DataFrame(rows).to_csv(DATA / "band_retention.csv", index=False)

    summary = {
        "rows_excluding_world": len(d),
        "months": d.period.nunique(),
        "partners": d.partner_code.nunique(),
        "period": [d.period.min(), d.period.max()],
        "world_total_max_abs_difference": world_check,
        "china_value_share": china.trade_value_usd / total.trade_value_usd,
        "china_weight_share": china.net_weight_kg / total.net_weight_kg,
    }
    return ry, summary


def usgs_prices():
    rows = []
    for edition_year, fname in EDITIONS.items():
        text = " ".join((p.extract_text() or "") for p in pypdf.PdfReader(MCS / fname).pages)
        text = re.sub(r"\s+", " ", text)
        years = [int(y) for y in re.search(r"Salient Statistics.{0,30}?((?:20\d\d\s?e?\s){5})", text).group(1).replace("e", "").split()]
        for com, el in USGS_ELEMENT.items():
            vals = re.search(el + r" oxide,? ?[\d.]+ ?% minimum ?((?:[\d,]+ ){5})", text).group(1).split()
            for y, v in zip(years, vals):
                rows.append({"edition": edition_year, "year": y, "commodity": com,
                             "usgs_series": f"{el} oxide", "price_usd_kg": float(v.replace(",", "")),
                             "estimated": y == years[-1], "basis": "annual average"})
    for edition_year, fname in OLD_EDITIONS.items():
        text = " ".join((p.extract_text() or "") for p in pypdf.PdfReader(MCS_OLD / fname).pages)
        text = re.sub(r"\s+", " ", text)
        years = [int(y) for y in re.search(r"Salient Statistics.{0,30}?((?:20\d\d\s?e?\s){5})", text).group(1).replace("e", "").split()]
        for com, el in USGS_ELEMENT.items():
            vals = re.search(el + r" oxide,? [\d.]+ ?% minimum ((?:[\d,]+(?:–[\d,]+)? ){5})", text).group(1).split()
            for y, v in zip(years, vals):
                lo, _, hi = v.replace(",", "").partition("–")
                mid = (float(lo) + float(hi or lo)) / 2
                rows.append({"edition": edition_year, "year": y, "commodity": com,
                             "usgs_series": f"{el} oxide", "price_usd_kg": mid,
                             "estimated": y == years[-1], "basis": "yearend range midpoint"})
    allp = pd.DataFrame(rows)
    allp.to_csv(DATA / "usgs_oxide_prices_all_editions.csv", index=False)
    # Keep each year's value from the latest edition that reports it
    latest = allp.sort_values("edition").groupby(["commodity", "year"]).tail(1).sort_values(["commodity", "year"])
    latest = latest[latest.year >= CALIB_FIRST_YEAR]
    latest.to_csv(DATA / "usgs_oxide_prices.csv", index=False)
    return allp, latest


def build_inputs(prices):
    # --- Commodities ---------------------------------------------------------
    p = prices.pivot(index="year", columns="commodity", values="price_usd_kg")
    yoy = p.pct_change().dropna()
    base = p.loc[BASE_COST_YEARS[0]:BASE_COST_YEARS[1]].mean()  # mean of annual averages, 2016-2024
    commodities = pd.DataFrame({
        "commodity": COMMODITIES,
        "tier": [3, 1, 2],
        "eu_sr": [3.85, 5.6, 4.9],
        "eu_ei": [7.1, 7.8, 6.4],
        "demand_t": [300.0, 30.0, 6.0],
        "base_cost_usd_t": [base[c] * 1000 for c in COMMODITIES],
    })
    commodities.to_csv(INPUTS / "commodities.csv", index=False)

    calib = []
    for c in COMMODITIES:
        ups = yoy[c][yoy[c] > 0]
        calib.append({"commodity": c, "n_years": len(yoy), "n_increases": len(ups),
                      "low": ups.min(), "medium": ups.median(), "high": ups.max(),
                      "increase_years": " ".join(str(y) for y in ups.index)})
    calib = pd.DataFrame(calib)
    calib.to_csv(DATA / "price_shock_calibration.csv", index=False)

    # --- Regions: cost premium over China and no-shock capacity ----------------
    regions = pd.DataFrame([
        {"region": "China", "cost_premium": 0.00, "cap_NdPr": 1.00, "cap_Dy": 1.00, "cap_Tb": 1.00},
        {"region": "Asia excl. China", "cost_premium": 0.10, "cap_NdPr": 0.50, "cap_Dy": 0.20, "cap_Tb": 0.20},
        {"region": "Europe", "cost_premium": 0.20, "cap_NdPr": 0.20, "cap_Dy": 0.10, "cap_Tb": 0.10},
        {"region": "North America and other", "cost_premium": 0.25, "cap_NdPr": 0.30, "cap_Dy": 0.10, "cap_Tb": 0.10},
    ])
    regions.to_csv(INPUTS / "regions.csv", index=False)

    # --- Tiers ---------------------------------------------------------------
    tiers = pd.DataFrame([
        {"tier": 1, "cap_U": 0.60, "floor_weeks": 8, "penalty_mult": 3.0, "fill_target": 0.95, "extra_budget": 1},
        {"tier": 2, "cap_U": 0.70, "floor_weeks": 6, "penalty_mult": 2.5, "fill_target": 0.92, "extra_budget": 1},
        {"tier": 3, "cap_U": 0.80, "floor_weeks": 4, "penalty_mult": 1.0, "fill_target": 0.90, "extra_budget": 0},
    ])
    tiers.to_csv(INPUTS / "tiers.csv", index=False)

    # --- Shock magnitudes by severity ------------------------------------------
    shocks = []
    for sev in ["low", "medium", "high"]:
        for c in COMMODITIES:
            shocks.append({"shock": "P", "severity": sev, "commodity": c, "parameter": "China price increase",
                           "value": float(calib.set_index("commodity").loc[c, sev]), "basis": f"USGS annual price increases, {CALIB_FIRST_YEAR + 1}-{yoy.index.max()} (2010-2015 yearend midpoints)"})
        shocks.append({"shock": "P", "severity": sev, "commodity": "all", "parameter": "spillover to other regions (share of China increase)",
                       "value": 0.5, "basis": "assumption"})
        shocks.append({"shock": "C", "severity": sev, "commodity": "all", "parameter": "China capacity reduction",
                       "value": {"low": 0.3, "medium": 0.5, "high": 0.8}[sev], "basis": "assumption"})
        shocks.append({"shock": "E", "severity": sev, "commodity": "all", "parameter": "China lead time added (weeks)",
                       "value": {"low": 4, "medium": 8, "high": 12}[sev], "basis": "assumption"})
        shocks.append({"shock": "R", "severity": sev, "commodity": "all", "parameter": "Europe and North America lead time added (weeks)",
                       "value": {"low": 3, "medium": 6, "high": 9}[sev], "basis": "assumption"})
        shocks.append({"shock": "R", "severity": sev, "commodity": "all", "parameter": "China and Asia lead time added (weeks)",
                       "value": {"low": 1, "medium": 2, "high": 3}[sev], "basis": "assumption"})
        shocks.append({"shock": "R", "severity": sev, "commodity": "all", "parameter": "Europe and North America landed cost increase",
                       "value": {"low": 0.05, "medium": 0.10, "high": 0.20}[sev], "basis": "assumption"})
        shocks.append({"shock": "R", "severity": sev, "commodity": "all", "parameter": "China and Asia landed cost increase",
                       "value": {"low": 0.02, "medium": 0.03, "high": 0.05}[sev], "basis": "assumption"})
    pd.DataFrame(shocks).to_csv(INPUTS / "shocks.csv", index=False)

    general = {
        "horizon_weeks": 52,
        "spot_price_multiplier": 1.5,
        "spot_limit_share_of_demand": 0.10,
        "holding_cost_rate": 0.20,
        "shortage_penalty_multiple_of_cost": 3.0,
        "working_capital_weeks_of_demand_value": 26,
        "min_share_L": 0.0,
        "default_severity": "medium",
        "gamma_levels": [0, 1, 2, 3, 4],
        "ev_shock_probability": 0.20,
        "dual_sourcing": {"China": 0.80, "alternative": "lowest-cost non-China region", "alternative_share": 0.20, "stock_weeks": 6},
        "contract_premium": 0.05,
        "service_slack_penalty_multiple_of_spot": 10.0,
        "tie_break_weight_no_shock_cost": 0.001,
        # Planning scenarios: a shock lasts this many quarters from the decision quarter, then ends
        # (author decision 2026-11-22, matched to the stress-test shocks; was 4 = whole horizon in run1_prelim)
        "shock_duration_quarters": 2,
    }
    # Status quo: observed 2025 import weight shares by region (all HS 284690 flows)
    ry = pd.read_csv(DATA / "comtrade_region_year.csv")
    general["status_quo_shares"] = ry[ry.year == 2025].set_index("region").weight_share.round(4).to_dict()
    (INPUTS / "general.json").write_text(json.dumps(general, indent=2), encoding="utf-8")
    return commodities, calib, yoy


if __name__ == "__main__":
    ry, summary = comtrade()
    allp, prices = usgs_prices()
    commodities, calib, yoy = build_inputs(prices)
    (DATA / "comtrade_summary.json").write_text(json.dumps(summary, indent=2, default=float), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=float))
    print(pd.read_csv(DATA / "band_retention.csv").round(3).to_string())
    print(prices.pivot(index="year", columns="commodity", values="price_usd_kg").to_string())
    print(yoy.round(3).to_string())
    print(calib.round(3).to_string())
    print(commodities.to_string())
