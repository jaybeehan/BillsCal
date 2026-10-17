"""Find recurring bills and pay in a pile of transactions, and project them forward."""

import calendar
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta

D = date.fromisoformat

# ----------------------------------------------------------------- accounts
def account_id(t):
    return f"{t['bank']}-{t['kind']}-{t['last4'] or 'x'}"


def merge_transactions(parsed_files, settings):
    """Combine every file's rows into one list with no double counting.

    Statements overlap (a CSV covering 90 days, then the PDF for one month...).
    For each account, a (date, amount) pair is kept as many times as the file
    with the most copies of it has — so re-uploading or overlapping files adds
    nothing, but two genuine $5 coffees on the same day in one file both stay.
    """
    acct_settings = settings.get("accounts", {})
    per_file = []
    for f in parsed_files:
        rows = []
        for t in f["result"]["transactions"]:
            rows.append(dict(t, account=account_id(t), file=f["id"]))
        per_file.append(rows)

    # An account read without its last 4 digits joins the one with the same bank+kind.
    known = defaultdict(set)
    for rows in per_file:
        for t in rows:
            if t["last4"]:
                known[(t["bank"], t["kind"])].add(t["account"])
    for rows in per_file:
        for t in rows:
            if not t["last4"] and len(known[(t["bank"], t["kind"])]) == 1:
                t["account"] = next(iter(known[(t["bank"], t["kind"])]))

    best = {}  # (account, date, amount) -> list of rows from the file with most copies
    for rows in per_file:
        groups = defaultdict(list)
        for t in rows:
            groups[(t["account"], t["date"], t["amount"])].append(t)
        for k, v in groups.items():
            if len(v) > len(best.get(k, [])):
                best[k] = v

    out = []
    for rows in best.values():
        for t in rows:
            a = acct_settings.get(t["account"], {})
            if a.get("ignore"):
                continue
            t = dict(t)
            if a.get("flip"):
                t["amount"] = -t["amount"]
            out.append(t)
    out.sort(key=lambda t: (t["date"], t["desc"]))
    return out


def account_list(parsed_files, settings):
    stats = {}
    for f in parsed_files:
        for t in f["result"]["transactions"]:
            aid = account_id(t)
            s = stats.setdefault(aid, {"id": aid, "bank": t["bank"], "kind": t["kind"], "last4": t["last4"],
                                       "count": 0, "first": t["date"], "last": t["date"]})
            s["count"] += 1
            s["first"] = min(s["first"], t["date"])
            s["last"] = max(s["last"], t["date"])
    for s in stats.values():
        a = settings.get("accounts", {}).get(s["id"], {})
        kind = "Card" if s["kind"] == "credit" else "Chequing"
        s["label"] = a.get("label") or f"{s['bank']} {kind}" + (f" ••{s['last4']}" if s["last4"] else "")
        s["flip"] = bool(a.get("flip"))
        s["ignore"] = bool(a.get("ignore"))
    return sorted(stats.values(), key=lambda s: s["label"])


# ------------------------------------------------------------- descriptions
NOISE = set("""pos purchase purchases interac debit card online banking bill bills payment payments pmt pymt pre
authorized preauthorized preauth auth pad misc memo dn pr cw ap ip rp fpos opos apos withdrawal deposit www com
ca net org inc ltd corp co the of to from and ab bc on sk mb qc ns nb nl pe yt nt nu can canada recurring
contactless debit tap web""".split())

INTERNAL = re.compile(r"\b(online (banking )?transfer|transfer (to|from)|funds transfer|tfr|trsf|trf (to|from)|"
                      r"(to|from) (deposit|savings|chequing) account|between accounts|savings transfer)\b", re.I)
NOT_INTERNAL = re.compile(r"e-?\s?transfer|e-?trf|interac|email money|autodeposit", re.I)
CARD_WORDS = re.compile(r"visa|master ?card|\bmc\b|amex|american express|credit card|\bcr card\b", re.I)
PAY_WORDS = re.compile(r"payroll|pay ?roll|salary|\bpay\b|dir(ect)? dep|wages|employer", re.I)


def merchant_key(desc):
    toks = []
    for raw in str(desc).lower().split():
        if re.search(r"\d", raw):
            continue
        toks += [t for t in re.split(r"[^a-z]+", raw) if len(t) >= 2 and t not in NOISE]
    key = " ".join(toks[:3])
    return key or re.sub(r"\d+", "", str(desc).lower()).strip()[:30] or "unknown"


def is_internal(t):
    return bool(INTERNAL.search(t["desc"])) and not NOT_INTERNAL.search(t["desc"])


