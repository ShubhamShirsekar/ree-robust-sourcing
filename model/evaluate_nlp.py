"""Evaluate machine labels against the author's hand labels, and turn labels into model signals.

1. Accuracy: for each method (keyword baseline, LLM), relevance accuracy / precision / recall / F1,
   shock-type accuracy and severity accuracy on articles both sides call relevant, affects-China
   accuracy, and confusion matrices. Reported as they come out, including weak results.
2. Signals: for each label source (hand, keyword, llm), the highest severity per shock type per quarter
   (the next quarter's decision uses it; medium where there is no article), and emergency triggers:
   two high-severity C or E articles affecting China within 7 days (labeling_guide.md Section 3).

Output: outputs/nlp/nlp_metrics.json, confusion_<method>_<field>.csv, signals_<source>.csv, triggers_<source>.csv
Run: python -I model/evaluate_nlp.py [--news=DIR] [--out=DIR]
"""
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
arg = lambda k, d: next((Path(a.split("=", 1)[1]) for a in sys.argv if a.startswith(f"--{k}=")), d)
NEWS = arg("news", HERE / "news")
OUT = arg("out", HERE / "outputs" / "nlp")
OUT.mkdir(parents=True, exist_ok=True)
FIELDS = ["relevant", "shock_type", "severity", "affects_china"]
RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}

art = pd.read_csv(NEWS / "news_articles.csv", dtype=str, encoding="utf-8-sig")
hand = pd.read_csv(NEWS / "hand_labels.csv", dtype=str, encoding="utf-8-sig")
sources = {"hand": hand}
for name, f in [("keyword", "keyword_labels.csv"), ("nvidia", "nvidia_labels.csv"), ("laya", "laya_labels.csv"),
                ("llm", "ai_labels.csv")]:
    if (NEWS / f).exists():
        sources[name] = pd.read_csv(NEWS / f, dtype=str, encoding="utf-8-sig")


def prf(y, p):
    tp = ((y == "Y") & (p == "Y")).sum()
    fp = ((y == "N") & (p == "Y")).sum()
    fn = ((y == "Y") & (p == "N")).sum()
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else float("nan")
    return {"accuracy": float((y == p).mean()), "precision": float(prec), "recall": float(rec), "f1": float(f1)}


metrics = {"n_articles": len(art), "n_hand_relevant": int((hand.relevant == "Y").sum())}
for name in ("keyword", "nvidia", "laya", "llm"):
    if name not in sources:
        continue
    m = hand[["article_id"] + FIELDS].merge(sources[name][["article_id"] + FIELDS], on="article_id",
                                            suffixes=("_hand", "_pred"), how="left")
    m[[f + "_pred" for f in FIELDS]] = m[[f + "_pred" for f in FIELDS]].fillna("missing")
    r = {"relevance": prf(m.relevant_hand, m.relevant_pred),
         "affects_china_accuracy": float((m.affects_china_hand == m.affects_china_pred).mean())}
    both = m[(m.relevant_hand == "Y") & (m.relevant_pred == "Y")]
    r["n_both_relevant"] = len(both)
    r["shock_type_accuracy_on_both_relevant"] = float((both.shock_type_hand == both.shock_type_pred).mean()) if len(both) else None
    r["severity_accuracy_on_both_relevant"] = float((both.severity_hand == both.severity_pred).mean()) if len(both) else None
    sev_dist = (both.severity_pred.map(RANK) - both.severity_hand.map(RANK)).abs()
    r["severity_within_one_level"] = float((sev_dist <= 1).mean()) if len(both) else None
    metrics[name] = r
    for f in ("relevant", "shock_type", "severity"):
        pd.crosstab(m[f + "_hand"], m[f + "_pred"], rownames=["hand"], colnames=[name]) \
            .to_csv(OUT / f"confusion_{name}_{f}.csv")

# ------------------------------------------------------------------ signals and triggers
art["day"] = pd.to_datetime(art.date, format="%Y-%m-%d", errors="coerce")
art["quarter"] = pd.PeriodIndex(pd.to_datetime(art.date.str[:7], format="%Y-%m", errors="coerce"), freq="Q")
undated = art[art.quarter.isna()].article_id.tolist()
metrics["articles_without_month"] = undated
for name, lab in sources.items():
    d = art.merge(lab[["article_id"] + FIELDS], on="article_id")
    d = d[(d.relevant == "Y") & d.quarter.notna()]
    d["rank"] = d.severity.map(RANK)
    sig = d.pivot_table(index="quarter", columns="shock_type", values="rank", aggfunc="max")
    sig = sig.reindex(columns=["P", "C", "E", "R"]).fillna(RANK["medium"]).astype(int)
    sig = sig.replace({v: k for k, v in RANK.items()})
    sig.to_csv(OUT / f"signals_{name}.csv")
    hi = d[d.shock_type.isin(["C", "E"]) & (d.severity == "high") & (d.affects_china == "Y") & d.day.notna()] \
        .sort_values("day")
    trig, last = [], None
    for _, row in hi.iterrows():
        prior = hi[(hi.day < row.day) & (hi.day >= row.day - pd.Timedelta(days=7))]
        if len(prior) and (last is None or row.day > last + pd.Timedelta(days=7)):
            trig.append({"trigger_date": row.day.date().isoformat(), "confirming_article": row.article_id,
                         "first_article": prior.iloc[0].article_id, "types": "".join(sorted({row.shock_type, prior.iloc[0].shock_type}))})
            last = row.day
    pd.DataFrame(trig, columns=["trigger_date", "confirming_article", "first_article", "types"]) \
        .to_csv(OUT / f"triggers_{name}.csv", index=False)
    metrics[f"triggers_{name}"] = len(trig)
    metrics[f"high_CE_china_without_full_date_{name}"] = int(
        (d.shock_type.isin(["C", "E"]) & (d.severity == "high") & (d.affects_china == "Y") & d.day.isna()).sum())

(OUT / "nlp_metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
print(json.dumps(metrics, indent=2, default=str))
