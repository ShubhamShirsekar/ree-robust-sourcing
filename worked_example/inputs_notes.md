# Model Inputs: For the Author's Approval

*Prepared 2026-11-21. **Approved by the author on 2026-11-21.** Values come from `prepare_inputs.py`, which writes `data/` (extracts) and `inputs/` (model inputs).*

**Basis key.** **Measured:** read directly from a named source. **Derived:** calculated from measured data. **Assumption:** chosen by the author, stated openly and tested in the sensitivity analysis.

---

## 1. What the data showed

**Comtrade, Japan imports, HS 284690, 2019–2025** (`thesis.db`, read-only)
- 758 partner-month rows, 84 months and 25 partners, after removing the World total. The World total equals the sum of all partners to within USD 0.003.
- **China supplies 78.3% of import value and 86.8% of import weight.** China's weight share rose from 67.7% in 2019 to 92.6% in 2025 (`data/comtrade_region_year.csv`).
- **The unit-value bands fail as an element proxy.** In weight, they retain 17.2% for NdPr, 0.4% for Dy and 0.2% for Tb (`data/band_retention.csv`). HS 284690 therefore cannot measure Dy or Tb flows by region.
- **Consequence:** Comtrade is used only for aggregate concentration and regional mix, not for element prices or element flows. This is a finding to report in the limitations, and it changes Chapter 3 (Section 4 below).

**Extended Comtrade data, 2010–2025 (being fetched).** Japan's imports of HS 284690 (compounds), 280530 (metals) and 850511 (magnets) are being fetched into `data/comtrade_extended.db` (decision of 21 November). When the fetch is complete, three things change:
- The concentration figures above are restated for 2010–2025, by HS code.
- The C (capacity) and E (licensing) magnitudes are checked against the 2010–2011 Chinese export restrictions, and the 2025 licensing controls, where the data show them. Any value that changes is logged here with its reason, before the model is run.
- The status quo policy uses the latest observed mix across all three codes.

**USGS Mineral Commodity Summaries, 2021–2025 editions** (Argus prices, annual averages, USD/kg)

| Year | Nd oxide | Dy oxide | Tb oxide |
|---|---|---|---|
| 2016 | 40 | 198 | 415 |
| 2017 | 50 | 187 | 501 |
| 2018 | 50 | 179 | 455 |
| 2019 | 45 | 239 | 507 |
| 2020 | 49 | 261 | 670 |
| 2021 | 98 | 410 | 1,346 |
| 2022 | 134 | 382 | 2,051 |
| 2023 | 78 | 330 | 1,298 |
| 2024 (estimated) | 56 | 260 | 810 |

Each year's value comes from the latest edition that reports it. Later editions revise earlier years, sometimes heavily: the 2023 edition gave Nd 2021 as 49, and the 2025 edition gives 98 (`data/usgs_oxide_prices_all_editions.csv`). USGS reports Nd oxide, not NdPr oxide, so it is used as the NdPr price proxy.

---

## 2. Inputs

### Commodities

| Input | NdPr | Dy | Tb | Basis |
|---|---|---|---|---|
| Tier | 3 | 1 | 2 | Derived: EU 2023 SR (Table 4.2) |
| Annual demand (tonnes) | 300 | 30 | 6 | **Assumption:** illustrative magnet producer |
| No-shock cost, China (USD/kg) | 66.7 | 271.8 | 894.8 | Derived: mean of USGS annual prices, 2016–2024 |

### Regions

| Region | Cost premium over China | No-shock capacity, NdPr (share of demand) | Dy | Tb | Basis |
|---|---|---|---|---|---|
| China | 0% | 100% | 100% | 100% | **Assumption** |
| Asia excl. China | +10% | 50% | 20% | 20% | **Assumption** |
| Europe | +20% | 20% | 10% | 10% | **Assumption** |
| North America and other | +25% | 30% | 10% | 10% | **Assumption** |

Partner-to-region mapping:
- **Asia excl. China:** India, Philippines, Viet Nam, Korea, Thailand, Malaysia, Other Asia nes, Kazakhstan, Cambodia
- **Europe:** Italy, Estonia, Austria, Germany, France, UK, Norway, Russia, Türkiye, Czechia, Belgium, Armenia, Switzerland
- **North America and other:** USA, Canada

Comtrade's non-China share of import weight was 7–8% in 2024–2025, and the capacities are set above that level. No public data gives capacity by element and region, which is why these values are assumptions.

