# Status and handoff

Read this first in a new chat. It is the one place that says where the project stands. Last updated 2026-11-06, `main` at the merge of all feature branches, 193 tests passing.

## Who and how we work

- Owner: Raghav Kumar J, MRF team, Heritage Foods. Plain explanations please; not a daily coder. Tests locally on **Windows** with a virtualenv.
- Windows gotchas: run `python manage.py ...` (not `manage.py ...`, the file association prints nothing); start the app with `python app.py` in its own window; "localhost refused to connect" means the app is not running; after pulling, restart the app and press Ctrl+F5.
- Workflow that worked: one branch per feature off the latest, run `python -m pytest -q`, commit with the Co-Authored-By and Claude-Session trailers, push the branch, tell the user the exact commands (`git fetch origin`, `git checkout <branch>`, `git pull`). Merge to `main` only when asked.
- Check UI changes with Playwright screenshots on sample data: run the app with `HERITAGEEDGE_DATA_DIR=/tmp/<new dir> HERITAGEEDGE_USE_SAMPLE=1`, create users with `engine.users.create(...)`, log in, screenshot at 1366 and 1600 px wide.
- Never put real chain names, prices, volumes or costs in code, tests, docs, commits or screenshots (see `CLAUDE.md`). Tests use `tests/synth.py`.

## What is built (all on `main`)

| Area | What it does | Where |
|---|---|---|
| Promo simulator `/` | Levers, scope picker, KPIs, volume check and ladder, contribution bridge, breakdowns, per-row adjustments, saved scenarios (browser only) | `engine/simulate.py`, `static/js/simulator.js`, `common.js` |
| P&L `/pnl` | Net revenue to net margin, any grouping, drill-down, Baseline / Scenario / Change, volume as packs plus litres and kg in one column, Excel export | `engine/simulate.py`, `engine/export.py`, `pnl.js` |
| Data `/data` | SQL volumes, offer file and MIS uploads, build, reconciliation (admin only) | `engine/prepare.py`, `sql_source.py`, `datastore.py` |
| Login | Own email and password, set-password links (invite 72 h, reset 2 h, single use), lockout after 5 wrong tries, forced change for starting passwords, one admin made by `manage.py create-admin` | `engine/users.py`, `engine/mailer.py`, `auth_base.html` and login templates |
| Roles | admin (everything, manages users), leadership (all data, full P&L), KAM (own state + chain rows, P&L to contribution only), enforced on the server | `engine/access.py`, `simulate.strip_below_cm`, `app.py visible()` |
| Users `/admin/users` | Upload KAM Excel with a starting password, add a person, search and filter, status badges, send link, disable, edit page with state/cluster mapping, alias fix for clusters that match no chain | `templates/users.html`, `user_detail.html` |
| KAM screens | "All my accounts" wording, no net margin anywhere, explanation page when a KAM has no mapped accounts | `templates/no_accounts.html` |
| Activity `/admin/activity` | Log of sign-ins, failed sign-ins, screens opened, exports, access changes, data steps; people table; charts; CSV; counts of which pickers and sliders are used (names only, never values) | `engine/activity.py`, `activity.html`, `POST /api/usage` |
| Look | Heritage branding, login page with customer network picture (names from git-ignored `data/login_chains.txt`), compact layout for scaled displays, sidebar footer with status and account cards | `static/css/app.css`, `engine/network.py` |

Numbers were reconciled to the MIS for Aug 2026 (net margin 2.3% vs 2.2%).

Branch `other-deductions` (not merged) also holds: the "Where other deductions come from" panel and the 19 Data checks (`engine/integrity.py`). Next: run them on real data, then fix what turns red, in `docs/ERROR_REGISTER.md` order.

**Where the "other deductions" work stands (Nov 2026).** Decision: stay on monthly data (option C), show the leftover as a named residual, no guessing. Waiting on the user to confirm the MRP in force per month; until then build on one month (Aug) so old vs new MRP does not matter. `mrp_in_force` infers old vs new from billing and is only a stand-in: it changes the gross / promotion / other split, never net revenue; it is on by default in this branch and should be switched off (use New MRP unless `config/mrp_effective.csv` gives dates) if the user says so. The user's view: prices change mid-month, which monthly data cannot show; option A (add billing week to `billing.sql` plus a read-only "price steps inside a month" check) is the way to test it if wanted. Rule for working here: label each statement "seen in the data" or "my reading", and ask before changing anything that moves a number.

## Not done yet (priority order)

1. **Hosting.** Still Flask's dev server on HTTP. IT should host it behind HTTPS with Waitress or Gunicorn. Other computers need `HOST=0.0.0.0` and `APP_BASE_URL`.
2. **Email.** Set the `SMTP_*` lines in `.env`; until then the admin copies links by hand. Then drop the shared starting password in favour of individual links.
3. **Backups** of `data/` (dataset, `users.db` with accounts and the activity log, uploads). Nothing backs it up.
4. **Sign-off on numbers.** Open questions in `DECISIONS.md` (trade margin definition, GST on offers, other deductions split); the ZEPTO SQL tie-out and the 500 ML SalesQuantity check the user was going to run.
5. **Shared scenarios** (server side, compare side by side). Biggest user-facing gap.
6. Small: self-hosted fonts; clearer messages for expired, used or replaced links; P&L "choose columns"; a one-line verdict at the top of the simulator; track P&L group-by and row adjustments if wanted; delete old merged branches on GitHub.
7. Not built by design: several admins, managers seeing a team's KAMs, single sign-on, mapping beyond state + chain.

## Conventions that are easy to forget

- Every maths change needs a test and a `docs/HOW_IT_WORKS.md` update. A route that returns numbers takes `allowed` rows and strips below-contribution lines for a KAM. A route that exports data or changes access calls `log(...)` and the log never holds numbers or passwords.
- Form POSTs need the CSRF token; `/api/*` JSON posts rely on SameSite=Lax. In tests set `session["csrf"]` again after logging in.
- CSS: one file. Page-specific rules must be scoped (an unscoped table rule once broke the P&L first column).
- Emails and names in screenshots or tests must be made up (`example.com`).

## Other docs

`CLAUDE.md` (rules and architecture, auto-read by Claude Code), `README.md` (run it), `docs/HOW_IT_WORKS.md` (formulas, who sees what, activity log), `docs/DATA.md` (pipeline), `docs/DECISIONS.md` (why, open questions), `docs/ROADMAP.md` (what next), `CONTRIBUTING.md` (workflow, adding a lever).
