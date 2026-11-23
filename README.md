# Robust Sourcing of Rare Earth Elements

Solution code and current results for the MSc thesis *AI and Data-Driven Risk Management in Supply Chains: A Robust Sourcing Strategy for Rare Earth Elements* (KEDGE Business School, 2026).

**Status:** work in progress. Code under the MIT License (see `LICENSE`); data files keep the terms of their sources (UN Comtrade, USGS, European Commission).

## What it does

A decision model for an importer in Japan that buys NdPr, dysprosium and terbium from four supplier regions.

- **Data layer.** UN Comtrade import data, USGS oxide prices from 2010 to 2024, and the EU 2023 criticality scores set the model inputs.
- **NLP layer.** News headlines are classified into shock type, severity and whether China is affected. Three classifiers are compared: a keyword baseline, Nemotron (an open-weight LLM via the NVIDIA API) and Laya (a local open-weight model). The labels set the quarterly shock severity and trigger emergency re-plans.
- **Robust optimization.** A linear program (PuLP + CBC) chooses annual contracts, quarterly orders and stock. It minimises the worst-case cost over all combinations of up to Γ shock types and is re-solved every quarter.
- **Evaluation.** A stress test over 16 scenarios and a news-triggered re-plan. Results are compared with an expected-value model, a dual-sourcing rule and the status quo.

## Layout

| Path | Content |
|---|---|
| `worked_example/model.py` | Planning and settlement model |
| `worked_example/prepare_inputs.py` | Builds `inputs/` from the source data |
| `worked_example/run_stress.py`, `run_replan.py` | Stress test and re-plan runs |
| `worked_example/check_model.py` | Independent checks of the model |
| `worked_example/nlp_classify.py`, `evaluate_nlp.py` | News classifiers and their evaluation |
| `worked_example/build_star_db.py` | Star Schema database, built from the same files |
| `worked_example/fetch_comtrade_extended.py` | Fetches the UN Comtrade data (needs your own key) |
| `worked_example/inputs/` | Model inputs; assumptions are labelled in `inputs_notes.md` |
| `worked_example/outputs/run3/`, `replan2/` | Current stress-test and re-plan results, each with `run_info.json` |
| `worked_example/outputs/checks/check_report.json` | Results of the independent model checks |
| `worked_example/news/labeling_guide.md` | Label definitions used by the classifiers and the hand labels |

## Not included

- API keys. They are read at run time from a local `.env` file outside the repository.
- Raw UN Comtrade data and the database built from it. Re-fetch them with `fetch_comtrade_extended.py`, using your own key.
- USGS PDFs. These are public domain and can be downloaded from usgs.gov.
- Superseded runs, data extracts and the news article table.

## Run

Python 3.12. Install with `pip install -r requirements.txt`, then from the repository root:

```
python worked_example/prepare_inputs.py
python worked_example/run_stress.py
python worked_example/check_model.py
```

Each run writes its results and a `run_info.json` to `worked_example/outputs/`. Every number reported in the thesis comes from a file there.