### Tiers

| Input | Tier 1 (Dy) | Tier 2 (Tb) | Tier 3 (NdPr) | Basis |
|---|---|---|---|---|
| Single-region cap $U_\tau$ | 60% | 70% | 80% | **Assumption** (must be ≥ 25%) |
| Safety stock floor $f_\tau$ (weeks) | 8 | 6 | 4 | **Assumption** |
| Shortage penalty multiplier $m_\tau$ | 3.0 | 2.5 | 1.0 | Existing `thesis.db` values |
| Fill rate target $\alpha_\tau$ | 95% | 92% | 90% | **Assumption** |
| Extra service budget $b_\tau$ | 1 | 1 | 0 | **Assumption** |

### General

| Input | Value | Basis |
|---|---|---|
| Spot price | 1.5 × highest regional cost | **Assumption** |
| Spot limit | 10% of demand | **Assumption** |
| Holding cost | 20% of value per year | **Assumption** |
| Base shortage penalty $p_i$ | 3 × no-shock cost | **Assumption** |
| Working capital $W$ | Value of 26 weeks of demand | **Assumption** |
| Minimum share $L_{ij}$ | 0 | **Assumption** |

### Shock magnitudes

The main run uses medium severity. Low and high severity enter the sensitivity analysis, and the NLP check shows which severity recent news supports.

| Shock | Parameter | Low | Medium | High | Basis |
|---|---|---|---|---|---|
| P | China price increase: NdPr | +8.9% | +30.9% | +100% | Derived: USGS year-on-year increases (smallest, median, largest), 2017–2024 |
| P | China price increase: Dy | +9.2% | +33.5% | +57.1% | Derived, as above |
| P | China price increase: Tb | +11.4% | +32.1% | +100.9% | Derived, as above |
| P | Spillover to other regions | 50% of China's increase | 50% | 50% | **Assumption** |
| C | China capacity reduction | −30% | −50% | −80% | **Assumption** |
| E | China lead time added | +4 weeks | +8 weeks | +12 weeks | **Assumption** |
| R | Lead time added: Europe and North America | +3 weeks | +6 weeks | +9 weeks | **Assumption** |
| R | Lead time added: China and Asia | +1 week | +2 weeks | +3 weeks | **Assumption** |
| R | Landed cost increase: Europe and North America | +5% | +10% | +20% | **Assumption** |
| R | Landed cost increase: China and Asia | +2% | +3% | +5% | **Assumption** |

Annual averages understate price spikes within a year, so the price magnitudes are conservative. The Comtrade aggregate shows no clear capacity drop for China after any single month. That is expected, because HS 284690 is dominated by cheaper compounds, so C and E cannot be calibrated from it.

### Policies compared

| Policy | Definition | Basis |
|---|---|---|
| Robust | Chapter 5 model at Γ = 0 to 4 | Model |
| Expected value | Each shock type occurs independently with 20% probability per year; scenario weights follow from that | **Assumption**, used only for this baseline |
| Dual sourcing | 80% China, 20% lowest-cost non-China region (Asia excl. China); 6 weeks of stock for all commodities | **Assumption** |
| Uniform constraints | Robust model with Tier 2 settings for all commodities | **Assumption** |

---

## 3. Your decisions

1. **Approve the inputs above,** or change any value. After approval, inputs change only with a logged reason.
2. **Status quo baseline:** should the observed 2025 import mix (China 92.6%) be added as a fifth policy? I recommend yes: it is measured, not assumed, and it gives managers a direct comparison with current practice.
3. **No-shock cost:** the 2016–2024 mean is proposed. The 2024 price is lower (Nd 56, Dy 260, Tb 810), so using it would make protection look relatively more expensive.

---

## 4. Changes to Chapters 3–5

- **3.3 and Table 3.1:** element prices come from USGS, not Comtrade unit values. The bands are measured and fail for Dy and Tb, so Comtrade is used for concentration and regional mix only. State the retention figures.
- **3.3, 4.6:** USGS revises earlier years; state which edition each value comes from.
- **4.4:** caps are 60%, 70% and 80%, all above the 25% minimum.
- **4.5, Table 4.3:** P spillover is 50% to other regions. R affects all regions, more strongly for Europe and North America.
- **5.1:** regions confirmed as listed above.
- **5.5:** dual sourcing is 80/20 with Asia excl. China and 6 weeks of stock.