def is_card_payment(t):
    """Money leaving a chequing account to pay a credit card."""
    return t["kind"] == "bank" and t["amount"] < 0 and bool(CARD_WORDS.search(t["desc"])) \
        and bool(re.search(r"pay|pmt|pymt", t["desc"], re.I))


# ----------------------------------------------------------------- cadences
# name, min gap, max gap, nominal days, month step (None = fixed day count), min occurrences
CADENCES = [
    ("weekly", 6, 8, 7, None, 4),
    ("biweekly", 12, 16, 14, None, 3),
    ("monthly", 26, 35, 30.44, 1, 2),
    ("every 2 months", 55, 67, 61, 2, 2),
    ("quarterly", 82, 100, 91, 3, 2),
    ("twice a year", 168, 196, 182, 6, 2),
    ("yearly", 345, 385, 365, 12, 2),
]
LABEL = {c[0]: c[0] for c in CADENCES}


def fit_cadence(dates):
    days = sorted(set(dates))
    if len(days) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    med = statistics.median(gaps)
    for name, lo, hi, nominal, step, min_n in CADENCES:
        if len(days) < min_n or not (lo <= med <= hi):
            continue
        # one skipped period (e.g. a month with no charge) is still "on schedule"
        ok = sum(lo <= g <= hi or (step in (None, 1) and 2 * lo <= g <= 2 * hi) for g in gaps)
        if ok / len(gaps) >= 0.7:
            return {"cadence": name, "nominal": nominal, "step": step, "gap": med}
    return None


def amount_clusters(rows):
    rows = sorted(rows, key=lambda t: abs(t["amount"]))
    clusters, cur = [], []
    for t in rows:
        if cur and abs(t["amount"]) > abs(cur[-1]["amount"]) * 1.15 + 1:
            clusters.append(cur)
            cur = []
        cur.append(t)
    if cur:
        clusters.append(cur)
    return clusters


def round2sig(x):
    x = abs(x)
    if x == 0:
        return 0
    from math import floor, log10
    p = floor(log10(x)) - 1
    return round(x, -p)


def summarize(key, rows, fit, data_end_by_acct, split):
    rows = sorted(rows, key=lambda t: t["date"])
    amts = [abs(t["amount"]) for t in rows]
    recent = amts[-6:]
    if len(recent) >= 3 and max(recent[-3:]) - min(recent[-3:]) <= 0.02 * max(recent[-3:]):
        amount = recent[-1]
    elif len(recent) >= 3:
        amount = statistics.median(recent[-4:])
    else:
        amount = recent[-1]
    spread = (max(recent) - min(recent)) / max(max(recent), 0.01)
    last = rows[-1]
    dom_list = [D(t["date"]).day for t in rows]
    dom = 31 if min(dom_list) >= 28 else int(statistics.median(dom_list))
    acct_end = D(data_end_by_acct.get(last["account"], last["date"]))
    gap = fit["nominal"]
    ended = (acct_end - D(last["date"])).days > gap * 1.6 + 3
    n = len(set(t["date"] for t in rows))
    if spread > 0.45:
        confidence = "low"  # amount all over the place: shopping, not a bill
    elif n >= (5 if fit["step"] is None else 3) and spread < 0.25:
        confidence = "high"
    elif n >= 3 or spread < 0.05:
        confidence = "medium"
    else:
        confidence = "low"
    card_payment = all(is_card_payment(t) for t in rows[-3:])
    year = [abs(t["amount"]) for t in rows if (D(last["date"]) - D(t["date"])).days < 365]
    avg12 = sum(year) / len(year)
    avg_months = min(12, round((D(last["date"]) - D(rows[0]["date"])).days / 30.44) + 1)
    bid = key + (f"|{round2sig(statistics.median(amts)):g}" if split else "")
    default = not ended and not card_payment and (confidence != "low" or spread < 0.05)
    if fit["cadence"] == "weekly" and spread > 0.3:
        default = False  # weekly + varying amount is usually shopping, not a bill
    return {
        "id": bid, "key": key, "name": key.title() + (f" ({amount:.2f})" if split else ""), "cadence": fit["cadence"], "step": fit["step"],
        "nominal": gap, "dom": dom, "amount": round(amount, 2), "min": round(min(recent), 2),
        "max": round(max(recent), 2), "varies": spread >= 0.05, "count": len(rows),
        "avg12": round(avg12, 2), "avg_months": avg_months,
        "latest": round(amts[-1], 2), "latest_date": last["date"],
        "first": rows[0]["date"], "last": last["date"], "account": last["account"],
        "example": last["desc"], "confidence": confidence, "ended": ended,
        "card_payment": card_payment, "default_include": bool(default),
        "history": [{"date": t["date"], "amount": abs(t["amount"]), "account": t["account"]} for t in rows],
    }


