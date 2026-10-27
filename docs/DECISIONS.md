# Decisions and open questions

Why things are the way they are. Add to this file when you change direction, so the next person does not have to guess.

## Decisions made

**Plain Python and Flask, sample data first, then real data.** The team needed something to react to quickly. Flask matches the team's other projects, and a pure-Python engine is easy to read and test. Real data now comes in through the Data page.

**One engine, two screens.** The simulator and the P&L page both call the same engine in `engine/simulate.py`, so they can never disagree.

**The simulator reports what you select.** People pick a chain, state or SKU and expect every number on the screen to be about that selection, so the cards, volume check, bridge and breakdowns cover the selected rows only, and all levers apply to them. Earlier the cards always showed the whole business, so a narrow selection barely moved them. The P&L page still shows every row, with the cost levers applied to everything.

**Promo response is stronger than price response.** Shoppers react harder to a temporary promo than to a permanent price change, so promo uses `elasticity x PROMO_RESPONSE_BOOST`. Without this, promos looked unrealistically useless in the simulator.

**Costs and billing use the same months.** The MIS is cut to the base months. Earlier builds averaged MIS costs over every month in the sheet against Jul-Sep billing, and the model showed 3.6% net margin against 0.4% in the MIS. Aligning the months removed most of that gap. The remaining gap, if any, is shown by the revenue per pack check on the Data page.

**Volume is the user's input, not the model's output.** Real volume depends on competition, seasonality, distribution and shelf visibility, none of which we hold, and a fitted elasticity from billing alone would look precise and be wrong. Earlier versions let the model drive volume through elasticity and a "price sensitivity" multiplier, which hid the key assumption inside sliders. Now people enter the volume they expect (default 0), and the simulator shows the breakeven and a ladder of contribution at -10% to +30%. The model's estimate is shown only as a suggestion with a "Use" button. The "Promo passed to shelf price" and "Price sensitivity" levers were removed (the pass-through is now a constant that only feeds the suggestion). Saved scenarios that hold the removed levers ignore them. Calibration against history, if done, is only a sanity check on the suggestion (see `ROADMAP.md`).

**The contribution bridge holds volume at baseline for the rate steps.** This keeps each bar a clean, explainable effect (what the price change does to margin on today's volume) and shows the volume you expect separately.

**Brand colours.** The app uses Heritage green (#259821 from the website), the logo's red and green, and a gold accent. The website only exposed the green, so the rest of the palette was chosen to match the logo.

**Scenarios save in the browser.** It was the quickest way to let people keep scenarios without building accounts. It is also the main limitation: nothing is shared (see `ROADMAP.md`).

**Real data never goes into git.** The dataset holds chain-wise prices, offers and costs. `data/`, `.env` and Excel files are git-ignored, tests use synthetic data, and the repo carries a synthetic `sample_data/` so a fresh clone still runs. The billing query (`engine/sql/billing.sql`) *is* in git, because it holds table names but no login or data, and the whole team needs the same one. The login (host, port, user, password) lives only in each person's `.env`. The app also refuses to listen beyond the local computer without `APP_PASSWORD`.

**Volumes from SQL, terms from the offer file, costs from the MIS.** Billing is the source of truth for volume and net price. The offer file gives list price, trade margin and offers. The MIS gives costs, but has no chain column, so costs are spread to chains (see `HOW_IT_WORKS.md`).

**Scope is the offer file.** Only customer x SKU lines that appear in the offer file are included, so customers without terms on file (for example institutions) are left out rather than guessed at.

**Net revenue always ties to billing.** The "other deductions" line (called "rebates" in the code and in older notes) is the balancing item between list invoice and billed price. It keeps the baseline exact, at the cost of mixing fees with credit notes and mix effects.

**Contribution is gross margin less over/under and S&D, as in the MIS.** In the MIS, gross margin less over/under and FOC is "GM-R", and less S&D again is "MC" (contribution). The model follows that and leaves out FOC, which was near zero. Net margin is contribution less P&A, interest and marketing, and does not depend on where over/under sits. An earlier version took over/under after contribution, which made contribution about 1.5 points higher than the MIS.

## Open questions (please confirm)

- **Trade margin definition.** The model treats it as a share of MRP, and rebates and promo as shares of invoice value. Confirm this matches how the chains' terms are actually written.
- **Net margin against finance (resolved for Aug 2026).** With costs and billing both on August, the model matches the MIS: net margin 2.3% against 2.2%, COGS 87.0% against 87.0%, revenue per pack within 0.1%. The earlier 3.6% against 0.4% came from mixing months. Re-check this after each new build, and before sharing the bottom line. Original note: In a first trial build the model's net margin came out several times higher than the MIS's own net margin for the same customer groups. Part of the gap is the window and scope, but it needs explaining. The first trial showed the gap sitting in the per-pack costs (COGS, S&D, over/under) while the revenue-based lines matched, which points to revenue per pack differing between billing and the MIS. Use the revenue per pack check on the Data page to confirm, and agree the result with finance before the bottom line is shown to anyone.
- **Offer values are GST-inclusive (resolved for Aug 2026).** In the offer file, `Price as per TOT NLC` equals `New MRP x (1 - trade margin)` on every row, and `NLC/Offer Price` is that less the offer, so offers are on the same GST-inclusive basis as MRP. The build already converts both to the ex-GST invoice, so promo is on the right basis. Billing agrees: for most customers on a ghee SKU the billed price per pack equals `(MRP x (1 - trade) - offer) / (1 + GST)` to the paisa. What is left in "other deductions" is timing and unmatched offers, for example an offer that changed mid-month or several offer rows for one customer and SKU (see below).
- **Cost allocation.** Volume-based allocation gives every chain the same cost per pack. Decide whether S&D or others should use another driver (for example drop count), which would need route-level data.
- **Elasticities and response constants** only feed the volume suggestion and are assumptions. See `DATA.md`, section 4.
- **What "other deductions" holds.** Checked on one SKU in August: 54% of litres were billed exactly at the offer-file price. The rest came from offers that changed mid-month (the file shows the latest offer, billing used the older one early in the month), several offer rows for one customer and SKU that the build averages, offers billing did not follow, and a few month-end low-price lines. Net revenue and margins are unaffected, but the split between promo and other deductions is approximate. Next step: read the base month's offer from the offer file's monthly remark and flag the largest price gaps on the Data page.
- **COGS lever replaced the milk lever.** The MIS gives one COGS number with no split between milk, packing and conversion, so a "milk share" per family was an invented assumption. The simulator now has one lever, COGS change %, which scales the whole line. To model a milk price move, work out what share of COGS it is and enter the resulting percentage. Scenarios saved earlier with the milk lever ignore it and need that value re-entered.
