"""A scenario sheet: download the rows as Excel, put the scenario next to the baseline, upload it back.

The Rows sheet has one line per dataset row the person may see (chain, customer group, state, city, sales office,
category, variant, pack, SKU). For five drivers it shows the baseline and an empty-looking Scenario column holding the
same number, in litres and per-litre rupees: volume, MRP, trade margin, promo and other deductions. The team types a
new number, or a formula such as =baseline*1.2, in the Scenario column and uploads the file.

Nothing here does any maths of its own. An upload turns each changed Scenario value into the change the engine already
takes for that row (percent or percentage points, `simulate.ADJUST_LEVERS` on a `row` adjustment), working it out from
the row's own data, so the P&L then calculates exactly as it does for any other scenario. COGS and S&D stay global, on
the Levers sheet. Volumes count kg as litres, as the LPD figures do (1 kg = 1 litre).
"""
import io
from dataclasses import replace

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import data
from . import simulate as sim
from .export import AMOUNT, COLUMNS, HEADER_FILL, HEADER_FONT, LEVER_LABELS

MAX_BYTES = 40 * 1024 * 1024
MAX_ROWS = 300000
INPUT_FILL = PatternFill("solid", fgColor="FFF8DC")
BASE_FILL = PatternFill("solid", fgColor="EEF1EE")
GREY = Font(color="808080")
TOL = 1e-7                                   # a change smaller than this is rounding noise, not a change

LEVERS_SHEET = "Levers"
ROWS_SHEET = "Rows"
READ_ME = "Read me"
GLOBAL_LEVERS = ("cogs_change_pct", "freight_change_pct")        # the cost levers stay global

# The five drivers set per row: key, title, number format
DRIVERS = [
    ("volume", "Volume ('000 L)", "#,##0.00"),
    ("mrp", "MRP per litre, ex-GST (Rs)", "#,##0.00"),
    ("trade", "Trade margin (% of MRP)", "0.00"),
    ("promo", "Promo per litre (Rs)", "#,##0.00"),
    ("other", "Other deductions per litre (Rs)", "#,##0.00;[Red]-#,##0.00"),
]
DRIVER_TITLE = {k: t for k, t, _ in DRIVERS}

README_LINES = [
    "How to use this sheet",
    "1. Rows sheet: one line for each chain, customer group, state, city, sales office, product and pack in the baseline. "
    "Use the filter arrows on the header to find the rows you want (one SKU in one state for one chain, say).",
    "2. Each driver has two columns: Baseline (grey, for reading) and Scenario (yellow). The Scenario column starts with the same number as the "
    "baseline. Type a new number there, or a formula such as =baseline cell * 1.2, and fill it down. Volume is in thousand litres (kg counts as litres), "
    "prices and deductions are rupees per litre ex-GST, trade margin is a % of MRP.",
    "3. Only a Scenario value that differs from its baseline counts. Rows you do not touch stay at baseline.",
    "4. The grey columns to the right are the baseline P&L of that row (amounts in Rs lakh, volume per day in litres). They are for reading only; "
    "the scenario result is shown on the P&L page after the upload.",
    "5. Levers sheet: COGS and S&D / freight change in %, applied to every row. These two stay global.",
    "6. The engine limits how far one row can move (for example volume -50% to +100%, MRP +/-15%, promo -5 to +20 points of invoice). "
    "A Scenario value beyond that is brought to the limit, and the upload tells you which lines.",
    "7. Do not rename the sheets or the columns, and do not delete the hidden last column of the Rows sheet: it is how each line is matched.",
    "8. Whatever was set on the screen when you downloaded this sheet is already in the Scenario columns. Upload the file on the P&L page.",
]


class SheetError(Exception):
    """The uploaded file cannot be used; the message says what to fix, in plain words."""


