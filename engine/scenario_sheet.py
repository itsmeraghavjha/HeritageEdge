"""A scenario sheet: download the levers and the rows as Excel, change them there, upload it back.

Nothing here does any maths. The sheet is only another way to fill in what the simulator already takes: the seven levers
and the row adjustments (`simulate.ADJUST_LEVERS`, here set on one dataset row each).
The upload becomes the `levers` payload (with `adjust`) that the P&L screen sends to the engine as before.

Layout: a "Levers" sheet (one line per lever) and one "Rows" sheet with one line per dataset row the person may see
(chain, customer group, state, city, sales office, category, variant, pack, SKU), with the five commercial levers to fill
in. Use Excel's filter to find the rows, then type or fill down. Blank means no change. A hidden last column holds the
row's key, so the upload does not depend on how a name is spelled.
"""
import io

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import data
from . import simulate as sim
from .export import HEADER_FILL, HEADER_FONT, LEVER_LABELS

MAX_BYTES = 40 * 1024 * 1024
MAX_ROWS = 300000
INPUT_FILL = PatternFill("solid", fgColor="FFF8DC")
GREY = Font(color="808080")

LEVERS_SHEET = "Levers"
ROWS_SHEET = "Rows"
READ_ME = "Read me"

README_LINES = [
    "How to use this sheet",
    "1. Fill in the yellow cells. A blank cell means no change.",
    "2. Levers sheet: the same seven levers as on the simulator. They apply to the rows in the scope chosen on the screen.",
    "3. Rows sheet: one line for each chain, customer group, state, city, sales office, product and pack the baseline holds. "
    "Use the filter arrows on the header to find the rows you want (one SKU in one state for one chain, say), then type a value or "
    "fill it down. What you enter there is added on top of the Levers sheet for that row only.",
    "4. Units are the same as on the screen: price and volume in %, promo, trade margin and other deductions in percentage points (pp). "
    "A value outside the allowed range is brought back to the nearest allowed value, and the upload tells you.",
    "5. Do not rename the sheets or the columns, and do not delete the hidden last column of the Rows sheet: it is how each line is matched.",
    "6. Row changes already set on the screen (on a chain, SKU or state) are shown here on every row they reach, so one file holds everything.",
    "7. Upload the file on the P&L page. The P&L then shows this scenario against the baseline.",
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


def _set_now(row, adjust):
    """What is set on the screen for this row: the adjustments of its chain, category, SKU and state, and of the row
    itself, added up the way the engine adds them."""
    out = {}
    for dim, by_id in adjust.items():
        got = by_id.get(str(row[sim.ADJUST_FIELD.get(dim, dim)]))
        for k, v in (got or {}).items():
            out[k] = out.get(k, 0.0) + v
    return out


def build_template(levers_raw, allowed=None):
    """The workbook, filled with the levers and row changes set now. Returns a BytesIO."""
    lv, adjust = sim.split_levers(levers_raw)
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
    ws.column_dimensions["A"].width = 110

    ws = wb.create_sheet(LEVERS_SHEET)
    ws.append(["Lever", "Value", "Unit", "Allowed range", "Code"])
    for key, (label, unit) in LEVER_LABELS.items():
        lo, hi = sim.LIMITS[key]
        ws.append([label, getattr(lv, key), unit, f"{lo:g} to {hi:g}", key])
        r = ws.max_row
        ws.cell(row=r, column=2).fill = INPUT_FILL
        ws.cell(row=r, column=5).font = GREY
    _header(ws, [40, 12, 16, 16, 22])

    ws = wb.create_sheet(ROWS_SHEET)
    titles = [t for _, t in data.DIM_DEFS]
    lever_cols = [f"{LEVER_LABELS[k][0]} ({LEVER_LABELS[k][1]})" for k in sim.ADJUST_LEVERS]
    ws.append(titles + ["Baseline net revenue (Rs lakh)"] + lever_cols + ["Key"])
    n_dims, first_lever = len(titles), len(titles) + 2
    last = first_lever + len(sim.ADJUST_LEVERS)                       # the key column
    base = sim.base_metrics()
    order = sorted(_row_indices(allowed), key=lambda i: (-base[i]["net"], i))
    for i in order:
        row = data.ROWS[i]
        now = _set_now(row, adjust)
        ws.append([data.LABEL[d].get(row[d], row[d]) for d, _ in data.DIM_DEFS] + [base[i]["net"] / 1e5] +
                  [now.get(k) or None for k in sim.ADJUST_LEVERS] + [row["rowkey"]])
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=n_dims + 1).number_format = "#,##0.00"
        for c in range(first_lever, last):
            ws.cell(row=r, column=c).fill = INPUT_FILL
        ws.cell(row=r, column=last).number_format = "@"
    _header(ws, [24, 18, 16, 16, 20, 20, 16, 14, 30, 16] + [17] * len(sim.ADJUST_LEVERS) + [10])
    ws.column_dimensions[get_column_letter(last)].hidden = True
    ws.auto_filter.ref = f"A1:{get_column_letter(last)}{max(ws.max_row, 2)}"
    ws.freeze_panes = ws.cell(row=2, column=2)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def read_upload(stream, allowed=None):
    """Reads an uploaded sheet. Returns {"levers": {all seven}, "adjust": {...}, "report": {...}}.

    The report counts what was used and lists, in words, what was ignored or brought back into range, so nothing is
    dropped silently. A cell that is not a number stops the upload, with its sheet and cell. The adjustments that come
    back are all on single rows; they replace whatever was set on the screen, which the sheet already showed."""
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
            if key not in sim.LIMITS:
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
        keys = {data.ROWS[i]["rowkey"]: i for i in _row_indices(allowed)}
        n_dims = len(data.DIM_DEFS)
        first, width = n_dims + 2, n_dims + 2 + len(sim.ADJUST_LEVERS)           # first lever column, key column
        unknown = 0
        for n, row in enumerate(wb[ROWS_SHEET].iter_rows(min_row=2, max_col=width, values_only=True), start=2):
            if n > MAX_ROWS:
                raise SheetError(f"The {ROWS_SHEET} sheet has too many lines.")
            row = tuple(row) + (None,) * (width - len(row))
            entered = {}
            for k, (cell, key) in enumerate(zip(row[first - 1:width - 1], sim.ADJUST_LEVERS)):
                v = _number(cell, f"{ROWS_SHEET} sheet, cell {get_column_letter(first + k)}{n}")
                if v:
                    entered[key] = v
            if not entered:
                continue
            code = str(row[width - 1] or "").strip()
            if code not in keys:
                unknown += 1                         # not in the baseline, or not one this person may see
                continue
            for key, v in entered.items():
                lo, hi = sim.LIMITS[key]
                if not lo <= v <= hi:
                    report["clamped"].append(f"{ROWS_SHEET} sheet line {n}: {LEVER_LABELS[key][0]} ({v:g}) was brought to {min(hi, max(lo, v)):g}")
            rows[code] = entered
        if unknown:
            report["ignored"].append(f"{unknown} line{'s' if unknown > 1 else ''} on the {ROWS_SHEET} sheet that are not rows of this baseline")
    wb.close()

    adjust = sim.parse_adjust({"row": rows})       # the same clamping and clean-up as everywhere else
    report["changed_rows"] = len(adjust.get("row", {}))
    return {"levers": levers, "adjust": adjust, "report": report}
