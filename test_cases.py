"""Runs the 10 sample cases and checks the result of each one.

What is checked for every SKU line: supplier, priority (P1-P4) and the approval label
(AUTO = runs on its own, MANAGER = needs a human, NEVER = blocked, NO_ORDER).
Also checks that trick text was caught. Manager lines are approved automatically here.

Run:  python test_cases.py            (uses AI if a key is set)
      python test_cases.py --backup   (backup mode only)
      python test_cases.py --wait 60  (wait 60 s between cases for the free Groq limit)
"""
import os
import sys
import time

if "--backup" in sys.argv:
    os.environ["FORCE_BACKUP"] = "1"

import workflow                      # noqa: E402
from llm import mode_label           # noqa: E402

# case: ({sku-kind: (supplier, priority, label)}, injection expected?, send-back expected?)
EXPECTED = {
    "C01": ({"SKU-101-policy": ("SUP-01", "P1", "AUTO"), "SKU-102-policy": ("SUP-01", "P4", "NO_ORDER")}, False, False),
    "C02": ({"SKU-201-policy": ("SUP-03", "P2", "MANAGER"), "SKU-203-policy": ("SUP-04", "P4", "NO_ORDER")}, False, True),
    "C03": ({"SKU-601-policy": ("SUP-10", "P1", "MANAGER")}, False, False),
    "C04": ({"SKU-103-policy": ("SUP-01", "P1", "AUTO")}, False, False),
    "C05": ({"SKU-501-policy": ("SUP-08", "P4", "NO_ORDER")}, False, False),
    "C06": ({"SKU-104-policy": ("SUP-07", "P2", "AUTO"), "SKU-105-policy": ("SUP-01", "P2", "AUTO")}, True, False),
    "C07": ({"SKU-401-requested": ("SUP-09", "P2", "NEVER"), "SKU-401-replacement": ("SUP-05", "P2", "AUTO")}, True, False),
    "C08": ({"SKU-402-requested": ("SUP-05", "P1", "NEVER"), "SKU-402-replacement": ("SUP-05", "P1", "AUTO")}, False, False),
    "C09": ({"SKU-401-policy": ("SUP-05", "P1", "AUTO"), "SKU-402-policy": ("SUP-05", "P1", "AUTO"),
             "SKU-403-policy": ("SUP-05", "P3", "AUTO")}, False, False),
    "C10": ({"SKU-203-requested": ("SUP-11", "P1", "NEVER"), "SKU-203-replacement": ("SUP-04", "P2", "AUTO")}, False, False),
}


def check(case_id):
    tid, st, pause = workflow.start(case_id)
    if pause:
        st = workflow.resume(tid, {l["line_id"]: "approve" for l in pause.value["lines"]})
    want, inj, sendback = EXPECTED[case_id]
    got = {ln["line_id"].split("-", 1)[1]: (ln["supplier_id"], ln["priority"], ln["label"]) for ln in st["lines"]}
    problems = []
    for k, v in want.items():
        if got.get(k) != v:
            problems.append(f"{k}: expected {v}, got {got.get(k)}")
    if inj and not st.get("injections"):
        problems.append("trick text not caught")
    did_send_back = st.get("attempts", 1) > 1
    if sendback != did_send_back:
        problems.append(f"send back expected {sendback}, got {did_send_back}")
    return st, problems


def main():
    wait = int(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 0
    print(f"RestockPilot test run - {mode_label()}\n")
    print(f"{'Case':5} {'Result':6}  {'Lines (supplier, priority, label)':70} Notes")
    passed = 0
    for i, cid in enumerate(EXPECTED):
        if i and wait:
            time.sleep(wait)
        st, problems = check(cid)
        lines = "; ".join(f"{ln['sku']} {ln['supplier_id']} {ln['priority']} {ln['label']}" for ln in st["lines"])
        notes = []
        if st.get("injections"):
            notes.append(f"trick text caught x{len(st['injections'])}")
        if st.get("attempts", 1) > 1:
            notes.append("sent back once")
        ok = not problems
        passed += ok
        print(f"{cid:5} {'PASS' if ok else 'FAIL':6}  {lines[:70]:70} {', '.join(notes)}")
        for p in problems:
            print(f"      -> {p}")
    print(f"\n{passed}/{len(EXPECTED)} cases passed")


if __name__ == "__main__":
    main()
