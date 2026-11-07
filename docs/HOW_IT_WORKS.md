# How HeritageEdge works

This explains what the numbers mean and how the levers move them. It is written for both the MRF team and developers.

## What the tool is for

The MRF team wants to try a promo, price change or change in chain terms **before** committing to it, and see the effect on net revenue, margin and contribution. The simulator does that. The P&L page shows the same numbers as a statement, cut by chain, customer group, state, city, sales office, product category, variant, pack size and SKU.

Everything is a **monthly run-rate**. "Baseline" is the position built from your data (the average of the months you chose) with no levers set. "Scenario" is the baseline with your levers applied.

## The P&L, line by line

Calculated for every row of data (one chain and customer group, in one sales office, for one SKU), then added up:

| Line | Meaning |
|---|---|
| MRP value | units x MRP (excluding GST) |
| minus Trade margin | the chain's share of MRP, from the offer file |
| **= Gross sales** | list invoice value |
| minus Promo | the current offer from the offer file, as a share of list invoice |
| minus Other deductions | the gap between list invoice and what was actually billed (credit notes, mix, other schemes) |
| **= Net revenue** | what was billed. Ties back to the billing data |
| minus COGS | attributed from the MIS. The COGS change lever scales the whole line |
| **= Gross margin** | |
| minus Over/under | attributed from the MIS |
| minus S&D / freight | attributed from the MIS |
| **= Contribution** | gross margin less over/under and S&D / freight. This is the MIS definition (its "MC") |
| minus P&A, Interest, Marketing | attributed from the MIS |
| **= Net margin** | the bottom line |

The MIS also has FOC, notional and OI lines. They were zero or close to zero in the months checked, so the model leaves them out. If they become material, add them as cost lines (`config/attribution.csv`, `engine/data.py`).

### Where each row's terms come from

- **Units:** average monthly packs billed over the base months you ticked on the Data page.
- **MRP, trade margin, offer, GST:** the offer file, averaged over the districts a customer buys in.
- **Net price:** billed invoice value divided by packs. Because "Other deductions" is the difference between list and billed price, net revenue always ties to billing.
- **Costs:** the MIS, spread to chains. See the next section.

## How MIS costs reach each chain

The MIS has no chain column. For every SKU x sales office x customer group it takes the MIS cost and spreads it to chains. `config/attribution.csv` sets the rule for each cost line:

- `volume`: every chain pays the same cost per pack in that cell.
- `revenue`: every chain pays the same percentage of its net revenue.

Where the MIS has no figure for an exact SKU x office cell, it falls back to the SKU average and then the product-family average.

**Limit of the method:** with volume-based lines, chains differ only through their mix of SKUs and offices, not through how costly they are to serve. It is an allocation, not a measured cost to serve. Splitting S&D by drop count, say, would need route-level data.

In a scenario, volume-based lines move with units and revenue-based lines move with net revenue.

## The levers

| Lever | What it changes | Applies to |
|---|---|---|
| MRP change (%) | Shelf price and invoice price together | Selected scope |
| Extra promo discount (pp) | Added to each row's current promo rate | Selected scope |
| Trade margin change (pp) | Chain margin as a share of MRP | Selected scope |
| Other deductions and display fees (pp) | Moves the "Other deductions" line. See the note below | Selected scope |
| Volume change you expect (%) | Your own view of how many more or fewer packs the levers will sell. Default 0 | Selected scope |
| COGS change (%) | The whole COGS line, from the MIS. Use it for a milk, packing or other input cost change | Selected scope |
| S&D / freight cost (%) | The S&D line | Selected scope |

"Selected scope" means the dimensions you limit under *Apply price, promo and chain terms to*. Any dimension can be limited, and nothing selected means everything. **The simulator screen shows the selection only:** the cards, volume check, bridge and breakdowns are for the selected rows (the whole business when nothing is selected), and every lever, including the two cost levers, applies to those rows. The P&L page still shows every row: there the commercial levers apply to the selection, and the cost levers apply to everything.

**Note on "Other deductions and display fees":** in the real data this line also contains credit notes and mix effects, because it is the balancing item between list and billed price. Moving it by 1 pp changes that balancing line, which is not strictly "display fees". Treat it as "other deductions from invoice".

## Volume: your input, the model's hint, and the volume check

Volume is the one number the data cannot give us: we hold no competitor prices, seasonality or shelf data. So the levers change only what each pack earns, and **volume is an input you enter** ("Volume change you expect", default 0, limited to -50% to +100%). It scales the packs of every row in scope: `units = baseline units x (1 + volume change)`. Every line then follows the packs, as in the P&L maths above. On the P&L page, rows outside the scope keep their baseline volume.

