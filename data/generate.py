"""Deterministic synthetic warehouse for bi-copilot.

No real company data: every name, number and pattern is generated from a fixed seed.
Writes CSVs to data/csv/. Shapes mirror a B2B hardware + services business so the
semantic layer has something realistic to govern (fiscal year starting April, cancelled
orders that must be excluded from revenue, regional targets, customer hierarchy).
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42
OUT = Path(__file__).resolve().parent / "csv"
START = date(2024, 4, 1)  # FY2025 Q1
END = date(2026, 9, 30)  # FY2027 Q2
N_CUSTOMERS = 400
N_PRODUCTS = 60
N_REPS = 40
N_ORDERS = 24_000

REGIONS = {
    "APAC": ["Malaysia", "Singapore", "Japan", "Australia", "India", "Indonesia"],
    "EMEA": ["Germany", "United Kingdom", "France", "Netherlands", "UAE"],
    "NA": ["United States", "Canada"],
    "LATAM": ["Brazil", "Mexico", "Chile"],
}
SEGMENTS = [("Enterprise", 0.30), ("SMB", 0.40), ("Public Sector", 0.18), ("Education", 0.12)]
INDUSTRIES = ["Manufacturing", "Financial Services", "Healthcare", "Retail", "Technology", "Government", "Logistics"]
TIERS = [("Gold", 0.15), ("Silver", 0.35), ("Bronze", 0.50)]
CHANNELS = [("direct", 0.45), ("partner", 0.40), ("online", 0.15)]
CATEGORIES = {
    "Laptop": (("ThinkBook-like", "UltraSlim", "Workstation Mobile"), 700, 2800, 0.72),
    "Desktop": (("Tower", "Tiny", "All-in-One"), 500, 2200, 0.74),
    "Server": (("Rack 1U", "Rack 2U", "Tower Server"), 3000, 14000, 0.80),
    "Service": (("Premier Support", "Deployment", "Managed Services"), 150, 2500, 0.45),
    "Accessory": (("Docking", "Monitor", "Peripheral"), 40, 600, 0.65),
}
CATEGORY_WEIGHTS = {"Laptop": 0.42, "Desktop": 0.15, "Server": 0.08, "Service": 0.15, "Accessory": 0.20}
WORDS_A = ["Northern", "Pacific", "Summit", "Harbor", "Meridian", "Apex", "Delta", "Cedar", "Orion", "Lumen", "Vertex"]
WORDS_B = ["Logistics", "Holdings", "Health", "Systems", "Retail", "Bank", "University", "Council", "Dynamics", "Works"]
FIRST = [
    "Aisha",
    "Wei",
    "Priya",
    "Lucas",
    "Mei",
    "Omar",
    "Sofia",
    "Hiro",
    "Anna",
    "Daniel",
    "Fatimah",
    "Jonas",
    "Chloe",
]
LAST = ["Tan", "Lim", "Schmidt", "Garcia", "Nair", "Kobayashi", "Okafor", "Novak", "Silva", "Wong", "Dubois", "Ahmed"]


def weighted(rng: random.Random, pairs):
    names, weights = zip(*pairs)
    return rng.choices(names, weights=weights, k=1)[0]


def fiscal(d: date) -> tuple[int, int]:
    fy = d.year + 1 if d.month >= 4 else d.year
    fq = ((d.month - 4) % 12) // 3 + 1
    return fy, fq


def build_dates():
    rows = []
    d = START
    while d <= END:
        fy, fq = fiscal(d)
        rows.append(
            {
                "date_key": d.isoformat(),
                "year": d.year,
                "quarter": (d.month - 1) // 3 + 1,
                "month": d.month,
                "month_name": d.strftime("%b"),
                "year_month": d.strftime("%Y-%m"),
                "fiscal_year": fy,
                "fiscal_quarter": fq,
                "fiscal_period": f"FY{str(fy)[2:]}-Q{fq}",
            }
        )
        d += timedelta(days=1)
    return rows


def build_customers(rng: random.Random):
    rows = []
    for i in range(1, N_CUSTOMERS + 1):
        region = weighted(rng, [("APAC", 0.40), ("EMEA", 0.28), ("NA", 0.22), ("LATAM", 0.10)])
        onboarded = START - timedelta(days=rng.randint(0, 1500))
        rows.append(
            {
                "customer_id": i,
                "customer_name": f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)} {i:03d}",
                "segment": weighted(rng, SEGMENTS),
                "industry": rng.choice(INDUSTRIES),
                "country": rng.choice(REGIONS[region]),
                "region": region,
                "tier": weighted(rng, TIERS),
                "onboarded_date": onboarded.isoformat(),
                "is_active": 0 if rng.random() < 0.08 else 1,
            }
        )
    return rows


def build_products(rng: random.Random):
    rows = []
    pid = 1
    for category, (families, lo, hi, margin) in CATEGORIES.items():
        per_cat = max(4, round(N_PRODUCTS * CATEGORY_WEIGHTS[category]))
        for k in range(per_cat):
            family = families[k % len(families)]
            price = round(rng.uniform(lo, hi), 2)
            rows.append(
                {
                    "product_id": pid,
                    "product_name": f"{family} {category[:3].upper()}-{pid:03d}",
                    "category": category,
                    "family": family,
                    "list_price": price,
                    "unit_cost": round(price * (1 - margin) * rng.uniform(0.9, 1.1), 2),
                }
            )
            pid += 1
    return rows


def build_reps(rng: random.Random):
    rows = []
    for i in range(1, N_REPS + 1):
        region = weighted(rng, [("APAC", 0.40), ("EMEA", 0.28), ("NA", 0.22), ("LATAM", 0.10)])
        rows.append(
            {
                "sales_rep_id": i,
                "rep_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                "region": region,
                "team": f"{region} {'Enterprise' if i % 3 == 0 else 'Commercial'}",
            }
        )
    return rows


def build_orders(rng: random.Random, customers, products, reps, dates):
    by_region_reps = {}
    for r in reps:
        by_region_reps.setdefault(r["region"], []).append(r["sales_rep_id"])
    prod_by_cat = {}
    for p in products:
        prod_by_cat.setdefault(p["category"], []).append(p)
    date_keys = [d["date_key"] for d in dates]
    n_days = len(date_keys)
    rows = []
    for oid in range(1, N_ORDERS + 1):
        # mild growth + fiscal-Q4 (Jan-Mar) push + seasonal dip in Aug
        while True:
            idx = int(rng.triangular(0, n_days - 1, n_days * 0.65))
            d = date.fromisoformat(date_keys[idx])
            boost = 1.25 if d.month in (2, 3) else (0.8 if d.month == 8 else 1.0)
            if rng.random() < boost / 1.25:
                break
        cust = rng.choice(customers)
        category = weighted(rng, list(CATEGORY_WEIGHTS.items()))
        prod = rng.choice(prod_by_cat[category])
        qty_hi = {"Enterprise": 60, "Public Sector": 40, "Education": 50, "SMB": 12}[cust["segment"]]
        qty = max(1, int(rng.lognormvariate(1.2, 0.8)))
        qty = min(qty, qty_hi)
        discount = round(rng.choice([0, 0, 5, 10, 12.5, 15, 20, 25]) + (5 if cust["tier"] == "Gold" else 0), 2)
        unit_price = round(prod["list_price"] * (1 - discount / 100), 2)
        status = weighted(rng, [("completed", 0.90), ("cancelled", 0.07), ("returned", 0.03)])
        rows.append(
            {
                "order_id": oid,
                "order_date": d.isoformat(),
                "customer_id": cust["customer_id"],
                "product_id": prod["product_id"],
                "sales_rep_id": rng.choice(by_region_reps[cust["region"]]),
                "channel": weighted(rng, CHANNELS),
                "status": status,
                "quantity": qty,
                "unit_price": unit_price,
                "discount_pct": discount,
                "net_amount": round(qty * unit_price, 2),
                "cost_amount": round(qty * prod["unit_cost"], 2),
            }
        )
    rows.sort(key=lambda r: (r["order_date"], r["order_id"]))
    return rows


def build_targets(rng: random.Random, orders, dates):
    """Targets = last year's actual * 1.1 per region/quarter (with noise); first year gets a flat base."""
    fiscal_of = {d["date_key"]: (d["fiscal_year"], d["fiscal_quarter"]) for d in dates}
    actual: dict[tuple[str, int, int], float] = {}
    region_of = {}
    for o in orders:  # orders carry _region (looked up from dim_customer by the caller)
        if o["status"] != "completed":
            continue
        fy, fq = fiscal_of[o["order_date"]]
        key = (o["_region"], fy, fq)
        actual[key] = actual.get(key, 0.0) + o["net_amount"]
        region_of[o["_region"]] = True
    rows = []
    periods = sorted({v for v in fiscal_of.values()})
    for region in sorted(region_of):
        for fy, fq in periods:
            prev = actual.get((region, fy - 1, fq))
            base = prev * 1.10 if prev else actual.get((region, fy, fq), 1_000_000.0) * 0.95
            rows.append(
                {
                    "region": region,
                    "fiscal_year": fy,
                    "fiscal_quarter": fq,
                    "target_amount": round(base * rng.uniform(0.95, 1.05), 2),
                }
            )
    return rows


def write(name: str, rows: list[dict]):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in rows[0] if not k.startswith("_")])
        w.writeheader()
        for r in rows:
            w.writerow({k: v for k, v in r.items() if not k.startswith("_")})


def main():
    rng = random.Random(SEED)
    dates = build_dates()
    customers = build_customers(rng)
    products = build_products(rng)
    reps = build_reps(rng)
    orders = build_orders(rng, customers, products, reps, dates)
    region_by_customer = {c["customer_id"]: c["region"] for c in customers}
    for o in orders:
        o["_region"] = region_by_customer[o["customer_id"]]
    targets = build_targets(rng, orders, dates)
    write("dim_date", dates)
    write("dim_customer", customers)
    write("dim_product", products)
    write("dim_sales_rep", reps)
    write("fact_orders", orders)
    write("fact_targets", targets)
    print(f"wrote {len(orders)} orders, {len(customers)} customers, {len(products)} products -> {OUT}")


if __name__ == "__main__":
    main()
