"""Excel export of the P&L tree and the scenario levers."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import simulate as sim

LEVER_LABELS = {
    "price_change_pct": ("Price (MRP) change", "%"),
    "promo_add_pp": ("Extra promo discount", "pp of invoice"),
    "promo_pass_through_pct": ("Promo passed to shelf price", "%"),
    "trade_margin_add_pp": ("Trade margin change", "pp of MRP"),
    "rebate_add_pp": ("Rebates and display fees", "pp of invoice"),
    "elasticity_scale": ("Price sensitivity multiplier", "x"),
    "manual_uplift_pct": ("Extra volume from visibility", "%"),
    "milk_cost_change_pct": ("Milk procurement cost", "%"),
    "freight_change_pct": ("S&D / freight cost", "%"),
}

HEADER_FILL = PatternFill("solid", fgColor="123524")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TOTAL_FONT = Font(bold=True)

# (header, metric key, scale, number format)
COLUMNS = [
    ("Volume ('000 units)", "units", 1e3, "#,##0.0"),
    ("Gross sales", "gross", 1e5, "#,##0.0"),
    ("Promo", "promo", 1e5, "#,##0.0"),
    ("Rebates", "rebates", 1e5, "#,##0.0"),
    ("Net revenue", "net", 1e5, "#,##0.0"),
    ("COGS", "cogs", 1e5, "#,##0.0"),
    ("Gross margin", "gm", 1e5, "#,##0.0"),
    ("Over/under", "ou", 1e5, "#,##0.0"),
    ("S&D / freight", "logistics", 1e5, "#,##0.0"),
    ("Contribution", "cm", 1e5, "#,##0.0"),
    ("P&A", "pa", 1e5, "#,##0.0"),
    ("Interest", "interest", 1e5, "#,##0.0"),
    ("Marketing", "mktg", 1e5, "#,##0.0"),
    ("Net margin", "nm", 1e5, "#,##0.0"),
    ("GM %", "gm_pct", 0.01, "0.0"),
    ("CM %", "cm_pct", 0.01, "0.0"),
    ("NM %", "nm_pct", 0.01, "0.0"),
]


def _flatten(nodes, depth=0):
    for n in nodes:
        yield depth, n
        yield from _flatten(n.get("children", []), depth + 1)


def _filter_text(flt_raw):
    from . import data
    parts = []
    for dim, title in data.DIM_DEFS:
        ids = (flt_raw or {}).get(dim) or []
        names = [data.LABEL[dim][i] for i in ids if i in data.LABEL[dim]]
        if names:
            parts.append(f"{title}: {', '.join(names)}")
    return "; ".join(parts) or "none (all rows)"


def _basis_text():
    from . import data
    months = data.months_text(data.META.get("base_months", []))
    if data.MODE == "actual":
        return f"Monthly run-rate, {months} actual billing." if months else "Monthly run-rate, actual billing."
    return "Monthly run-rate. Sample data, not actuals."


def build_workbook(levers_raw, scope_raw, dims, filter_raw=None):
    res = sim.pnl(levers_raw, scope_raw, dims, filter_raw)
    lv = sim.parse_levers(levers_raw)
    wb = Workbook()

    for title, side in (("Scenario P&L", "sim"), ("Baseline P&L", "base")):
        ws = wb.active if title == "Scenario P&L" else wb.create_sheet(title)
        ws.title = title
        ws.append([f"Amounts in Rs lakh unless stated. {_basis_text()} "
                   f"Filters: {_filter_text(filter_raw)}"])
        ws.append(["Name"] + [c[0] for c in COLUMNS])
        for cell in ws[2]:
            cell.fill, cell.font = HEADER_FILL, HEADER_FONT
            cell.alignment = Alignment(horizontal="center", wrap_text=True)

        def emit(label, metrics, depth, bold=False):
            ws.append(["    " * depth + label] +
                      [metrics[k] / scale for _, k, scale, _ in COLUMNS])
            row = ws.max_row
            for i, (_, _, _, fmt) in enumerate(COLUMNS, start=2):
                ws.cell(row=row, column=i).number_format = fmt
            if bold:
                for cell in ws[row]:
                    cell.font = TOTAL_FONT

        emit("Total", res["total"][side], 0, bold=True)
        for depth, node in _flatten(res["tree"]):
            emit(node["label"], node[side], depth, bold=depth == 0)
        ws.column_dimensions["A"].width = 38
        for i in range(2, len(COLUMNS) + 2):
            ws.column_dimensions[get_column_letter(i)].width = 14
        ws.freeze_panes = "B3"

    ws = wb.create_sheet("Scenario levers")
    ws.append(["Lever", "Value", "Unit"])
    for cell in ws[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
    for key, (label, unit) in LEVER_LABELS.items():
        ws.append([label, getattr(lv, key), unit])
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["C"].width = 16

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
