# Decisions and open questions

Why things are the way they are. Add to this file when you change direction, so the next person does not have to guess.

## Decisions made

**Plain Python and Flask, sample data first, then real data.** The team needed something to react to quickly. Flask matches the team's other projects, and a pure-Python engine is easy to read and test. Real data now comes in through the Data page.

**One engine, two screens.** The simulator and the P&L page both call the same engine in `engine/simulate.py`, so they can never disagree.

**Levers can be limited to chains, states and SKUs; costs cannot.** Price, promo and chain terms are commercial decisions made per chain or SKU. A milk price rise or freight rise hits every line, so those levers always apply to everything.

**Promo response is stronger than price response.** Shoppers react harder to a temporary promo than to a permanent price change, so promo uses `elasticity x PROMO_RESPONSE_BOOST`. Without this, promos looked unrealistically useless in the simulator.

**Volume is not predicted; it is an assumption with a range.** Real volume depends on competition, seasonality, distribution and shelf visibility, none of which we hold. A fitted elasticity from billing alone would look precise and be wrong. So the simulator shows a breakeven (extra volume needed to match today's contribution, no elasticity involved) and a low / base / high range (0.5x, 1x, 1.5x of the modelled response). Calibration against history, if done, is only a sanity check on the assumptions where the history is clean (see `ROADMAP.md`).

**The contribution bridge holds volume at baseline for the rate steps.** This keeps each bar a clean, explainable effect (what the price change does to margin on today's volume) and shows the volume response separately.

**Brand colours.** The app uses Heritage green (#259821 from the website), the logo's red and green, and a gold accent. The website only exposed the green, so the rest of the palette was chosen to match the logo.

**Scenarios save in the browser.** It was the quickest way to let people keep scenarios without building accounts. It is also the main limitation: nothing is shared (see `ROADMAP.md`).

**Real data never goes into git.** The dataset holds chain-wise prices, offers and costs. `data/`, `.env` and Excel files are git-ignored, tests use synthetic data, and the repo carries a synthetic `sample_data/` so a fresh clone still runs. The billing query (`engine/sql/billing.sql`) *is* in git, because it holds table names but no login or data, and the whole team needs the same one. The login (host, port, user, password) lives only in each person's `.env`. The app also refuses to listen beyond the local computer without `APP_PASSWORD`.

**Volumes from SQL, terms from the offer file, costs from the MIS.** Billing is the source of truth for volume and net price. The offer file gives list price, trade margin and offers. The MIS gives costs, but has no chain column, so costs are spread to chains (see `HOW_IT_WORKS.md`).

**Scope is the offer file.** Only customer x SKU lines that appear in the offer file are included, so customers without terms on file (for example institutions) are left out rather than guessed at.

**Net revenue always ties to billing.** The "rebates and other" line is the balancing item between list invoice and billed price. It keeps the baseline exact, at the cost of mixing fees with credit notes and mix effects.

**Contribution is gross margin less S&D.** Net margin is the new bottom line, after P&A, interest, over/under and marketing.

## Open questions (please confirm)

- **Trade margin definition.** The model treats it as a share of MRP, and rebates and promo as shares of invoice value. Confirm this matches how the chains' terms are actually written.
- **Net margin against finance.** In a first trial build the model's net margin came out several times higher than the MIS's own net margin for the same customer groups. Part of the gap is the window and scope, but it needs explaining. Use the reconciliation on the Data page and agree the result with finance before the bottom line is shown to anyone.
- **Is the offer value GST-inclusive?** The build treats offers on the same basis as MRP (including GST) and converts them to the ex-GST invoice. If offers are already ex-GST, promo is understated and "rebates and other" is overstated by the same amount. Net revenue is unaffected.
- **Contribution definition.** Currently gross margin less S&D. Confirm that matches how the MRF team and finance define it.
- **Cost allocation.** Volume-based allocation gives every chain the same cost per pack. Decide whether S&D or others should use another driver (for example drop count), which would need route-level data.
- **Elasticities and response constants** are assumptions. See `DATA.md`, section 4.
