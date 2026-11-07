# Error register: what can make the numbers wrong

One list of every known way the figures can be off, so we fix them one at a time. For each: where it comes from, how big it could be, how to measure it, and the fix. Update the status when one is closed. "Ties" means the total still matches billing or the MIS; most items below move money between lines or chains, not the total.

How to read the size: **High** can move a chain or line by several points of margin, **Med** by about a point, **Low** is under that or only touches rare lines.

## A. Inputs

| # | Cause | Where | Size | Measure it | Fix | Status |
|---|---|---|---|---|---|---|
| A1 | (Measured by "Where other deductions come from" on the Data page.) Offer file may hold several rows per customer x SKU (history, mid-month changes). The build takes a plain average of MRP, trade and offer; it does not pick the latest or the one in force | `prepare.build_dataset` (`terms` groupby mean) | High if the file holds history | Count customer x SKU pairs with more than one row and how far their values differ; the Data page already lists the biggest price gaps | Choose the row valid in the base month (date columns), or weight by days in force | Open |
| A2 | One set of terms is applied to all base months, though offers change inside a month (festival schemes, ends of month) | same | High on promo vs other deductions | Build a single month, then another, and compare the promo rate per chain | Build per month and average, or read terms by month | Open |
| A3 | Scope is only customer x SKU lines in the offer file, and only Modern Formats and E-Commerce (the MIS groups). The rest of billing is left out | `in_offer`, `MIS_GROUPS` | Med to High for totals; chains look more or less important than they are | Data page quality block: share of billing revenue in scope, and outside the MIS groups | Show scope beside every total; add other groups only when a cost source exists | Measured, not shown on screens |
| A4 | Billing lines whose pack size cannot be read are dropped (volume and revenue) | `unreadable` | Low when under 1%, check each build | Quality block: share with unreadable pack size | Extend pack parsing for new formats | Measured |
| A5 | Billing revenue may include credit notes, returns or free goods in ways the SQL does not separate | `engine/sql/billing.sql` | Unknown | Tie one chain and month to SQL by hand, then to finance's sales register | Confirm what `InvoiceValue_exTax` contains with IT | Open (ZEPTO tie-out pending) |
| A6 | Units are the average over the chosen base months, so one odd month (festival, stock push) shifts the baseline | `units = packs / n` | Med | Build with one month vs three and compare volume per chain | Choose normal months; show month-by-month volume next to the average | Open |
| A7 | The offer file's `Cluster` decides which chain a customer belongs to; a wrong or renamed cluster moves volume between chains | `chain` from `Cluster` | Med | List customers per chain and compare with the KAM sheet | One agreed customer-to-chain table | Open |
| A8 | Billing, offer file and MIS can be pulled on different days or be revised | process | Low to Med | Record the dates on the Data page | Show "built from" dates on every screen | Partly (build time shown) |

## B. Build: prices and terms

| # | Cause | Where | Size | Measure it | Fix | Status |
|---|---|---|---|---|---|---|
| B1 | "Other deductions" is the plug: it absorbs every price gap (mid-month changes, unmatched schemes, credit notes, mix), so it hides errors in A1, A2 and A5. Net revenue ties by construction | `other_rate = 1 - promo - net_px / gross_u` | High for the promo vs other split | Offer vs billed price check (Data page) | Fix A1, A2 first; then show "unexplained" separately from real fees | Open |
| B2 | Rates are clipped: promo between 0 and 60%, other between -50% and +90%. A line outside that is forced into range and its price gap disappears | `clip` in `build_dataset` | Low if the clipped share is small | Quality block: revenue with price gap clipped | Investigate those SKUs; do not clip silently | Measured |
| B3 | Offer, MRP and trade are weighted by packs within a row, but averaged unweighted across offer rows (A1) | `terms` vs `w_*` | Low to Med | as A1 | as A1 | Open |
| B4 | GST basis of offer values | `mrp_x`, `raw_promo` | none now | Billed price equals `(MRP x (1 - trade) - offer) / (1 + GST)` for most lines in Aug 2026 | Re-check each new offer file | Resolved Aug 2026 |

## C. Build: costs from the MIS

| # | Cause | Where | Size | Measure it | Fix | Status |
|---|---|---|---|---|---|---|
| C1 | The MIS has no chain column, so each chain gets the average cost per pack (or per rupee) of its SKU, office and group cell. Chain-level contribution is an allocation | `attribute_costs` | High for chain comparisons; none for totals | Reconciliation per customer group; compare two chains with the same SKU mix | Real cost-to-serve data (drops, routes, credit days) from operations | Open (needs data) |
| C2 | Fallback ladder: SKU x office, then SKU average, then family ratio. The family ratio is the roughest | same, `cost_source` | Med when many rows fall back | `meta.cost_source`: share of rows per level | Show the share on the Data page; fill gaps from a mapped SKU | Measured |
| C3 | The MIS price per cell averages every customer in the cell, but the model holds only offer-file customers | `mix_check` | Med | Data page mix check ("is it the customers?") | Use only comparable customers for the rates, or weight | Measured |
| C4 | Packs in the MIS may be counted differently from billing | `price_check` | Med if ratio is off 100% | "Packs billed against packs in the MIS" ratio | Align the pack definition with finance | Measured |
| C5 | Cost per pack is clipped between -0.5x and +3x net price per pack; revenue-based costs between -50% and +200% | `attribute_costs` | Low | count of clipped rows (not reported yet) | Report the count | Open |
| C6 | MIS months must equal billing months; without a year in the MIS they match by month name only | `pick_mis_months` | High when wrong (once about 3 points) | Data page shows MIS months used | Keep the year column in the MIS | Guarded |
| C7 | Which cost lines are per pack and which are a % of net revenue (`config/attribution.csv`) is a judgement. P&A, interest and marketing as a % of revenue means a high-price pack carries more of them | `attribution.csv` | Med for net margin only | Compare chain net margin under both rules | Agree the driver with finance per line | Open |

