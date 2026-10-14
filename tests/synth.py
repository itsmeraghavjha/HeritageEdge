"""A tiny made-up world (customers, SKUs, offer file, billing, MIS) for testing the dataset builder.

Nothing here is real. The numbers are chosen so that the right answer is easy to work out by hand:
every customer x SKU x sales office bills 900, 1000 and 1100 packs in Jul, Aug and Sep (average 1000),
and a loud 5000 in Oct, which a Jul-Sep baseline must ignore.
"""
import pandas as pd
from openpyxl import Workbook

MONTHS = ["2026-07-01", "2026-08-01", "2026-09-01"]
OCT = "2026-10-01"
VOLUMES = {"2026-07-01": 900, "2026-08-01": 1000, "2026-09-01": 1100, OCT: 5000}
OTHER_RATE = 0.03

# cid, cluster, customer group, sales offices billed
CUSTOMERS = [
    (1001, "ALPHA", "Modern Formats", [2001]),
    (1002, "BETA", "E-Commerce", [2001, 2002]),
    (1003, "GAMMA", "Institutions", [2001]),       # a group the MIS does not cover
    (1004, "DELTA", "Modern Formats", [2001]),     # its offer line has no MRP
]
# sku, name, family, pack text, litres/kg per pack, MRP incl GST, GST, trade margin, offer Rs (on invoice), off invoice Rs
SKUS = [
    (7001, "Test milk 500 ml", "Milk", "500 ML", 0.5, 50.0, 0.05, 0.20, 2.0, 0.0),
    (7002, "Test curd 400 g", "Curd", "400 GMS", 0.4, 120.0, 0.12, 0.15, 0.0, 6.0),
]
OFFICES = {2001: ("Alpha City Sales Office", "Alphaville", "KARNATAKA"),
           2002: ("Beta Town Sales Office", "Betatown", "TELANGANA")}


def net_price(sku):
    """Billed price per pack ex GST: list invoice price less the offer and 3% of other deductions."""
    _, _, _, _, _, mrp, gst, trade, on, off = sku
    promo = (on + off) / (mrp * (1 - trade))
    return mrp / (1 + gst) * (1 - trade) * (1 - promo - OTHER_RATE)


def make_offer(path):
    rows = []
    for cid, cluster, _, _ in CUSTOMERS:
        for sku, name, _, _, _, mrp, gst, trade, on, off in SKUS:
            rows.append({"Customer": cid, "Sku Code": sku, "Cluster": cluster, "Material": name,
                         "New MRP": None if cid == 1004 else mrp, "TOT Margin on Invoice": trade,
                         "On Invoice offer": on, "Off Invoice Offer in Value": off, "GST%": gst})
    pd.DataFrame(rows).to_excel(path, index=False)


def billing_rows(months=None, include_specials=True):
    rows = []
    for cid, _, grp, offices in CUSTOMERS:
        for so in offices:
            for sku in SKUS:
                code, _, family, pack, size, *_ = sku
                for month in (months or list(VOLUMES)):
                    packs = VOLUMES[month]
                    rows.append({
                        "BillMonth": month, "CustomerGroup": grp, "CustomerID": cid, "SalesOfficeID": so,
                        "SalesOfficeName": OFFICES[so][0], "CITY": OFFICES[so][1], "STATE": OFFICES[so][2],
                        "MaterialCode": code, "ProductFamily": family, "Variant": "Plain", "PackSize": pack,
                        "SalesUnit": "L", "Qty_LtrKg": packs * size, "Packs": packs,
                        "InvoiceValue_exTax": packs * net_price(sku), "Tax": 0.0, "InvoiceValue_inclTax": 0.0,
                        "BillingLines": 10})
    if include_specials:
        # 1. SQL could not read the pack size (Packs empty) but the text is readable: the builder must recover it
        for r in rows:
            if (r["CustomerID"], r["MaterialCode"], r["SalesOfficeID"], r["BillMonth"]) == (1001, 7001, 2001, "2026-09-01"):
                r["Packs"] = None
        # 2. a line whose pack size cannot be read at all: it must be dropped and counted
        rows.append({**rows[0], "CustomerID": 1002, "SalesOfficeID": 2002, "MaterialCode": 7002, "BillMonth": "2026-07-01",
                     "PackSize": "ASSORTED", "Packs": None, "Qty_LtrKg": 300, "InvoiceValue_exTax": 30000.0})
    return pd.DataFrame(rows)


def make_billing(path, **kw):
    billing_rows(**kw).to_csv(path, index=False)


MIS_HEADERS = ["Row", "Row2", "Customer Group", "Actual data SKU Qty", "Actual data Revenue", "Actual data COGS",
               "Actual data S&D Costs", "Actual data P&A Cost", "Actual data Interest", "Actual data O/U Abs",
               "Mktg Cost", "ProdHier01-1", "PCCODE", "Month", "Year", "filler3", "filler4", "filler5",
               "filler6", "SKU WITHOUT A USABLE HEADER", "filler7", "filler8"]
MIS_COGS_PER_PACK, MIS_SD_PER_PACK, MIS_OU_PER_PACK = 20.0, 1.0, 0.4
MIS_PA_PCT, MIS_INTEREST_PCT, MIS_MKTG_PCT = 0.03, 0.005, 0.003


# April is a trap: its COGS per pack is double. A build that does not keep to the base months picks it up.
MIS_MONTHS = (("Apr", 2.0), ("Jul", 1.0), ("Aug", 1.0), ("Sep", 1.0))


def make_mis(path, sheet="SAP_MIS_DATABASE", drop_column=None, months=MIS_MONTHS, month_style="name"):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    headers = [h for h in MIS_HEADERS if h != drop_column]
    ws.append(headers)
    idx = {h: i for i, h in enumerate(headers)}
    for month, cogs_factor in months:
        label = {"name": month, "dated": f"{month}-26", "number": {"Apr": 4, "Jul": 7, "Aug": 8, "Sep": 9}[month]}[month_style]
        for grp in ("Modern Formats", "E-Commerce", "Institutions"):
            for so in OFFICES:
                for sku in SKUS:
                    qty = 10000.0
                    rev = qty * 30.0
                    row = [None] * len(headers)
                    row[0], row[1] = "x", "y"
                    values = {"Customer Group": grp, "Actual data SKU Qty": qty, "Actual data Revenue": rev,
                              "Actual data COGS": qty * MIS_COGS_PER_PACK * cogs_factor,
                              "Actual data S&D Costs": qty * MIS_SD_PER_PACK,
                              "Actual data P&A Cost": rev * MIS_PA_PCT, "Actual data Interest": rev * MIS_INTEREST_PCT,
                              "Actual data O/U Abs": qty * MIS_OU_PER_PACK, "Mktg Cost": rev * MIS_MKTG_PCT,
                              "ProdHier01-1": sku[2], "PCCODE": so, "Month": label, "Year": 2026}
                    for k, v in values.items():
                        if k in idx:
                            row[idx[k]] = v
                    row[19 if len(headers) > 19 else -1] = sku[0]
                    ws.append(row)
    wb.save(path)
