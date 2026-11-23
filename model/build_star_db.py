"""Build the project Star Schema database from the files the model uses, and check that it matches them.

Output: model/data/star.db (rebuilt from scratch on every run) and outputs/star_db_check.json.
The original code/sql/thesis.db is read only (for the unit-value bands) and never changed.
No new numbers are created here: every table is loaded from an existing input, data or output file.

Dimensions: dim_commodity, dim_region, dim_partner, dim_time
Facts:      fact_trade, fact_price_history, fact_risk_events, fact_event_labels, fact_signals, fact_model_results
Config:     scenarios_config (shock sizes by type, severity and commodity)

Run: python model/build_star_db.py
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA, INPUTS, NEWS, OUT = HERE / "data", HERE / "inputs", HERE / "news", HERE / "outputs"
DB = DATA / "star.db"
LEGACY = "file:D:/DAB/Thesis/code/sql/thesis.db?mode=ro"

# Region mapping: same as prepare_inputs.py / make_figures.py; partners not listed fall into
# "North America and other" (the rule used for Figure 1.1), flagged as mapping = 'default'.
REGIONS = {
    "China": [156],
    "Asia excl. China": [699, 608, 704, 410, 764, 458, 490, 398, 116],
    "Europe": [380, 233, 40, 276, 251, 826, 579, 643, 792, 203, 56, 51, 757],
    "North America and other": [842, 124],
}
PARTNER_NAMES = {
    156: "China", 699: "India", 608: "Philippines", 704: "Viet Nam", 410: "Rep. of Korea", 764: "Thailand",
    458: "Malaysia", 490: "Other Asia, nes", 398: "Kazakhstan", 116: "Cambodia", 380: "Italy", 233: "Estonia",
    40: "Austria", 276: "Germany", 251: "France", 826: "United Kingdom", 579: "Norway", 643: "Russian Federation",
    792: "Türkiye", 203: "Czechia", 56: "Belgium", 51: "Armenia", 757: "Switzerland", 842: "USA", 124: "Canada",
}
CMD = {"284690": "Rare-earth compounds", "280530": "Rare-earth metals", "850511": "Permanent magnets"}
LEGACY_ID = {"NdPr": "NDPR_OXIDE", "Dy": "DY_OXIDE", "Tb": "TB_OXIDE"}

SCHEMA = """
CREATE TABLE dim_commodity (
  commodity_id TEXT PRIMARY KEY, display_name TEXT, hs_code TEXT, category TEXT,
  tier INTEGER, eu_supply_risk REAL, eu_economic_importance REAL,
  china_cap REAL, stock_floor_weeks REAL, shortage_penalty_mult REAL, fill_target REAL,
  demand_t REAL, base_cost_usd_t REAL, uv_band_min_usd_kg REAL, uv_band_max_usd_kg REAL, source TEXT);
CREATE TABLE dim_region (
  region TEXT PRIMARY KEY, cost_premium REAL, cap_NdPr REAL, cap_Dy REAL, cap_Tb REAL);
CREATE TABLE dim_partner (
  partner_code INTEGER PRIMARY KEY, partner_name TEXT, region TEXT REFERENCES dim_region(region), mapping TEXT);
CREATE TABLE dim_time (
  month_id TEXT PRIMARY KEY, year INTEGER, quarter TEXT, month INTEGER);
CREATE TABLE fact_trade (
  month_id TEXT REFERENCES dim_time(month_id), cmd_code TEXT, cmd_name TEXT,
  partner_code INTEGER REFERENCES dim_partner(partner_code),
  trade_value_usd REAL, net_weight_kg REAL, PRIMARY KEY (month_id, cmd_code, partner_code));
CREATE TABLE fact_price_history (
  year INTEGER, commodity_id TEXT REFERENCES dim_commodity(commodity_id), price_usd_kg REAL,
  usgs_series TEXT, usgs_edition INTEGER, estimated INTEGER, basis TEXT, PRIMARY KEY (year, commodity_id));
CREATE TABLE scenarios_config (
  shock TEXT, severity TEXT, commodity TEXT, parameter TEXT, value REAL, basis TEXT,
  PRIMARY KEY (shock, severity, commodity, parameter));
CREATE TABLE fact_risk_events (
  article_id TEXT PRIMARY KEY, date TEXT, quarter TEXT, headline TEXT, outlet TEXT, url TEXT,
  source_type TEXT, collected_by TEXT, retrieved_on TEXT);
CREATE TABLE fact_event_labels (
  article_id TEXT REFERENCES fact_risk_events(article_id), label_source TEXT,
  relevant TEXT, shock_type TEXT, severity TEXT, affects_china TEXT, model TEXT,
  PRIMARY KEY (article_id, label_source));
CREATE TABLE fact_signals (
  quarter TEXT, label_source TEXT, shock_type TEXT, severity TEXT, PRIMARY KEY (quarter, label_source, shock_type));
