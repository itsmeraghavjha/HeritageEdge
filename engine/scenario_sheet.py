"""A scenario sheet: download the levers and the per-row adjustments as Excel, change them there, upload it back.

Nothing here does any maths. The sheet is only another way to fill in what the simulator already takes: the seven
levers and the row adjustments (a lever set on one chain, product category, SKU or state, see `simulate.ADJUST_LEVERS`).
The upload becomes the `levers` payload (with `adjust`) that the P&L screen sends to the engine as before.

Layout: a "Levers" sheet (one row per lever) and one sheet per grouping, listing every chain, category, SKU or state the
person may see, largest first, with the five commercial levers to fill in. Blank means no change. Codes are in a grey
column so the upload does not depend on a name being spelled the same.
"""
import io
import re

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import data
from . import simulate as sim
from .export import HEADER_FILL, HEADER_FONT, LEVER_LABELS

MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 20000
INPUT_FILL = PatternFill("solid", fgColor="FFF8DC")
GREY = Font(color="808080")

LEVERS_SHEET = "Levers"
READ_ME = "Read me"
# sheet name -> dimension id. Names are what a person sees; the upload finds each sheet by name.
GROUP_SHEETS = {"Chain": "chain", "Product category": "family", "SKU": "sku", "State": "state"}

README_LINES = [
    "How to use this sheet",
    "1. Fill in the yellow cells. A blank cell means no change.",
    "2. Levers sheet: the same seven levers as on the simulator. They apply to the rows in the scope chosen on the screen.",
    "3. Chain, Product category, SKU and State sheets: a change on one chain, category, SKU or state, added on top of the Levers sheet. "
    "A row that matches several (a chain and a SKU, say) gets them added up.",
    "4. Units are the same as on the screen: price and volume in %, promo, trade margin and other deductions in percentage points (pp). "
    "A value outside the allowed range is brought back to the nearest allowed value, and the upload tells you.",
    "5. Keep the grey Code column as it is. Do not rename the sheets or the columns.",
    "6. Upload the file on the P&L page. The P&L then shows this scenario against the baseline.",
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
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = "A2"


def _base_net(dim, allowed):
    """{code: baseline net revenue} over the rows the person may see."""
    out = {}
    for i in (range(len(data.ROWS)) if allowed is None else allowed):
        row, metrics = data.ROWS[i], sim.base_metrics()[i]
        out[str(row[dim])] = out.get(str(row[dim]), 0.0) + metrics["net"]
    return out


def build_template(levers_raw, allowed=None):
    """The workbook, filled with the levers and row adjustments the person has set now. Returns a BytesIO."""
    lv, adjust = sim.split_levers(levers_raw)
    wb = Workbook()
    ws = wb.active
    ws.title = READ_ME
    for k, line in enumerate(README_LINES, 1):
        c = ws.cell(row=k, column=1, value=line)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if k == 1:
            c.font = Font(bold=True, size=13)
    note = (f"Baseline: {data.months_text(data.META.get('base_months', [])) or 'sample data'}. "
            f"{data.period_label(data.META)}." if data.MODE == "actual" else "Sample data, not actuals.")
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

    titles = dict(data.DIM_DEFS)
    for sheet, dim in GROUP_SHEETS.items():
        ws = wb.create_sheet(sheet)
        net = _base_net(dim, allowed)
        ws.append([titles[dim], "Baseline net revenue (Rs lakh)"] +
                  [f"{LEVER_LABELS[k][0]} ({LEVER_LABELS[k][1]})" for k in sim.ADJUST_LEVERS] + ["Code"])
        have = adjust.get(dim, {})
        for code in sorted(net, key=lambda c: -net[c]):
            ws.append([data.LABEL[dim].get(code, code), net[code] / 1e5] +
                      [have.get(code, {}).get(k) for k in sim.ADJUST_LEVERS] + [code])
            r = ws.max_row
            ws.cell(row=r, column=2).number_format = "#,##0.0"
            for c in range(3, 3 + len(sim.ADJUST_LEVERS)):
                ws.cell(row=r, column=c).fill = INPUT_FILL
            code_cell = ws.cell(row=r, column=3 + len(sim.ADJUST_LEVERS))
            code_cell.number_format = "@"
            code_cell.value = str(code)
            code_cell.font = GREY
        _header(ws, [38, 18] + [17] * len(sim.ADJUST_LEVERS) + [14])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def read_upload(stream, allowed=None):
    """Reads an uploaded sheet. Returns {"levers": {all seven}, "adjust": {...}, "report": {...}}.

    The report counts what was used and lists, in words, what was ignored or brought back into range, so nothing is
    dropped silently. A cell that is not a number stops the upload, with its sheet and cell."""
    raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise SheetError("The file is too large. Upload the sheet downloaded from this page.")
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise SheetError("This is not an Excel file (.xlsx). Download the scenario sheet from this page and fill that in.")
    names = set(wb.sheetnames)
    if LEVERS_SHEET not in names and not (names & set(GROUP_SHEETS)):
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

    seen_codes = {dim: set(_base_net(dim, allowed)) for dim in GROUP_SHEETS.values()}
    adjust = {}
    for sheet, dim in GROUP_SHEETS.items():
        if sheet not in names:
            continue
        unknown = 0
        width = 3 + len(sim.ADJUST_LEVERS)
        for n, row in enumerate(wb[sheet].iter_rows(min_row=2, max_col=width, values_only=True), start=2):
            if n > MAX_ROWS:
                raise SheetError(f"The {sheet} sheet has too many rows.")
            row = tuple(row) + (None,) * (width - len(row))
            code = _code(row[width - 1])
            entered = {}
            for k, cell, key in zip(range(len(sim.ADJUST_LEVERS)), row[2:width - 1], sim.ADJUST_LEVERS):
                v = _number(cell, f"{sheet} sheet, cell {get_column_letter(3 + k)}{n}")
                if v:
                    entered[key] = v
            if not entered:
                continue
            if code not in seen_codes[dim]:
                unknown += 1                       # not in the file's list, or not one this person may see
                continue
            for key, v in list(entered.items()):
                lo, hi = sim.LIMITS[key]
                if not lo <= v <= hi:
                    report["clamped"].append(
                        f"{sheet} {data.LABEL[dim].get(code, code)}: {LEVER_LABELS[key][0]} ({v:g}) was brought to {min(hi, max(lo, v)):g}")
            adjust.setdefault(dim, {})[code] = entered
        if unknown:
            report["ignored"].append(f"{unknown} row{'s' if unknown > 1 else ''} on the {sheet} sheet with a code that is not in the data")
    wb.close()

    adjust = sim.parse_adjust(adjust)              # the same clamping and clean-up as everywhere else
    report["changed_rows"] = sum(len(v) for v in adjust.values())
    return {"levers": levers, "adjust": adjust, "report": report}
