"""Loads the made-up store data from the data/ folder once."""
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"


def _rows(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


SKUS = {r["sku"]: {
    "sku": r["sku"], "name": r["name"], "category": r["category"],
    "unit_cost": float(r["unit_cost"]), "mrp": float(r["mrp"]),
    "shelf_life_days": int(r["shelf_life_days"]), "case_pack": int(r["case_pack"]),
    "shelf_capacity": int(r["shelf_capacity"])} for r in _rows("skus.csv")}

SUPPLIERS = {r["supplier_id"]: {
    "supplier_id": r["supplier_id"], "name": r["name"], "categories": r["categories"].split(";"),
    "msme": r["msme"] == "yes", "payment_days": int(r["payment_days"]),
    "lead_time_days": int(r["lead_time_days"]), "fill_rate": float(r["fill_rate"]),
    "on_time_rate": float(r["on_time_rate"]), "status": r["status"],
    "price_index": float(r["price_index"]), "gstin": r["gstin"]} for r in _rows("suppliers.csv")}

EVENTS = [{"event": r["event"], "start": date.fromisoformat(r["start"]),
           "end": date.fromisoformat(r["end"]), "categories": r["categories"].split(";"),
           "uplift": float(r["uplift"]), "keywords": r["keywords"].split(";")} for r in _rows("events.csv")]

STOCK = {r["sku"]: {"on_hand": int(r["on_hand"]), "on_order": int(r["on_order"]),
                    "usual_supplier": r["usual_supplier"]} for r in _rows("stock.csv")}

_SALES = defaultdict(list)
for r in _rows("sales_history.csv"):
    _SALES[r["sku"]].append((date.fromisoformat(r["date"]), int(r["units_sold"])))


def sales_for(sku):
    return sorted(_SALES.get(sku, []))


def load_cases():
    with open(DATA / "cases.json", encoding="utf-8") as f:
        return json.load(f)
