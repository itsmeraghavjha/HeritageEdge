# Roadmap

Roughly in order of value. Move items to "Done" with a date when finished.

## Next

1. **Settle the open questions** in `DECISIONS.md`: trade margin definition, whether offer values include GST. The net margin reconciliation against the MIS is done for Aug 2026.
2. **Record promo outcomes.** When the team runs a promo, save the planned terms, the breakeven shown, and the volume actually billed. Over time this is the only honest evidence for what uplift to expect, and it lets the breakeven be judged against real results.
3. **Sanity-check the elasticities against history, only where it is clean.** The offer file goes back to April 2025; with at least 24 months of billing, compare modelled response with what happened for chains and SKUs with no competing change. Use it to adjust assumptions in `config/assumptions.csv`, not to forecast.
4. **Share scenarios.** Saved scenarios live in each person's browser today. Store them on the server so the team can open, compare and comment on each other's work.
5. **Compare scenarios side by side**, for example "Festive push" against "Price increase of 3%".

## Later

- Per-user login and roles (view only against can edit). Today there is one shared password.
- Deploy on a company server with a production web server instead of `python app.py`.
- Other channels (general trade, institutional).
- Pull the MIS from SQL Server too, if it lives there, so it no longer needs uploading.
- A better cost-to-serve driver for S&D than cost per pack.
- Time view: month-by-month, and promo calendars.
- Self-hosted fonts, so the app looks identical on networks that block Google Fonts.

## Done

- 2026-10-10: First version of the simulator and P&L, sample data, Excel export, Heritage branding, collapsible sidebar.
- 2026-10-11: Breakeven view and low / base / high volume response on the simulator.
- 2026-10-11: Real data pipeline (SQL volumes, offer file, MIS), net margin P&L, filters and extra dimensions, Data page, optional login.