def detect_recurring(txns, settings, sheet_values=None):
    data_end = {}
    for t in txns:
        data_end[t["account"]] = max(data_end.get(t["account"], ""), t["date"])
    groups = defaultdict(list)
    for t in txns:
        if t["amount"] < 0 and not is_internal(t):
            groups[merchant_key(t["desc"])].append(t)

    items = []
    for key, rows in groups.items():
        whole = fit_cadence([D(t["date"]) for t in rows])
        cl_fits = []
        clusters = amount_clusters(rows)
        if len(clusters) > 1:
            for c in clusters:
                f = fit_cadence([D(t["date"]) for t in c])
                if f:
                    cl_fits.append((c, f))
        # Split by amount only when the amounts run side by side (two Apple
        # subscriptions), not when one bill is simply bigger in winter.
        if len(cl_fits) >= 2:
            newest = max(t["date"] for t in rows)
            if not all((D(newest) - D(max(t["date"] for t in c))).days <= 45 for c, _ in cl_fits):
                cl_fits = [] if whole else cl_fits[:1]
        if len(cl_fits) >= 2 or (cl_fits and not whole):
            for c, f in cl_fits:
                items.append(summarize(key, c, f, data_end, split=True))
        elif whole:
            items.append(summarize(key, rows, whole, data_end, split=False))

    overrides = settings.get("bills", {})
    for it in items:
        o = overrides.get(it["id"], {})
        it["include"] = o.get("include", it["default_include"])
        if o.get("name"):
            it["name"] = o["name"]
        if o.get("amount") not in (None, ""):
            it["amount_override"] = float(o["amount"])
        choose_amount(it, o.get("source", "auto"), sheet_values or {})
    items.sort(key=lambda i: (not i["include"], i["dom"] if i["step"] else 0, i["name"]))
    return items


def choose_amount(it, source, sheet_values):
    """Which amount to plan for. Your own number wins; then a linked sheet value;
    then, for bills that vary (utilities), the average of the last 12 months."""
    it["source"] = source
    if "amount_override" in it:
        it["use_amount"], it["amount_from"] = it["amount_override"], "your amount"
        return
    if source.startswith("sheet:"):
        names = [n for n in source[6:].split("+") if n]
        vals = [sheet_values.get(n) for n in names]
        if names and all(isinstance(v, (int, float)) for v in vals):
            it["use_amount"] = round(sum(vals), 2)
            it["amount_from"] = "12-mo average from sheet (" + " + ".join(names) + ")"
            return
        it["sheet_missing"] = True  # sheet not reachable: fall back below
    if source == "avg12" or (source in ("auto",) or source.startswith("sheet:")) and it["varies"]:
        it["use_amount"] = it["avg12"]
        m = it["avg_months"]
        it["amount_from"] = f"average of last {m} month{'s' if m != 1 else ''}"
        return
    it["use_amount"], it["amount_from"] = it["latest"], "latest amount"


# ---------------------------------------------------------------------- pay
def detect_pay(txns, settings):
    groups = defaultdict(list)
    for t in txns:
        if t["amount"] > 0 and t["kind"] == "bank" and not is_internal(t):
            groups[merchant_key(t["desc"])].append(t)
    cands = []
    for key, rows in groups.items():
        f = fit_cadence([D(t["date"]) for t in rows])
        if not f or f["cadence"] not in ("weekly", "biweekly", "monthly"):
            continue
        rows = sorted(rows, key=lambda t: t["date"])
        recent = [t["amount"] for t in rows[-6:]]
        score = sum(recent) * (2 if PAY_WORDS.search(rows[-1]["desc"]) else 1) * (1.5 if f["cadence"] == "biweekly" else 1)
        cands.append((score, key, rows, f))
    cands.sort(key=lambda c: -c[0])

    others = [{"name": k.title(), "cadence": f["cadence"], "amount": round(statistics.median([t["amount"] for t in r[-4:]]), 2),
               "last": r[-1]["date"]} for _, k, r, f in cands[1:6]]

    p = settings.get("pay", {})
    out = {"detected": False, "others": others}
    if cands:
        _, key, rows, f = cands[0]
        recent = [t["amount"] for t in rows[-6:]]
        last3 = recent[-3:]
        if len(last3) >= 3 and max(last3) - min(last3) <= 0.01 * max(last3):
            guess = last3[-1]
        else:
            guess = statistics.median(recent[-4:])
        out.update({
            "detected": True, "name": key.title(), "example": rows[-1]["desc"], "cadence": f["cadence"],
            "every_days": 14 if f["cadence"] == "biweekly" else 7 if f["cadence"] == "weekly" else None,
            "amount": round(guess, 2), "min": round(min(recent), 2), "max": round(max(recent), 2),
            "last": rows[-1]["date"], "count": len(rows),
            "history": [{"date": t["date"], "amount": t["amount"]} for t in rows[-12:]],
        })
    if p.get("anchor"):
        out["anchor_override"] = p["anchor"]
    if p.get("amount") not in (None, ""):
        out["amount_override"] = float(p["amount"])
    out["use_amount"] = out.get("amount_override", out.get("amount"))
    out["anchor"] = p.get("anchor") or out.get("last")
    out["every"] = int(p.get("every") or out.get("every_days") or 14)
    return out


