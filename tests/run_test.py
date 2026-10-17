import os
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
from datetime import date
import parsers, analysis as A
S = sys.argv[1]
files = []
for n in sorted(os.listdir(S)):
    r = parsers.parse_file(open(f"{S}/{n}", "rb").read(), n)
    print(n, r["format"], len(r["transactions"]), r["warnings"])
    files.append({"id": n, "result": r})
tx = A.merge_transactions(files, {})
print("merged", len(tx), "raw", sum(len(f["result"]["transactions"]) for f in files))
for a in A.account_list(files, {}): print("  ", a["label"], a["count"])
items = A.detect_recurring(tx, {})
for i in items: print(f"{i['include']!s:5} {i['name'][:30]:30} {i['cadence']:9} dom{i['dom']:>3} {i['amount']:>8} {i['confidence']:6} card={i['card_payment']} n={i['count']}")
pay = A.detect_pay(tx, {})
print(pay["name"], pay["amount"], pay["cadence"], pay["anchor"])
fc = A.forecast(items, pay, [], A.default_start(pay, date(2026, 10, 17)))
print(fc["start"], fc["end"], "bills", fc["total"], "pay", fc["pay_total"], "left", fc["left"])
for b in fc["bills"]: print("   ", b["date"], b["name"], b["amount"], b["status"])
