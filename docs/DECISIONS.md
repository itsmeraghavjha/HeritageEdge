# Decisions and open questions

Why things are the way they are. Add to this file when you change direction, so the next person does not have to guess.

## Decisions made

**Plain Python and Flask, sample data first.** The team needed something to react to quickly. Flask matches the team's other projects, and a pure-Python engine is easy to read and test. Real data comes later through one function (`build_rows`).

**One engine, two screens.** The simulator and the P&L page both call the same engine in `engine/simulate.py`, so they can never disagree.

**Levers can be limited to chains, states and SKUs; costs cannot.** Price, promo and chain terms are commercial decisions made per chain or SKU. A milk price rise or freight rise hits every line, so those levers always apply to everything.

**Promo response is stronger than price response.** Shoppers react harder to a temporary promo than to a permanent price change, so promo uses `elasticity x PROMO_RESPONSE_BOOST`. Without this, promos looked unrealistically useless in the simulator.

**The contribution bridge holds volume at baseline for the rate steps.** This keeps each bar a clean, explainable effect (what the price change does to margin on today's volume) and shows the volume response separately.

**Brand colours.** The app uses Heritage green (#259821 from the website), the logo's red and green, and a gold accent. The website only exposed the green, so the rest of the palette was chosen to match the logo.

**Scenarios save in the browser.** It was the quickest way to let people keep scenarios without building accounts. It is also the main limitation: nothing is shared (see `ROADMAP.md`).

## Open questions (please confirm)

- **"SO" means Sales Office.** This was assumed. If it means something else (for example Sales Officer), the P&L grouping needs changing.
- **Trade margin definition.** The model treats it as a share of MRP, and rebates and promo as shares of invoice value. Confirm this matches how the chains' terms are actually written.
- **Sales and distribution at a flat 3.5% of net revenue.** Replace with real figures by chain or state if available.
- **Which costs belong between gross margin and contribution?** Currently only logistics and sales and distribution. Add others (for example A&P, trade spends outside promo) if the team's contribution definition includes them.
- **Elasticities and response constants** are assumptions. See `DATA.md`, section 4.