CREATE TABLE fact_model_results (
  run TEXT, policy TEXT, scenario TEXT, replan INTEGER, net_cost_kusd REAL, regret_kusd REAL,
  shortage_t REAL, spot_t REAL, survive_all INTEGER, min_fill_NdPr REAL, min_fill_Dy REAL, min_fill_Tb REAL,
  PRIMARY KEY (run, policy, scenario, replan));
"""


def main():
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    ins = lambda table, df: df.to_sql(table, con, if_exists="append", index=False)

    # ---- dimensions
    comm, tiers = pd.read_csv(INPUTS / "commodities.csv"), pd.read_csv(INPUTS / "tiers.csv")
    legacy = sqlite3.connect(LEGACY, uri=True)
    bands = pd.read_sql("select commodity_id, display_name, hs_code_proxy, category, filter_min_uv, filter_max_uv "
                        "from dim_commodities", legacy).set_index("commodity_id")
    legacy.close()
    c = comm.merge(tiers, on="tier")
    rows = []
    for r in c.itertuples():
        b = bands.loc[LEGACY_ID[r.commodity]]
        rows.append({"commodity_id": r.commodity, "display_name": b.display_name, "hs_code": b.hs_code_proxy,
                     "category": b.category, "tier": r.tier, "eu_supply_risk": r.eu_sr, "eu_economic_importance": r.eu_ei,
                     "china_cap": r.cap_U, "stock_floor_weeks": r.floor_weeks, "shortage_penalty_mult": r.penalty_mult,
                     "fill_target": r.fill_target, "demand_t": r.demand_t, "base_cost_usd_t": r.base_cost_usd_t,
                     "uv_band_min_usd_kg": b.filter_min_uv, "uv_band_max_usd_kg": b.filter_max_uv,
                     "source": "inputs/commodities.csv, inputs/tiers.csv; unit-value band from code/sql/thesis.db"})
    b = bands.loc["NDFEB_MAGNET"]
    rows.append({"commodity_id": "NdFeB_magnet", "display_name": b.display_name, "hs_code": b.hs_code_proxy,
                 "category": b.category, "source": "code/sql/thesis.db (downstream; not in the optimization model)"})
    ins("dim_commodity", pd.DataFrame(rows))
    regions = pd.read_csv(INPUTS / "regions.csv")
    ins("dim_region", regions)

    tr = sqlite3.connect(f"file:{DATA / 'comtrade_extended.db'}?mode=ro", uri=True)
    raw = pd.read_sql("select period, cmd_code, partner_code, trade_value_usd, net_weight_kg from imports", tr)
    tr.close()
    region_of = {p: r for r, ps in REGIONS.items() for p in ps}
    partners = sorted(set(raw.partner_code) - {0})
    ins("dim_partner", pd.DataFrame({
        "partner_code": partners, "partner_name": [PARTNER_NAMES.get(p) for p in partners],
        "region": [region_of.get(p, "North America and other") for p in partners],
        "mapping": ["listed" if p in region_of else "default" for p in partners]}))
    months = pd.period_range("2010-01", "2025-12", freq="M")
    ins("dim_time", pd.DataFrame({"month_id": months.strftime("%Y%m"), "year": months.year,
                                  "quarter": [f"{p.year}Q{p.quarter}" for p in months], "month": months.month}))

    # ---- facts
    t = raw[raw.partner_code != 0].rename(columns={"period": "month_id"})       # World total excluded
    t["cmd_name"] = t.cmd_code.map(CMD)
    ins("fact_trade", t[["month_id", "cmd_code", "cmd_name", "partner_code", "trade_value_usd", "net_weight_kg"]])
    p = pd.read_csv(DATA / "usgs_oxide_prices.csv")
    ins("fact_price_history", pd.DataFrame({"year": p.year, "commodity_id": p.commodity, "price_usd_kg": p.price_usd_kg,
                                            "usgs_series": p.usgs_series, "usgs_edition": p.edition,
                                            "estimated": p.estimated.astype(int), "basis": p.basis}))
    ins("scenarios_config", pd.read_csv(INPUTS / "shocks.csv"))

    art = pd.read_csv(NEWS / "news_articles.csv", dtype=str, encoding="utf-8-sig").fillna("")
    q = pd.PeriodIndex(pd.to_datetime(art.date.str[:7], format="%Y-%m", errors="coerce"), freq="Q")
    art["quarter"] = [f"{x.year}Q{x.quarter}" if pd.notna(x) else None for x in q]
    ins("fact_risk_events", art[["article_id", "date", "quarter", "headline", "outlet", "url", "source_type",
                                 "collected_by", "retrieved_on"]])
    fields = ["relevant", "shock_type", "severity", "affects_china"]
    sources = [("hand", "hand_labels.csv", None), ("keyword", "keyword_labels.csv", None),
               ("nvidia", "nvidia_labels.csv", "nvidia_run_info.json"), ("laya", "laya_labels.csv", "laya_run_info.json"),
               ("llm", "ai_labels.csv", "llm_run_info.json")]
    n_labels = {}
    for name, f, info in sources:
        if not (NEWS / f).exists():
            continue
        lab = pd.read_csv(NEWS / f, dtype=str, encoding="utf-8-sig").fillna("")
        lab = lab[(lab[fields] != "").all(axis=1)]                              # complete labels only
        model = json.loads((NEWS / info).read_text())["model" if name != "llm" else "requested_model"] \
            if info and (NEWS / info).exists() else ("author" if name == "hand" else "rules" if name == "keyword" else "")
        lab = lab.assign(label_source=name, model=model)[["article_id", "label_source"] + fields + ["model"]]
        ins("fact_event_labels", lab)
        n_labels[name] = len(lab)
    n_sig = 0
    for f in sorted((OUT / "nlp").glob("signals_*.csv")) if (OUT / "nlp").exists() else []:
        s = pd.read_csv(f).melt(id_vars="quarter", var_name="shock_type", value_name="severity")
        s["quarter"] = s.quarter.str.replace("Q", "Q")
        ins("fact_signals", s.assign(label_source=f.stem.removeprefix("signals_"))[["quarter", "label_source", "shock_type", "severity"]])
        n_sig += len(s)

    keep = ["policy", "scenario", "net_cost_kusd", "regret_kusd", "shortage_t", "spot_t", "survive_all",
            "min_fill_NdPr", "min_fill_Dy", "min_fill_Tb"]
    sp = pd.read_csv(OUT / "run3" / "stress_paths.csv")[keep].assign(run="run3", replan=0)
    rp = pd.read_csv(OUT / "replan2" / "replan_paths.csv").assign(run="replan2")
    rp["replan"] = rp.replan.astype(str).eq("True").astype(int)
    for col in ("regret_kusd", "survive_all"):
        rp[col] = None
    sp["survive_all"] = sp.survive_all.astype(str).eq("True").astype(int)
    ins("fact_model_results", pd.concat([sp, rp[sp.columns]]))
    con.commit()

    # ---- checks: the database reproduces the model's inputs and the published figures
    chk = {}
    dbp = pd.read_sql("select year, commodity_id as commodity, price_usd_kg from fact_price_history", con)
    m = p.merge(dbp, on=["year", "commodity"], suffixes=("", "_db"))
    chk["prices_match_usgs_csv"] = bool(len(m) == len(p) and (m.price_usd_kg - m.price_usd_kg_db).abs().max() < 1e-9)
    dbs = pd.read_sql("select * from scenarios_config", con)
    chk["shocks_match_inputs"] = bool(dbs.equals(pd.read_csv(INPUTS / "shocks.csv")[dbs.columns]))
    dbc = pd.read_sql("select commodity_id, tier, china_cap, stock_floor_weeks, fill_target, demand_t from dim_commodity "
                      "where tier is not null order by commodity_id", con)
    exp = c.rename(columns={"commodity": "commodity_id", "cap_U": "china_cap", "floor_weeks": "stock_floor_weeks"}) \
        .sort_values("commodity_id")[dbc.columns].reset_index(drop=True)
    chk["commodities_match_inputs"] = bool((dbc.astype(float, errors="ignore").values == exp.values).all())
    shares = pd.read_sql("""select substr(t.month_id,1,4) year, p.region, sum(t.net_weight_kg) w from fact_trade t
                            join dim_partner p using(partner_code) where t.cmd_code='284690' group by 1,2""", con)
    shares["share"] = shares.w / shares.groupby("year").w.transform("sum") * 100
    fig = pd.read_csv(OUT / "figures" / "fig1_1_data.csv").melt(id_vars="year", var_name="region", value_name="share_fig")
    fig["year"] = fig.year.astype(str)
    mm = fig.merge(shares, on=["year", "region"], how="left").fillna({"share": 0})
    chk["region_shares_match_figure_1_1"] = bool((mm.share.round(2) - mm.share_fig).abs().max() < 0.006)
    chk["world_rows_excluded"] = int(con.execute("select count(*) from fact_trade where partner_code=0").fetchone()[0]) == 0
    counts = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in
              ["dim_commodity", "dim_region", "dim_partner", "dim_time", "fact_trade", "fact_price_history",
               "scenarios_config", "fact_risk_events", "fact_event_labels", "fact_signals", "fact_model_results"]}
    con.close()
    report = {"database": str(DB.relative_to(HERE.parent)), "checks": chk, "all_pass": all(chk.values()),
              "rows": counts, "labels_by_source": n_labels,
              "partners_mapped_by_default": int(sum(p not in region_of for p in partners))}
    (OUT / "star_db_check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