def paydays_between(pay, start, end):
    if not pay.get("anchor"):
        return []
    a, step = D(pay["anchor"]), pay["every"]
    k = (start - a).days // step
    d = a + timedelta(days=k * step)
    out = []
    while d <= end:
        if d >= start:
            out.append(d)
        d += timedelta(days=step)
    return out


def default_start(pay, today):
    if pay.get("anchor"):
        a, step = D(pay["anchor"]), pay["every"]
        return a + timedelta(days=((today - a).days // step) * step)
    return today


# ----------------------------------------------------------------- forecast
def add_months(d, n, dom):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(dom, calendar.monthrange(y, m)[1]))


def project(item, start, end):
    """Expected dates for an item in [start, end], after its last posted date."""
    last = D(item["last"])
    out = []
    if item["step"]:
        k = 1
        while True:
            d = add_months(date(last.year, last.month, 1), k * item["step"], item["dom"])
            if d > end:
                break
            if d >= start and d > last:
                out.append(d)
            k += 1
            if k > 400:
                break
    else:
        step = int(round(item["nominal"]))
        d = last + timedelta(days=step)
        while d <= end:
            if d >= start:
                out.append(d)
            d += timedelta(days=step)
    return out


def project_manual(m, start, end):
    a = D(m["anchor"])
    cad = m.get("cadence", "monthly")
    out = []
    if cad in ("weekly", "biweekly"):
        step = 7 if cad == "weekly" else 14
        d = a + timedelta(days=((start - a).days // step) * step)
        while d <= end:
            if d >= start:
                out.append(d)
            d += timedelta(days=step)
    else:
        months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(cad, 1)
        base = date(a.year, a.month, 1)
        k = ((start.year - a.year) * 12 + start.month - a.month) // months - 1
        for i in range(k, k + 4):
            d = add_months(base, i * months, a.day)
            if start <= d <= end and d >= a:
                out.append(d)
    return out


def forecast(items, pay, manual, start, days=14):
    end = start + timedelta(days=days - 1)
    rows = []
    for it in items:
        if not it["include"]:
            continue
        for h in it["history"]:
            if start <= D(h["date"]) <= end:
                rows.append({"date": h["date"], "name": it["name"], "amount": round(h["amount"], 2),
                             "status": "posted", "id": it["id"], "account": h["account"], "cadence": it["cadence"]})
        for d in project(it, start, end):
            rows.append({"date": d.isoformat(), "name": it["name"], "amount": it["use_amount"],
                         "status": "expected", "id": it["id"], "account": it["account"], "cadence": it["cadence"],
                         "varies": it["varies"] and "amount_override" not in it, "amount_from": it.get("amount_from")})
    for m in manual:
        for d in project_manual(m, start, end):
            rows.append({"date": d.isoformat(), "name": m["name"], "amount": float(m["amount"]),
                         "status": "manual", "id": m["id"], "account": "", "cadence": m.get("cadence", "monthly")})
    rows.sort(key=lambda r: (r["date"], -r["amount"]))
    total = round(sum(r["amount"] for r in rows), 2)
    pd = paydays_between(pay, start, end)
    pay_total = round((pay.get("use_amount") or 0) * len(pd), 2)
    return {
        "start": start.isoformat(), "end": end.isoformat(), "bills": rows, "total": total,
        "total_posted": round(sum(r["amount"] for r in rows if r["status"] == "posted"), 2),
        "paydays": [d.isoformat() for d in pd], "pay_total": pay_total,
        "left": round(pay_total - total, 2),
    }
