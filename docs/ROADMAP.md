# Roadmap

Roughly in order of value. Move items to "Done" with a date when finished.

## Next

1. **Load real data.** Replace `build_rows()` and the reference tables (see `DATA.md`). Everything else works unchanged.
2. **Upload actuals from Excel.** Let the team drop a sales file into the app instead of editing code.
3. **Calibrate elasticities and promo response** from past promo and price-change history.
4. **Share scenarios.** Saved scenarios live in each person's browser today. Store them on the server so the team can open, compare and comment on each other's work.
5. **Compare scenarios side by side**, for example "Festive push" against "Price increase of 3%".

## Later

- Login and roles (view only against can edit).
- Deploy on a company server with a production web server instead of `python app.py`.
- Other channels (general trade, institutional) and more chains.
- Time view: month-by-month, and promo calendars.
- Self-hosted fonts, so the app looks identical on networks that block Google Fonts.

## Done

- 2026-10-10: First version of the simulator and P&L, sample data, Excel export, Heritage branding, collapsible sidebar.