**Model suggestion (a hint only).** For the price, promo and chain-term levers the model estimates the volume change from the elasticities in `config/assumptions.csv`, which the card shows with a "Use" button. Per row: `(1 + price change) ^ elasticity x promo ratio ^ (elasticity x PROMO_RESPONSE_BOOST) x trade and deduction push`, where the promo ratio is `1 - PROMO_PASS_THROUGH x promo drop x (1 - trade margin)`, then weighted by baseline packs over the rows in scope. The constants are at the top of `engine/simulate.py`. They are assumptions, not measurements, and the suggestion never changes any number unless you adopt it.

**Breakeven.** At the new terms, how much extra volume pays for the change? With terms fixed, every line scales with packs, so the answer is exact: `required = (baseline contribution - contribution outside scope) / contribution in scope at new terms - 1`. It needs no elasticity. The card compares it with the volume you entered. Statuses: *nothing to pay back* (no lever changes what a pack earns), *already ahead* (a negative requirement: how much volume can be lost), *needs more volume*, and *more volume will not pay it back* (contribution in scope is zero or negative). Only lines in scope can sell more, even for cost levers.

**Volume ladder.** The same scenario at volume changes of -10%, 0, +10%, +20% and +30% (`VOLUME_LADDER`), plus the value you entered, which is marked. Each row shows packs, contribution and net margin against today. It shows how much the answer depends on the volume you expect.

There is no built-in "realistic uplift" threshold; judge the breakeven against what the team has seen in past promos.

## Litres and kg

Volume is also shown as litres and kg. Each pack's size comes from its pack text (`500 ML`, `1.5 LTR`, `400 GMS`, `1 KG`, `700+700 ML`) and, for a dataset built before pack sizes were kept, from the SKU name. ML and L packs count as **litres**, GM and KG packs count as **kg**. The two are never converted into each other and never added together, so a total shows litres and kg as separate columns. Quantity is packs x pack size, so it moves with the volume change just as packs do. Packs whose size cannot be read are left out of both columns, not guessed.

## Who sees what

There are three roles. The **admin** (one) manages users and the Data page and sees everything. **Leadership** sees all data and the full P&L. A **KAM** sees only the rows for the states and chains they are mapped to, and the P&L only down to contribution.

A KAM never sees the Data or Users pages. The simulator labels their selection "All my accounts", says costs apply to their accounts, and drops the net margin card and column. A KAM with no mapped accounts (or only clusters that match no chain) gets a plain explanation on the simulator and P&L instead of a screen of zeros.

- **Mapping.** The admin uploads the KAM sheet (State, Cluster Name, Employee ID, Employee Name, Email; one row per state and cluster) on the Users page, with a starting password. That creates a KAM per email, who must choose their own password at first login. Uploading again updates names and mappings and leaves passwords alone. The admin can also edit one KAM's mapping rows, change a role, or add a person.
- **Matching.** A mapping row matches the data rows with the same state and a chain of the same name once case, spaces and punctuation are ignored ("V-Mart" matches "V-MART"). A cluster that matches no chain is listed on the Users page, where the admin can alias it to a chain; names are never guessed. A KAM with no matching rows sees nothing.
- **Enforced on the server.** Rows are cut before any calculation, so the simulator, the P&L, the Excel export and the lists of chains, states and SKUs all hold only the KAM's own rows, and "whole business" figures are their own rows too. For a KAM, P&A, interest, marketing, net margin and NM % are removed from every answer and from the export. The screens also hide those lines, but only the server cut counts. A scope or filter that names other chains returns nothing.
- **Same numbers.** A KAM's figures equal what leadership gets by selecting the same states and chains.

## Row adjustments

Under "Where it lands", a chain, product category, SKU or state can carry its own price, promo, trade margin, other deductions and volume change (the **Adjust** button on each row). They are the five commercial levers; the two cost levers stay global.

