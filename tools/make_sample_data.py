"""Writes sample_data/rows.csv: synthetic, made-up dairy modern-trade data in the same shape as real data.

    python tools/make_sample_data.py

The output is committed so the app runs for anyone who clones the repo. None of it is real.
"""
import csv
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "sample_data" / "rows.csv"
VOLUME_SCALE = 4.5

STATES = {"Telangana": 1.00, "Andhra Pradesh": 1.10, "Karnataka": 1.15, "Tamil Nadu": 1.20, "Maharashtra": 1.60}

# code, name, city, state, weight
OFFICES = [
    ("1001", "Hyderabad Central Sales Office", "Hyderabad", "Telangana", 1.6),
    ("1002", "Hyderabad North Sales Office", "Hyderabad", "Telangana", 1.3),
    ("1003", "Warangal Sales Office", "Warangal", "Telangana", 0.6),
    ("1004", "Karimnagar Sales Office", "Karimnagar", "Telangana", 0.5),
    ("1005", "Vijayawada Sales Office", "Vijayawada", "Andhra Pradesh", 1.0),
    ("1006", "Visakhapatnam Sales Office", "Visakhapatnam", "Andhra Pradesh", 0.9),
    ("1007", "Tirupati Sales Office", "Tirupati", "Andhra Pradesh", 0.5),
    ("1008", "Guntur Sales Office", "Guntur", "Andhra Pradesh", 0.6),
    ("1009", "Bengaluru North Sales Office", "Bengaluru", "Karnataka", 1.2),
    ("1010", "Bengaluru South Sales Office", "Bengaluru", "Karnataka", 1.1),
    ("1011", "Mysuru Sales Office", "Mysuru", "Karnataka", 0.5),
    ("1012", "Chennai Sales Office", "Chennai", "Tamil Nadu", 1.0),
    ("1013", "Coimbatore Sales Office", "Coimbatore", "Tamil Nadu", 0.5),
    ("1014", "Mumbai Sales Office", "Mumbai", "Maharashtra", 0.8),
    ("1015", "Pune Sales Office", "Pune", "Maharashtra", 0.6),
]

# name, group, trade margin, other terms rate, promo rate, size, states
CHAINS = [
    ("Sample Chain A", "Modern Formats", 0.115, 0.020, 0.015, 1.30, ["Telangana", "Andhra Pradesh", "Karnataka", "Maharashtra"]),
    ("Sample Chain B", "Modern Formats", 0.125, 0.025, 0.020, 1.20, list(STATES)),
    ("Sample Chain C", "Modern Formats", 0.140, 0.022, 0.020, 0.80, ["Telangana", "Andhra Pradesh", "Karnataka", "Tamil Nadu"]),
    ("Sample Chain D", "Modern Formats", 0.150, 0.028, 0.025, 0.45, ["Telangana", "Andhra Pradesh", "Tamil Nadu"]),
    ("Sample Chain E", "E-Commerce", 0.090, 0.015, 0.010, 0.70, ["Telangana", "Karnataka", "Tamil Nadu", "Maharashtra"]),
    ("Sample Chain F", "E-Commerce", 0.135, 0.020, 0.018, 0.55, ["Telangana", "Andhra Pradesh"]),
]

# code, name, family, variant, pack, mrp, gst, target gross margin at a typical chain price, freight/pack, base units
SKUS = [
    ("50001", "Toned milk 500 ml", "Milk", "Toned", "500 ML", 27, 0.00, 0.12, 0.45, 9000),
    ("50002", "Full cream milk 500 ml", "Milk", "Full cream", "500 ML", 34, 0.00, 0.13, 0.45, 5500),
    ("50003", "Curd cup 400 g", "Curd", "Cup", "400 GMS", 40, 0.05, 0.18, 0.70, 3000),
    ("50004", "Curd tub 1 kg", "Curd", "Tub", "1 KG", 85, 0.05, 0.20, 1.40, 900),
    ("50005", "Lassi 200 ml", "Lassi", "Sweet", "200 ML", 20, 0.12, 0.25, 0.40, 2000),
    ("50006", "Buttermilk 200 ml", "ButterMilk", "Spiced", "200 ML", 15, 0.05, 0.24, 0.35, 2200),
    ("50007", "Milkshake 180 ml", "Milk Shakes", "Chocolate", "180 ML", 30, 0.12, 0.24, 0.45, 900),
    ("50008", "Paneer 200 g", "Paneer", "Plain", "200 GMS", 95, 0.05, 0.18, 1.20, 1100),
    ("50009", "Butter 100 g", "Butter", "Salted", "100 GMS", 58, 0.12, 0.17, 0.90, 700),
    ("50010", "Ghee 1 L carton", "Ghee", "Cow", "1 L", 640, 0.12, 0.12, 3.50, 450),
    ("50011", "Cheese slices 200 g", "Cheese", "Slices", "200 GMS", 135, 0.12, 0.19, 1.40, 280),
]


def main(seed=7):
    rng = random.Random(seed)
    rows = []
    for cname, grp, trade, other, promo, size, states in CHAINS:
        for code, oname, city, state, weight in OFFICES:
            if state not in states:
                continue
            for sku, name, family, variant, pack, mrp, gst, target_gm, freight, base in SKUS:
                if rng.random() < 0.08:  # chain does not stock every SKU everywhere
                    continue
                units = round(base * size * weight * rng.uniform(0.85, 1.15) * VOLUME_SCALE, 1)
                mrp_x = mrp / (1 + gst)
                cogs = round(mrp_x * (1 - 0.125) * (1 - 0.04) * (1 - target_gm), 5)  # same cost for every chain
                gross = mrp_x * (1 - trade)
                net_px = gross * (1 - promo - other)
                rows.append({
                    "chain": cname, "grp": grp, "state": state, "city": city, "so": code, "so_name": oname,
                    "sku": sku, "name": name, "family": family, "variant": variant, "pack": pack,
                    "units": units, "mrp_x": round(mrp_x, 5), "trade": trade, "promo_rate": promo,
                    "other_rate": other, "net_px": round(net_px, 5),
                    "cogs": cogs,
                    "sd": round(freight * STATES[state] + 0.035 * net_px, 5),   # per pack
                    "pa": 0.03,                                                  # share of net revenue
                    "interest": 0.005,                                           # share of net revenue
                    "ou": round(0.015 * net_px, 5),                              # per pack
                    "mktg": 0.003,                                               # share of net revenue
                })
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
