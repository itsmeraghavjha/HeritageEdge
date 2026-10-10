# Roadmap

Roughly in order of value. Move items to "Done" with a date when finished.

## Next

1. **Settle the open questions** in `DECISIONS.md`, above all the net margin reconciliation against finance.
2. **Calibrate elasticities and promo response** from history. The offer file holds monthly offers back to April 2025, which can be matched to monthly billing volumes to measure how volume really responded.
3. **Share scenarios.** Saved scenarios live in each person's browser today. Store them on the server so the team can open, compare and comment on each other's work.
4. **Compare scenarios side by side**, for example "Festive push" against "Price increase of 3%".

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
- 2026-10-11: Real data pipeline (SQL volumes, offer file, MIS), net margin P&L, filters and extra dimensions, Data page, optional login.
