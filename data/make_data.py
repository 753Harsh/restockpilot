"""Makes the fake data for the ZipBasket Saket dark store.

All numbers are made up. Run once:  python data/make_data.py
A fixed random seed means the same files come out every time.
"""
import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
random.seed(7)
TODAY = date(2026, 10, 12)          # the "run date" used in every sample case

# sku, name, category, unit cost, MRP, shelf life (days), case pack, shelf space (units), base daily sales, weekend lift
SKUS = [
    ("SKU-101", "Toned Milk 500 ml", "Dairy", 27, 29, 3, 24, 600, 210, 1.10),
    ("SKU-102", "Curd 400 g", "Dairy", 38, 45, 10, 12, 300, 70, 1.15),
    ("SKU-103", "Paneer 200 g", "Dairy", 78, 95, 12, 10, 200, 45, 1.30),
    ("SKU-104", "Brown Bread 400 g", "Bakery", 40, 50, 5, 10, 150, 38, 1.10),
    ("SKU-105", "Eggs (pack of 6)", "Dairy", 42, 54, 14, 12, 250, 55, 1.20),
    ("SKU-201", "Onion 1 kg", "Fruits & Veg", 32, 45, 20, 10, 500, 140, 1.10),
    ("SKU-202", "Tomato 1 kg", "Fruits & Veg", 26, 40, 6, 10, 300, 110, 1.10),
    ("SKU-203", "Potato 1 kg", "Fruits & Veg", 22, 32, 30, 10, 500, 120, 1.05),
    ("SKU-204", "Banana (6 pcs)", "Fruits & Veg", 30, 45, 4, 12, 200, 60, 1.10),
    ("SKU-301", "Whole Wheat Atta 5 kg", "Staples", 205, 260, 90, 4, 120, 22, 1.20),
    ("SKU-302", "Toor Dal 1 kg", "Staples", 148, 180, 180, 10, 200, 28, 1.10),
    ("SKU-303", "Sunflower Oil 1 L", "Staples", 128, 160, 270, 12, 200, 30, 1.10),
    ("SKU-304", "Sugar 1 kg", "Staples", 42, 52, 365, 10, 200, 35, 1.10),
    ("SKU-401", "Cola 750 ml", "Beverages", 30, 40, 180, 24, 400, 60, 1.40),
    ("SKU-402", "Potato Chips 52 g", "Snacks", 15, 20, 120, 48, 600, 90, 1.45),
    ("SKU-403", "Instant Noodles 4-pack", "Snacks", 48, 60, 240, 24, 300, 40, 1.25),
    ("SKU-501", "Vanilla Ice Cream 700 ml", "Frozen", 120, 199, 180, 6, 80, 9, 1.30),
    ("SKU-601", "Dry Fruit Gift Box 500 g", "Festive", 420, 699, 180, 6, 150, 4, 1.10),
]

# supplier id, name, categories, MSME?, payment days, lead time days, fill rate, on-time rate, status, price index, GSTIN (fake)
SUPPLIERS = [
    ("SUP-01", "Yamuna Dairy Co-op", "Dairy", "yes", 30, 1, 0.97, 0.95, "approved", 1.00, "07AABCY1234F1Z5"),
    ("SUP-02", "Shree Ganesh Dairy", "Dairy", "yes", 30, 1, 0.92, 0.90, "approved", 0.97, "09AAFCS5521K1Z2"),
    ("SUP-03", "Azadpur Fresh Traders", "Fruits & Veg", "yes", 15, 1, 0.90, 0.88, "approved", 1.00, "07AAKFA8812L1Z9"),
    ("SUP-04", "Kisan Direct FPO", "Fruits & Veg", "yes", 7, 2, 0.85, 0.80, "approved", 0.90, "06AABAK3301M1Z1"),
    ("SUP-05", "Metro Staples Distributors", "Staples;Snacks;Beverages", "no", 45, 2, 0.96, 0.94, "approved", 1.00, "07AACCM7745N1Z4"),
    ("SUP-06", "Delhi FMCG Hub", "Staples;Snacks;Beverages;Frozen", "no", 30, 3, 0.93, 0.91, "approved", 0.98, "07AADCD2290P1Z7"),
    ("SUP-07", "Golden Harvest Bakers", "Bakery", "yes", 15, 1, 0.95, 0.93, "approved", 1.00, "07AAGFG4410Q1Z3"),
    ("SUP-08", "Chill Chain Frozen Foods", "Frozen", "no", 30, 2, 0.94, 0.92, "approved", 1.00, "06AAICC6651R1Z8"),
    ("SUP-09", "QuickProfit Wholesale", "Staples;Snacks;Beverages;Dairy", "no", 0, 2, 0.60, 0.55, "blocked", 0.80, "07AAQCQ0001X1Z0"),
    ("SUP-10", "Festive Treats India", "Festive", "yes", 45, 4, 0.94, 0.90, "approved", 1.00, "08AAFCF3321S1Z6"),
    ("SUP-11", "NewLeaf Agro Ventures", "Fruits & Veg;Staples", "yes", 90, 3, 0.0, 0.0, "new", 0.80, "07AANCN9988T1Z2"),
]