- **Added to the screen's levers.** For a row of data, the effective lever is the screen's value plus every adjustment that matches the row (its chain, its category, its SKU, its state), then clamped to the lever's limit in `LIMITS`. Where two adjustments match one row, for example a chain and a SKU, they add up.
- **Same maths as a selection.** Adjusting promo by +3 pp on one chain gives the same numbers as selecting that chain and setting +3 pp. A test checks it.
- **Selection still decides what is shown and changed.** Adjustments only matter for rows inside the selection.
- **Per-row breakeven.** Each row group shows its own breakeven: the extra volume that brings the group's contribution back to today's at the new terms, using the same formula as the screen-level breakeven but for that group.
- **Volume ladder.** If volume is set on rows, there is no single entered figure, so the marked line is the scenario's actual overall volume change. The other lines use one volume change for every row.
- **Not modelled.** A promo on one SKU may take sales from another (cannibalisation). Each row's volume is your own estimate, so the total can be lower than the sum of the rows.
- **Where it lives.** The adjustments travel inside the `levers` value as `adjust: {dimension: {id: {lever: value}}}`. They are saved with scenarios, applied on the P&L page, and listed on the "Scenario levers" sheet of the Excel export. "Clear all row changes" and "Reset levers" remove them.

## The contribution bridge

The chart on the simulator page answers "which of my changes is doing what?". It applies lever groups one at a time (price, then promo, then trade terms, then costs) **with volume held at baseline**, so each bar is a pure rate effect. Whatever remains is the **volume you expect**. The bars always add up exactly to the scenario contribution.

## Where things live

```
app.py                  Flask routes, optional login, Data page API
engine/data.py          Loads the dataset, defines the dimensions
engine/sql_source.py    Pulls billing from SQL Server
engine/prepare.py       Builds the dataset from billing + offer file + MIS
engine/datastore.py     Uploads and background jobs for the Data page
engine/simulate.py      P&L maths, levers, bridge, grouping
engine/export.py        Excel export (grouped rows you can collapse, negatives in red, flat sheet for a PivotTable)
config/                 Elasticity and cost attribution rules
sample_data/            Synthetic dataset (in git)
data/                   Real data (ignored by git)
templates/, static/     Pages, scripts, styles
tests/                  Engine and build tests, synthetic data generator
```

API (all JSON): `/api/meta`, `/api/simulate`, `/api/pnl`, `/api/export`, and for the Data page `/api/data/status`, `/api/data/upload/<offer|mis>`, `/api/data/sql/test`, `/api/data/sql/pull`, `/api/data/build`. Simulate, P&L and export take `levers`, `scope`, and for P&L and export `dims` and `filters`.

## Known limits

- Scenarios you save are stored **in your own browser**, so they are not shared with the team yet.
- Login is per person (email and password), with one admin who adds and disables users. See `docs/DECISIONS.md`. Passwords need 10 or more characters with letters and numbers; five wrong tries lock that email for 15 minutes; a set-password link works once and expires (72 hours for a new user, 2 hours from "Forgot password"); a newer link cancels older ones.
- `python app.py` runs Flask's development server. Do not use it as a shared production deployment.
- Volume is your input. The elasticities and response constants only feed the suggestion. The rate effects are exact arithmetic on the inputs.
- Scope is the customers and SKUs present in the offer file, within Modern Formats and E-Commerce.

## Activity log

`engine/activity.py` keeps an event log in `data/users.db` (git-ignored) and the admin reads it on the Activity page (`/admin/activity`, with a CSV download). It records who did what and when: sign-ins, failed sign-ins, sign-outs, password changes, links sent, people added, enabled or disabled, role and mapping changes, KAM file uploads, cluster matches, screens opened (once per five minutes per person and screen, and only when the page was really shown), Excel exports (and whether they were limited to contribution), and Data page steps. It never records numbers (levers, prices, volumes, chain figures) or passwords; text typed into the email box that is not an email address is replaced before saving. Events older than 400 days are dropped. Nothing is logged until the first login account exists. Writing the log can never break a request: errors are swallowed.

The page shows people active this week, sign-ins, exports and failed sign-ins, active people per day, the most-used screens, one line per person (last active, sign-ins, screens, exports; a person who has never signed in is flagged) and the event list with type and person filters.

Filter use is counted too: when someone picks values in a filter (chain, state, SKU and so on) on the simulator or P&L, the browser sends only the dimension and the screen to `/api/usage`, once per dimension per screen per page load. The values chosen are never sent or stored. The Activity page shows the count per dimension, how many people used it and where, over the last 30 days.

Sliders are counted the same way: when a lever is moved away from its starting value on the simulator, the browser sends only the lever's name (once per lever per page load). The amount is never sent or stored. The Activity page shows "Sliders used" with the count and number of people per lever.

## Data checks

The Data page lists 22 integrity checks (units, coverage, MIS match, tax and offer file). They only read the data and never change a number. See `docs/DATA.md`.

Offer per base month: the build reads each base month's offer text column (average if several), not the offer file's latest-month numbers. On-invoice offers lower the invoice price; off-invoice claims are paid later and may not be in billing (a data check measures this).
