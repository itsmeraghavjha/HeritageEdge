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
    "freight_change_pct": ("Freight cost", "%"),
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
    ("Logistics", "logistics", 1e5, "#,##0.0"),
    ("Sales and distribution", "sd", 1e5, "#,##0.0"),
    ("Contribution", "cm", 1e5, "#,##0.0"),
    ("GM %", "gm_pct", 0.01, "0.0"),
    ("CM %", "cm_pct", 0.01, "0.0"),
]


def _flatten(nodes, depth=0):
    for n in nodes:
        yield depth, n
        yield from _flatten(n.get("children", []), depth + 1)


def build_workbook(levers_raw, scope_raw, dims):
    res = sim.pnl(levers_raw, scope_raw, dims)
    lv = sim.parse_levers(levers_raw)
    wb = Workbook()

    for title, side in (("Scenario P&L", "sim"), ("Baseline P&L", "base")):
        ws = wb.active if title == "Scenario P&L" else wb.create_sheet(title)
        ws.title = title
        ws.append(["Amounts in Rs lakh unless stated. Monthly run-rate, illustrative data."])
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