## D. Simulator

| # | Cause | Where | Size | Measure it | Fix | Status |
|---|---|---|---|---|---|---|
| D1 | Volume is the user's own estimate; the suggestion rests on assumed constants (promo response 3x, pass-through 0.7, push per pp) and one elasticity per family. No cannibalisation, cross-chain or competitor effects | `simulate.py` constants, `config/assumptions.csv` | High for any what-if | Back-test against past promo and price changes | Record promo outcomes in the app and calibrate | Open |
| D2 | Costs behave per pack or per rupee even when volume or price changes. In reality part of S&D, P&A and interest is fixed, so a volume rise flatters margin and a fall hurts it less than shown | `calc_row` `cost()` | Med to High for big volume moves | Compare with fixed-cost view for +/-20% volume | Mark fixed vs variable per line; show a range | Open |
| D3 | Lever limits hide extreme inputs: trade margin is capped at 40% and promo at 0 or above | `calc_row` | Low | none | Show a warning when a cap applies | Open |
| D4 | "Other deductions" and costs are held at today's rate when price changes; they scale with the new price instead of staying in rupees | `calc_row` | Low to Med | | Decide per line | Open |
| D5 | Price pass-through: an MRP change is assumed fully reflected, GST unchanged, no rounding to price points | `calc_row` | Low | | Add rounding and partial pass-through if needed | Open |
| D6 | A KAM-level P&L is cut at contribution, but a chain's contribution is itself an allocation (C1) | access rules | see C1 | | Label chain-level costs "estimated" | Open |

## E. Presentation

| # | Cause | Size | Fix | Status |
|---|---|---|---|---|
| E1 | Estimated figures (chain-level costs, promo vs other split) look as precise as measured ones | High for trust | Mark them "estimated" with a one-line reason; show scope and "built from" on every screen | Open |
| E2 | Per-day volume (LPD, KGPD) divides by the base months' average days; wrong if months differ or the dataset predates the month list | Low | Rebuild the dataset after choosing months | Noted |
| E3 | Units are rounded to 0.1 pack in `rows.csv` | Low | none | Accepted |

## Suggested order

1. **Measure first (no code risk).** Build one month and three months and compare chain-level promo rate, other deductions and volume: this sizes A1, A2, A6 and B1 in one go.
2. **A1 and A2:** pick the offer row in force and apply terms by month. Biggest improvement to the promo vs other split.
3. **A3 and E1:** show scope and "estimated" labels on every screen so nobody over-reads chain figures.
4. **C2, C3 and C5:** put cost-source shares, mix gap and clip counts on the Data page.
5. **A5:** finish the SQL tie-out (ZEPTO, one more chain) and settle what billing contains.
6. **D2:** show a fixed-cost view for volume changes.
7. **D1:** record promo outcomes and calibrate.
8. **C1:** ask operations for cost-to-serve data (needs input, not code).


## Found on real data (Nov 2026)

- Offer numbers were the latest month's (Aug) even for a Jul base: fixed by reading the base month columns (`monthly_offers`).
- 44% of offer rows have an OLD MRP different from New MRP (avg +6%); the build uses New for all months. Open: needs the month the new MRP started, or the check's answer.
- Off-invoice offers (about a quarter of rows) are not on the invoice: open, the check measures whether billing includes them.
- The same customer x SKU repeats once per sales district; the build averages districts. Open: match on district to sales office.
- Real data, Aug base: billing is about 1% under the offer file even on lines with no offer, so a standing deduction exists outside offers (check "A price gap with no offer involved"). Off-invoice offers ARE inside billed price (billing sits within 0.1 pts of the offer-deducted price), so the model counts them correctly. Billing follows the new MRP.
- Jul+Aug: gap moves between months on some SKUs (ghee, milk sachets, butter at q-commerce chains). Check "The gap in each base month" shows which MRP fits each month.
- Jul+Aug on real data: 21 SKUs (curd, milk FCM, paneer high protein, cheese slices ...) billed at the OLD MRP in Jul and the NEW in Aug. Fixed by `mrp_in_force` (inferred); open: dates from whoever sets the MRP.
- Open: no-offer lines with 5% GST sit further under the file than 0% GST lines; check "How trade margin and GST combine" tests formula B.
