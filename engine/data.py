"""Illustrative modern-trade dairy data for HeritageEdge.

Everything here is made-up sample data so the app works out of the box.
To use real numbers, replace build_rows() with a loader that returns the same
row shape: {"chain", "state", "so", "sku", "units"} where units is the
baseline monthly volume for that chain / sales office / SKU combination.
"""
import random

VOLUME_SCALE = 4.5

STATES = [
    {"id": "TS", "name": "Telangana", "freight_factor": 1.00},
    {"id": "AP", "name": "Andhra Pradesh", "freight_factor": 1.10},
    {"id": "KA", "name": "Karnataka", "freight_factor": 1.15},
    {"id": "TN", "name": "Tamil Nadu", "freight_factor": 1.20},
    {"id": "MH", "name": "Maharashtra", "freight_factor": 1.60},
]

SALES_OFFICES = [
    {"id": "hyd-c", "name": "Hyderabad Central", "state": "TS", "weight": 1.6},
    {"id": "hyd-n", "name": "Hyderabad North", "state": "TS", "weight": 1.3},
    {"id": "wgl", "name": "Warangal", "state": "TS", "weight": 0.6},
    {"id": "knr", "name": "Karimnagar", "state": "TS", "weight": 0.5},
    {"id": "vja", "name": "Vijayawada", "state": "AP", "weight": 1.0},
    {"id": "vsp", "name": "Visakhapatnam", "state": "AP", "weight": 0.9},
    {"id": "tpt", "name": "Tirupati", "state": "AP", "weight": 0.5},
    {"id": "gnt", "name": "Guntur", "state": "AP", "weight": 0.6},
    {"id": "blr-n", "name": "Bengaluru North", "state": "KA", "weight": 1.2},
    {"id": "blr-s", "name": "Bengaluru South", "state": "KA", "weight": 1.1},
    {"id": "mys", "name": "Mysuru", "state": "KA", "weight": 0.5},
    {"id": "maa", "name": "Chennai", "state": "TN", "weight": 1.0},
    {"id": "cbe", "name": "Coimbatore", "state": "TN", "weight": 0.5},
    {"id": "bom", "name": "Mumbai", "state": "MH", "weight": 0.8},
    {"id": "pnq", "name": "Pune", "state": "MH", "weight": 0.6},
]

# trade_margin: share of MRP kept by the chain.
# rebate / base_promo: share of invoice value (display fees, listing fees, schemes).
CHAINS = [
    {"id": "dmart", "name": "DMart", "trade_margin": 0.115, "rebate": 0.020,
     "base_promo": 0.015, "size": 1.30, "states": ["TS", "AP", "KA", "MH"]},
    {"id": "reliance", "name": "Reliance Retail", "trade_margin": 0.125, "rebate": 0.025,
     "base_promo": 0.020, "size": 1.20, "states": ["TS", "AP", "KA", "TN", "MH"]},
    {"id": "more", "name": "More Retail", "trade_margin": 0.140, "rebate": 0.022,
     "base_promo": 0.020, "size": 0.80, "states": ["TS", "AP", "KA", "TN"]},
    {"id": "spencers", "name": "Spencer's", "trade_margin": 0.150, "rebate": 0.028,
     "base_promo": 0.025, "size": 0.45, "states": ["TS", "AP", "TN"]},
    {"id": "metro", "name": "Metro Cash & Carry", "trade_margin": 0.090, "rebate": 0.015,
     "base_promo": 0.010, "size": 0.70, "states": ["TS", "KA", "TN", "MH"]},
    {"id": "ratnadeep", "name": "Ratnadeep", "trade_margin": 0.135, "rebate": 0.020,
     "base_promo": 0.018, "size": 0.55, "states": ["TS", "AP"]},
]

# mrp, cogs and freight are per unit in rupees. milk_share is the share of COGS
# driven by milk procurement. elasticity is the price elasticity of demand.
SKUS = [
    {"id": "toned-500", "name": "Toned milk 500 ml", "category": "Milk", "mrp": 27,
     "cogs": 20.0, "milk_share": 0.85, "freight": 0.45, "elasticity": -0.35, "base_units": 9000},
    {"id": "fcm-500", "name": "Full cream milk 500 ml", "category": "Milk", "mrp": 34,
     "cogs": 25.0, "milk_share": 0.85, "freight": 0.45, "elasticity": -0.45, "base_units": 5500},
    {"id": "curd-400", "name": "Curd cup 400 g", "category": "Curd and beverages", "mrp": 40,
     "cogs": 27.5, "milk_share": 0.70, "freight": 0.70, "elasticity": -0.80, "base_units": 3000},
    {"id": "curd-1kg", "name": "Curd tub 1 kg", "category": "Curd and beverages", "mrp": 85,
     "cogs": 56.0, "milk_share": 0.70, "freight": 1.40, "elasticity": -0.90, "base_units": 900},
    {"id": "lassi-200", "name": "Lassi 200 ml", "category": "Curd and beverages", "mrp": 20,
     "cogs": 12.5, "milk_share": 0.55, "freight": 0.40, "elasticity": -1.00, "base_units": 2000},
    {"id": "bm-200", "name": "Buttermilk 200 ml", "category": "Curd and beverages", "mrp": 15,
     "cogs": 9.5, "milk_share": 0.50, "freight": 0.35, "elasticity": -0.90, "base_units": 2200},
    {"id": "shake-180", "name": "Milkshake 180 ml", "category": "Curd and beverages", "mrp": 30,
     "cogs": 19.0, "milk_share": 0.50, "freight": 0.45, "elasticity": -1.20, "base_units": 900},
    {"id": "paneer-200", "name": "Paneer 200 g", "category": "Value-added", "mrp": 95,
     "cogs": 66.0, "milk_share": 0.75, "freight": 1.20, "elasticity": -1.10, "base_units": 1100},
    {"id": "butter-100", "name": "Butter 100 g", "category": "Value-added", "mrp": 58,
     "cogs": 40.0, "milk_share": 0.80, "freight": 0.90, "elasticity": -1.00, "base_units": 700},
    {"id": "ghee-1l", "name": "Ghee 1 L carton", "category": "Value-added", "mrp": 640,
     "cogs": 470.0, "milk_share": 0.88, "freight": 3.50, "elasticity": -1.30, "base_units": 450},
    {"id": "cheese-200", "name": "Cheese slices 200 g", "category": "Value-added", "mrp": 135,
     "cogs": 92.0, "milk_share": 0.65, "freight": 1.40, "elasticity": -1.50, "base_units": 280},
]

CHAIN_BY_ID = {c["id"]: c for c in CHAINS}
STATE_BY_ID = {s["id"]: s for s in STATES}
SO_BY_ID = {s["id"]: s for s in SALES_OFFICES}
SKU_BY_ID = {s["id"]: s for s in SKUS}


def build_rows(seed=7):
    """One row per chain x sales office x SKU, with baseline monthly units."""
    rng = random.Random(seed)
    rows = []
    for chain in CHAINS:
        for so in SALES_OFFICES:
            if so["state"] not in chain["states"]:
                continue
            for sku in SKUS:
                if rng.random() < 0.08:  # chain does not stock every SKU everywhere
                    continue
                noise = rng.uniform(0.85, 1.15)
                units = sku["base_units"] * chain["size"] * so["weight"] * noise * VOLUME_SCALE
                rows.append({
                    "chain": chain["id"],
                    "state": so["state"],
                    "so": so["id"],
                    "sku": sku["id"],
                    "units": round(units),
                })
    return rows


ROWS = build_rows()
