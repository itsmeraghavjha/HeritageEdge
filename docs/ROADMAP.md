# Roadmap

Where things stand is in `STATUS.md`. This file is the ordered list of what to do next. Move items to "Done" with a date.

## Before real users

1. **Host it properly**: behind HTTPS with a production server (Waitress or Gunicorn) on a company machine; set `HOST`, `APP_BASE_URL`.
2. **Company SMTP** in `.env` so links are emailed; then retire the shared starting password.
3. **Back up `data/`** (dataset, accounts, activity log, uploads).
4. **Sign off the numbers** with finance: trade margin definition, GST basis, the split of other deductions, ZEPTO tie-out, 500 ML check. See `DECISIONS.md`.

## Next

5. **Share scenarios.** Saved scenarios live in each person's browser. Store them on the server so the team can open, compare and comment.
6. **Compare scenarios side by side**, for example "Festive push" against "Price increase of 3%".
7. **Record promo outcomes.** Save planned terms, the breakeven shown and the volume actually billed. It is the only honest evidence for the uplift to expect.
8. **Sanity-check elasticities against history**, only where clean (offer file from April 2025, 24+ months of billing). Adjust `config/assumptions.csv`; do not forecast with it.
9. Polish: clearer link-expiry messages, P&L "choose columns", one-line verdict on the simulator, self-hosted fonts.
10. Use the usage counts (Activity page) to decide what to simplify or remove.

## Later

- Other channels (general trade, institutional).
- Pull the MIS from SQL Server too, if it lives there.
- A better cost-to-serve driver for S&D than cost per pack.
- Time view: month by month, and promo calendars.
- Several admins, managers over teams of KAMs, single sign-on.

## Done

- 2026-11-06: Merged to `main`: row adjustments, litres/kg volume, email login and Users page, KAM access and KAM screens, UI polish and compact layout, sidebar footer, activity log with filter and slider counts.
- 2026-10-20: Volume is the user's input, with breakeven and a volume ladder; elasticity only suggests.
- 2026-10-11: Real data pipeline (SQL volumes, offer file, MIS), net margin P&L, filters and extra dimensions, Data page; breakeven view.
- 2026-10-10: First simulator and P&L, sample data, Excel export, Heritage branding.
