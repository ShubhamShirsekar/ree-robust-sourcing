# News Table and Labeling Guide

*Fixed on 2026-11-21, before any AI classification. If you change a rule, log the change and its reason in `memory/decisions_log.md`, and apply it only to articles not yet labeled.*

## 1. The two files

| File | Who fills it | Content |
|---|---|---|
| `news_articles.csv` | Collected by you or the workspace; **every row confirmed by you** | One real article per row |
| `hand_labels.csv` | **You only**, before the AI classification is run | Your label for each article |

The AI output will go in a third file, `ai_labels.csv`, created only after your labels are complete. Keeping it separate means your labels cannot be influenced by the AI's.

### `news_articles.csv` columns

| Column | Rule |
|---|---|
| `article_id` | N0001, N0002, … in the order collected |
| `date` | Publication date, YYYY-MM-DD |
| `headline` | Exactly as published |
| `outlet` | e.g. Reuters, Nikkei Asia, Financial Times, Mining.com, an official government site |
| `url` | Link to the article or archive page |
| `retrieved_on` | Date you opened and checked the link |
| `collected_by` | `author` or `workspace` |
| `source_type` | `news`, `official` (government or agency notice) or `report` (e.g. a USGS annual summary) |
| `notes` | Optional |

**Rules for the article table:**
- Only real articles that you have opened. No headline may be invented, paraphrased or reconstructed from memory.
- The headline is stored as published, and the video shows only the headline, outlet and date.
- Include articles that turn out to be irrelevant, such as general REE market news. The AI must learn to reject these, and the accuracy check needs them.
- Aim for coverage of every year from 2010 to 2025, with more articles around known events. A target of about 150 articles fits the time available.

### `hand_labels.csv` columns

| Column | Values |
|---|---|
| `relevant` | `Y` if the article reports an event that could change supply, price, licensing or shipping of REEs for an importer; otherwise `N` |
| `shock_type` | `P`, `C`, `E` or `R`, as defined below; `none` if not relevant. If two apply, choose the main one and note the other. |
| `severity` | `low`, `medium` or `high`, as defined below; `none` if not relevant |
| `affects_china` | `Y` if the event concerns supply from China |
| `labeled_by`, `labeled_on` | Your name and the date |
| `label_notes` | Optional reasoning, useful for difficult cases |

## 2. Shock types

| Type | Label when the article reports… |
|---|---|
| **P** Price surge | A sharp rise in REE prices, an export tax, or speculative buying |
| **C** Capacity restriction | Production or export quotas, mine or plant closures, processing limits, an export ban |
| **E** Export licensing | New or tighter export licences, approvals or controls that delay shipments without banning them |
| **R** Route disruption | Shipping, port or transit disruption on routes to Japan, including chokepoints and sanctions affecting transport |

## 3. Severity

Severity selects the shock magnitude used by the model (`model/inputs_notes.md`).

| Severity | Rule |
|---|---|
| **High** | In force now or announced with a date, covers dysprosium, terbium or magnet inputs, or a large share of supply, and has no stated end date |
| **Medium** | In force or announced, but limited in scope, product coverage or duration |
| **Low** | Proposed, threatened, rumoured, or a minor change |

**The emergency re-plan trigger** (`progress/model_revision.md` Section 5a) uses only articles labeled **C or E**, **high** severity and **affects China = Y**, with a second such article on the same event within 7 days.

## 4. How the labels are used

- **Accuracy:** AI labels are compared with yours (relevance, type and severity). Accuracy and a confusion matrix are reported as they come out, including weak results.
- **Quarterly severity:** for each quarter, the highest severity among relevant articles of each type sets the magnitude for the next quarter's decision. Where there are no articles of a type, medium severity is used, as in the stress test.
- **Replay video:** headlines appear on screen on their publication date.
