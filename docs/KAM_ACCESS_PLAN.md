# KAM access: plan and handoff

Status: designed, not built. Branch `kam-access` (from `ui-polish`, which includes `user-login`). Nothing here uses real names or numbers.

## What the user asked for

1. A **KAM** user type. Each KAM sees only their own part of the data.
2. **Leadership** (CEO, MRF head, other senior people) see all data and the full P&L.
3. KAMs see the P&L only **down to contribution**. Hidden from them: P&A, interest, marketing, net margin, NM %.
4. The admin **uploads the KAM Excel file once**. It creates the users with a **starting (dummy) password**. Users change it later (by email link).
5. The admin can **add and update the mapping and users** afterwards.

## The KAM file

One row per employee and mapping. Columns: `StateCluster Name` (state + cluster joined, ignore it), `State`, `Cluster Name`, `Employee ID`, `Employee Name`, `Email`. An employee has several rows (one per state and cluster). Cluster names are customer chains in the data (for example a chain called ABRL), spelled slightly differently at times (`V-Mart` vs `V-MART`), and some will have no match.

## Decisions

- Roles: `admin` (one, made by `manage.py`; sees all, manages users), `leadership` (sees all, full P&L, cannot manage users), `kam` (scoped, P&L to contribution). Old role `user` becomes `leadership` on migration.
- A KAM sees the rows where `state` equals the mapped state and `chain` equals the mapped cluster. Compare names after normalising (upper case, letters and digits only). Union over all of a KAM's mapping rows. A KAM with no mapping sees nothing.
- Unmatched clusters (no chain with that name) are listed to the admin. The admin can add an alias (cluster name to chain). Do not guess matches.
- **Enforce on the server**, never only in the browser. Rows are cut before any calculation; lines below contribution are removed from every response and from the Excel export.
- Starting password: the admin types one when uploading (rules: 10+ characters, letters and numbers). Each new user gets `must_change = 1`, so after the first login they are sent to a change-password page and can use nothing else until they do. Existing users in the file keep their password; only name, employee id and mapping update. Remind the admin that anyone who has not logged in yet can be logged into by anyone who knows the starting password, so also use "Send password link".
- `Data` page and `/api/data/*`: admin only.

## Where to change things (verified in the code)

- `engine/users.py`: add columns `employee_id`, `role` values, `must_change`; tables `kam_scope(id, user_id, state, cluster)` and `cluster_alias(cluster_norm, chain_norm)`. Migrate old databases with `ALTER TABLE` when a column is missing. `login()` returns `must_change`; `set_password_with_token` clears it. Add `change_password`, `set_role`, scope add/remove/replace, `import_mapping`.
- New `engine/access.py`: `norm()`, workbook parser (find the header row by names, case-insensitive), `allowed_indices(user)` returning row positions in `data.ROWS` (or `None` for all), `options_for(indices)` and combos for `/api/meta`, and a report of unmatched clusters.
- `engine/simulate.py`: add an `allowed` argument (positions, or `None`) to `view_rows`, `_items`, `simulate`, `pnl`, `pnl_items`. `simulate()` returns `whole` (all rows at baseline) and `rows_total`: both must use the allowed rows only, or they leak the whole business. Add `strip_below_cm(obj)` removing keys `pa`, `interest`, `mktg`, `nm`, `nm_pct`, `d_nm` recursively (the ladder carries `nm` and `d_nm`; `breakdown`, `bridge`, `base`, `sim`, `total`, `tree` carry the rest).
- `engine/export.py`: `build_workbook(..., allowed=None, limited=False)`. Drop the limited keys from `COLUMNS` and from the pivot sheet's `metric_cols`. `_pivot_sheet` calls `sim.pnl_items`, pass `allowed` there too.
- `app.py`: helper that returns `(allowed, limited)` for `g.user`; use it in `/api/meta` (options, combos, and `status` reduced to title only for non-admins), `/api/simulate`, `/api/pnl`, `/api/export`. Admin-only decorator on `/data` and `/api/data/*`. In `require_login`, if `must_change`, allow only `/change-password` and `/logout`. New routes: `/change-password`, admin `POST /admin/users/upload`, `/admin/users/<id>` (detail and mapping edit), role change, alias add/remove.
- Templates: `users.html` (upload form, role column, mapping summary, unmatched clusters and aliases), new `user_detail.html`, new `change_password.html` (extends `auth_base.html`). Expose `window.HE.user = {role, limited}` from `base.html`.
- Front end: hide the net margin KPI card (`renderKpis`), the P&A / interest / marketing / net margin / NM % rows (`SUMMARY_ROWS` in `simulator.js`, `COLUMNS` in `pnl.js`), the net margin column of the volume ladder, and tell `.kpis` to use four columns (add a class; the CSS spans assume five cards). KAM note text: "Showing your accounts".
- Row adjustment code lives on branch `row-adjustments`, which is separate; this branch does not include it.

## Tests to write (`tests/test_kam.py`, use `tests/synth.py`, never real names)

- Parser reads the sheet, merges several rows per employee, ignores the joined column, skips bad emails.
- A KAM sees only mapped rows in `/api/simulate`, `/api/pnl`, `/api/export`, `/api/meta` (options and combos), `whole` and `rows_total`; a KAM with no mapping sees nothing.
- A KAM response contains none of the limited keys anywhere; the export has no such columns.
- Leadership and admin see everything including net margin; a KAM and leadership user get 403 on `/admin/users` and `/data`.
- First login with the starting password forces a password change, and nothing else loads until then; re-upload keeps existing passwords and updates mapping; alias makes an unmatched cluster match.
- A client cannot widen its view by sending a scope or filter naming other chains.

## Done so far on other branches

`row-adjustments`: per-row sliders. `volume-units`: litres and kg. `user-login`: email and password login, admin Users page, set-password links, login page design (network picture reads customer names from `data/login_chains.txt`, git-ignored). `ui-polish`: layout fixes and a compact layout for scaled displays. Remote main is unchanged.