def _number(value, where):
    """A cell as a number. None for blank. Raises SheetError naming the cell for anything that is not a number."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise SheetError(f"{where} is not a number.")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except ValueError:
        raise SheetError(f"{where} is not a number ('{str(value)[:20]}').")


def _code(value):
    """A code cell as text: 12345 and 12345.0 both give '12345'."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _header(ws, widths):
    for cell in ws[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 48
    ws.freeze_panes = "A2"


def _row_indices(allowed):
    return range(len(data.ROWS)) if allowed is None else allowed


def _terms(row, lv):
    """The five drivers of a row under the levers `lv`, as the engine (`calc_row`) works them out; None when the row's
    pack size is unknown, so there is no litre figure. Baseline = the default levers."""
    size = row["size"]
    if not size:
        return None
    mrp = row["mrp_x"] * (1 + lv.price_change_pct / 100)
    trade = min(0.4, max(0.0, row["trade"] + lv.trade_margin_add_pp / 100))
    invoice = mrp * (1 - trade)
    units = row["units"] * max(0.0, 1 + lv.volume_change_pct / 100)
    return {"volume": units * size / 1e3, "mrp": mrp / size, "trade": trade * 100,
            "promo": invoice * max(0.0, row["promo_rate"] + lv.promo_add_pp / 100) / size,
            "other": invoice * max(-0.5, row["other_rate"] + lv.rebate_add_pp / 100) / size}


def _clamp(lever, v):
    lo, hi = sim.LIMITS[lever]
    return min(hi, max(lo, v))


def row_changes(row, scenario):
    """The engine changes that give `row` the scenario values (a dict of driver -> number; a missing or None driver stays
    at baseline). Returns (changes, limited): changes is {lever: value} for the levers that differ from baseline, limited
    lists the levers that had to be brought to the engine's allowed range."""
    base = _terms(row, sim.DEFAULT_LEVERS)
    if base is None:
        return {}, []
    want = {k: (scenario.get(k) if scenario.get(k) is not None else base[k]) for k in base}
    limited, out = [], {}

    def put(lever, v):
        c = _clamp(lever, v)
        if abs(c - v) > TOL:
            limited.append((lever, v, c))
        out[lever] = c
        return c

    price = put("price_change_pct", (want["mrp"] / base["mrp"] - 1) * 100) if base["mrp"] else 0.0
    t_add = put("trade_margin_add_pp", want["trade"] - base["trade"])
    mrp = base["mrp"] * (1 + price / 100)
    trade = min(0.4, max(0.0, row["trade"] + t_add / 100))
    invoice = mrp * (1 - trade)                          # per litre, with the price and trade margin the engine will use
    if invoice > 0:
        put("promo_add_pp", (want["promo"] / invoice - row["promo_rate"]) * 100)
        put("rebate_add_pp", (want["other"] / invoice - row["other_rate"]) * 100)
    if base["volume"] > 0:
        put("volume_change_pct", (want["volume"] / base["volume"] - 1) * 100)
    return {k: v for k, v in out.items() if abs(v) > TOL}, limited


def _pl_columns(limited):
    """The baseline P&L block: the P&L screen's money and % columns, with litres and kg together as one volume per day."""
    days = data.days_per_month()
    cols = [("Volume per day (LPD)", "vol_l", days, "#,##0;[Red]-#,##0")]
    cols += [c for c in COLUMNS if c[1] not in ("units", "litres", "kg")]
    return [c for c in cols if not (limited and c[1] in sim.BELOW_CONTRIBUTION)]


def build_template(levers_raw, allowed=None, limited=False, scope_raw=None):
    """The workbook, with the Scenario columns holding what is set on the screen now (the baseline when nothing is).
    Returns a BytesIO."""
    lv, adjust = sim.split_levers(levers_raw)
    scope = sim.parse_scope(scope_raw)
    resolve = sim.make_resolver(lv, adjust)
    wb = Workbook()
    ws = wb.active
    ws.title = READ_ME
    for k, line in enumerate(README_LINES, 1):
        c = ws.cell(row=k, column=1, value=line)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if k == 1:
            c.font = Font(bold=True, size=13)
    note = (f"Baseline: {data.period_label(data.META)}." if data.MODE == "actual" else "Sample data, not actuals.")
    ws.cell(row=len(README_LINES) + 2, column=1, value=note).font = GREY
    ws.column_dimensions["A"].width = 120

    ws = wb.create_sheet(LEVERS_SHEET)
    ws.append(["Lever", "Value", "Unit", "Allowed range", "Code"])
    for key in GLOBAL_LEVERS:
        label, unit = LEVER_LABELS[key]
        lo, hi = sim.LIMITS[key]
        ws.append([label, getattr(lv, key), unit, f"{lo:g} to {hi:g}", key])
        ws.cell(row=ws.max_row, column=2).fill = INPUT_FILL
        ws.cell(row=ws.max_row, column=5).font = GREY
    _header(ws, [40, 12, 16, 16, 22])

    ws = wb.create_sheet(ROWS_SHEET)
    titles = [t for _, t in data.DIM_DEFS]
    pl = _pl_columns(limited)
    header = list(titles)
    for _, t, _ in DRIVERS:
        header += [f"Baseline: {t}", f"Scenario: {t}"]
    header += [f"Baseline: {c[0]}" for c in pl] + ["Key"]
    ws.append(header)
    n_dims, first_driver = len(titles), len(titles) + 1
    first_pl = first_driver + 2 * len(DRIVERS)
    last = first_pl + len(pl)
    base = sim.base_metrics()
    order = sorted(_row_indices(allowed), key=lambda i: (-base[i]["net"], i))
    for i in order:
        row = data.ROWS[i]
        b = _terms(row, sim.DEFAULT_LEVERS)
        sc = _terms(row, resolve(row)) if (b and sim.in_scope(row, scope)) else b
        m = sim.derive(base[i])
        m["vol_l"] = m["litres"] + m["kg"]
        cells = []
        for k, _, _ in DRIVERS:
            cells += [b[k], sc[k]] if b else [None, None]
        ws.append([data.LABEL[d].get(row[d], row[d]) for d, _ in data.DIM_DEFS] + cells +
                  [m[key] / scale for _, key, scale, _ in pl] + [row["rowkey"]])
    for r in range(2, ws.max_row + 1):
        for d, (_, _, fmt) in enumerate(DRIVERS):
            b_cell, s_cell = ws.cell(row=r, column=first_driver + 2 * d), ws.cell(row=r, column=first_driver + 2 * d + 1)
            b_cell.number_format = s_cell.number_format = fmt
            b_cell.fill, b_cell.font = BASE_FILL, GREY
            s_cell.fill = INPUT_FILL
        for c, (_, _, _, fmt) in enumerate(pl, start=first_pl):
            ws.cell(row=r, column=c).number_format = fmt
        ws.cell(row=r, column=last).number_format = "@"
    _header(ws, [24, 18, 16, 16, 20, 20, 16, 14, 30] + [15] * (2 * len(DRIVERS)) + [14] * len(pl) + [10])
    ws.column_dimensions[get_column_letter(last)].hidden = True
    ws.auto_filter.ref = f"A1:{get_column_letter(last)}{max(ws.max_row, 2)}"
    ws.freeze_panes = ws.cell(row=2, column=2)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def read_upload(stream, allowed=None):
    """Reads an uploaded sheet. Returns {"levers": {all seven}, "adjust": {...}, "report": {...}}.

    The report counts what was used and lists, in words, what was ignored or brought into range, so nothing is dropped
    silently. A cell that is not a number stops the upload, with its sheet and cell. The commercial levers come back as
    zero and the changes as per-row adjustments: the Scenario columns already hold everything that was set before."""
    raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise SheetError("The file is too large. Upload the sheet downloaded from this page.")
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise SheetError("This is not an Excel file (.xlsx). Download the scenario sheet from this page and fill that in.")
    names = set(wb.sheetnames)
    if LEVERS_SHEET not in names and ROWS_SHEET not in names:
        raise SheetError("This is not a scenario sheet. Download one from this page and fill that in.")

    report = {"changed_rows": 0, "levers_set": 0, "ignored": [], "clamped": []}
    levers = {}
    if LEVERS_SHEET in names:
        for n, row in enumerate(wb[LEVERS_SHEET].iter_rows(min_row=2, max_col=5, values_only=True), start=2):
            key = _code(row[4]) if len(row) > 4 else ""
            if key not in GLOBAL_LEVERS:
                continue
            v = _number(row[1], f"{LEVERS_SHEET} sheet, cell B{n}")
            if v is None:
                continue
            lo, hi = sim.LIMITS[key]
            if not lo <= v <= hi:
                report["clamped"].append(f"{LEVER_LABELS[key][0]} ({v:g}) was brought to {min(hi, max(lo, v)):g}")
            levers[key] = min(hi, max(lo, v))
    levers = {k: levers.get(k, 0.0) for k in sim.LIMITS}
    report["levers_set"] = sum(1 for v in levers.values() if v)

    rows = {}
    if ROWS_SHEET in names:
        by_key = {data.ROWS[i]["rowkey"]: data.ROWS[i] for i in _row_indices(allowed)}
        lines = wb[ROWS_SHEET].iter_rows(values_only=True)
        head = [str(h).strip() if h is not None else "" for h in (next(lines, None) or ())]
        if "Key" not in head:
            raise SheetError("The Rows sheet has lost its last (hidden) Key column. Download the sheet again and fill that in.")
        at = {k: head.index(f"Scenario: {t}") for k, t, _ in DRIVERS if f"Scenario: {t}" in head}
        if not at:
            raise SheetError("The Rows sheet has no Scenario columns. Download the sheet again and fill that in.")
        key_col = head.index("Key")
        unknown = no_litres = 0
        for n, line in enumerate(lines, start=2):
            if n > MAX_ROWS:
                raise SheetError(f"The {ROWS_SHEET} sheet has too many lines.")
            line = tuple(line) + (None,) * (key_col + 1 - len(line))
            scen = {}
            for k, col in at.items():
                v = _number(line[col], f"{ROWS_SHEET} sheet, cell {get_column_letter(col + 1)}{n}")
                if v is not None:
                    scen[k] = v
            code = str(line[key_col] or "").strip()
            if not scen or code not in by_key:
                unknown += 1 if (scen and code not in by_key) else 0
                continue
            row = by_key[code]
            changes, limited = row_changes(row, scen)
            if _terms(row, sim.DEFAULT_LEVERS) is None:
                no_litres += 1
                continue
            for lever, want, got in limited:
                report["clamped"].append(
                    f"{ROWS_SHEET} sheet line {n}: {LEVER_LABELS[lever][0]} would be {want:+.1f}, more than the engine allows; brought to {got:+g}")
            if changes:
                rows[code] = changes
        if unknown:
            report["ignored"].append(f"{unknown} line{'s' if unknown > 1 else ''} on the {ROWS_SHEET} sheet that are not rows of this baseline")
        if no_litres:
            report["ignored"].append(f"{no_litres} line{'s' if no_litres > 1 else ''} whose pack size is unknown, so there is no litre figure")
    wb.close()

    adjust = sim.parse_adjust({"row": rows})       # the same clean-up as everywhere else
    report["changed_rows"] = len(adjust.get("row", {}))
    return {"levers": levers, "adjust": adjust, "report": report}