# events that change demand (name, start, end, categories, uplift, news keywords)
# the keywords let code spot a news story about the same event, so it is not counted twice
EVENTS = [
    ("Navratri fasting week", "2026-10-11", "2026-10-19", "Dairy;Fruits & Veg", 1.15, "navratri;fasting"),
    ("India vs Australia cricket final", "2026-10-15", "2026-10-15", "Snacks;Beverages", 1.80, "cricket;match;final"),
    ("Dussehra holiday", "2026-10-20", "2026-10-20", "Snacks;Beverages;Festive", 1.30, "dussehra"),
    ("Diwali gifting season", "2026-10-25", "2026-11-08", "Festive;Snacks;Staples", 2.50, "diwali;gifting"),
    ("Early winter (cooler days)", "2026-10-10", "2026-11-30", "Frozen", 0.60, "winter;temperatures;cooler"),
]


def write_csv(name, header, rows):
    with open(HERE / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    write_csv("skus.csv",
              ["sku", "name", "category", "unit_cost", "mrp", "shelf_life_days",
               "case_pack", "shelf_capacity"],
              [s[:8] for s in SKUS])

    write_csv("suppliers.csv",
              ["supplier_id", "name", "categories", "msme", "payment_days", "lead_time_days",
               "fill_rate", "on_time_rate", "status", "price_index", "gstin"],
              SUPPLIERS)

    write_csv("events.csv", ["event", "start", "end", "categories", "uplift", "keywords"], EVENTS)

    # 28 days of sales up to yesterday
    rows = []
    for s in SKUS:
        sku, base, wk = s[0], s[8], s[9]
        for d in range(28, 0, -1):
            day = TODAY - timedelta(days=d)
            mult = wk if day.weekday() >= 5 else 1.0
            trend = 1.0
            if sku == "SKU-501":            # ice cream falling as winter starts
                trend = 1.6 - 0.025 * (28 - d)
            if sku == "SKU-201":            # onion demand slowly rising (people stock up)
                trend = 0.9 + 0.008 * (28 - d)
            if sku == "SKU-601":            # gift boxes picking up before Diwali
                trend = 0.5 + 0.06 * (28 - d)
            qty = max(0, round(random.gauss(base * mult * trend, base * 0.12)))
            rows.append((sku, day.isoformat(), qty))
    write_csv("sales_history.csv", ["sku", "date", "units_sold"], rows)

    # stock on hand today, stock already ordered, and the usual supplier
    stock = {
        "SKU-101": (180, 0, "SUP-01"), "SKU-102": (150, 0, "SUP-01"),
        "SKU-103": (40, 0, "SUP-02"), "SKU-104": (45, 0, "SUP-07"),
        "SKU-105": (80, 0, "SUP-01"), "SKU-201": (210, 0, "SUP-04"),
        "SKU-202": (60, 100, "SUP-03"), "SKU-203": (420, 0, "SUP-04"),
        "SKU-204": (70, 0, "SUP-03"), "SKU-301": (60, 0, "SUP-05"),
        "SKU-302": (150, 0, "SUP-05"), "SKU-303": (110, 0, "SUP-05"),
        "SKU-304": (160, 0, "SUP-05"), "SKU-401": (150, 0, "SUP-05"),
        "SKU-402": (220, 0, "SUP-05"), "SKU-403": (190, 0, "SUP-05"),
        "SKU-501": (70, 0, "SUP-08"), "SKU-601": (12, 0, "SUP-10"),
    }
    write_csv("stock.csv", ["sku", "on_hand", "on_order", "usual_supplier"],
              [(k, *v) for k, v in stock.items()])
    print("Data written to", HERE)


if __name__ == "__main__":
    main()
