"""Turn bank / credit card files into a plain list of transactions.

Every parser returns:
    {"format": str, "transactions": [txn, ...], "warnings": [str, ...]}
where each txn is
    {"date": "YYYY-MM-DD", "desc": str, "amount": float,
     "bank": "RBC"|"BMO"|"?", "kind": "bank"|"credit", "last4": str}

Sign rule everywhere: amount < 0 means money left you (a bill, a purchase,
a withdrawal); amount > 0 means money came in (pay, a refund, a card payment
landing on the card).

Supported:
  * RBC CSV export      (Account Type, Account Number, Transaction Date, ..., CAD$)
  * BMO chequing CSV    (First Bank Card, Transaction Type, Date Posted, Transaction Amount, Description)
  * BMO Mastercard CSV  (Item #, Card #, Transaction Date, Posting Date, Transaction Amount, Description)
  * any other CSV with recognisable date / description / amount columns
  * QFX / OFX ("Quicken" / "Money" downloads) from either bank
  * PDF statements (RBC / BMO chequing, Visa, Mastercard), read by column position
"""

import csv
import io
import re
from datetime import date, datetime, timedelta

PARSER_VERSION = 4  # bump to make the server re-read every stored file

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MON_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"


# --------------------------------------------------------------------- helpers
def to_amount(s):
    """'1,234.56' / '-$12.00' / '(12.00)' / '12.00CR' / '12.00-' -> float or None."""
    if s is None:
        return None
    t = str(s).strip().replace("$", "").replace(",", "").replace(" ", "")
    if not t:
        return None
    neg = False
    if t.upper().endswith("CR"):
        t, neg = t[:-2], True
    if t.endswith("-"):
        t, neg = t[:-1], True
    if t.startswith("(") and t.endswith(")"):
        t, neg = t[1:-1], True
    try:
        v = float(t)
    except ValueError:
        return None
    return -abs(v) if neg else v


def parse_date(s, end_hint=None):
    """Many formats. end_hint (date) supplies the year when the text has none."""
    s = str(s or "").strip().strip("'\"")
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%b %d %Y",
                "%B %d %Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%b. %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
        mo, d = (a, b) if a <= 12 else (b, a)  # Canadian exports are M/D/Y; D/M/Y if obvious
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    # Year-less forms used inside PDF statements: "Oct 14", "Oct. 14", "14 Oct"
    m = re.fullmatch(MON_RE + r" ?(\d{1,2})", s, re.I) or None
    if m:
        mo, d = MONTHS[m[1][:3].lower()], int(m[2])
    else:
        m = re.fullmatch(r"(\d{1,2}) ?" + MON_RE, s, re.I)
        if not m:
            return None
        d, mo = int(m[1]), MONTHS[m[2][:3].lower()]
    if end_hint is None:
        end_hint = date.today()
    for y in (end_hint.year, end_hint.year - 1, end_hint.year + 1):
        try:
            cand = date(y, mo, d)
        except ValueError:
            continue
        if cand <= end_hint + timedelta(days=10):
            return cand
    return None


def guess_bank(text):
    t = text.lower()
    if "royal bank" in t or re.search(r"\brbc\b", t):
        return "RBC"
    if "bmo" in t or "bank of montreal" in t:
        return "BMO"
    if "td canada trust" in t or "toronto-dominion" in t or re.search(r"\btd\b", t) or "accountactivity" in t:
        return "TD"
    if "cibc" in t:
        return "CIBC"
    return "?"


def guess_kind(text):
    t = text.lower()
    if re.search(r"visa|mastercard|master card|credit limit|minimum payment|credit card", t):
        return "credit"
    return "bank"


def last4_of(s):
    digits = re.sub(r"\D", "", str(s or ""))
    return digits[-4:] if len(digits) >= 4 else ""


def flip_credit_if_needed(txns, warnings):
    """Card exports disagree on sign. Purchases always outnumber payments on a
    card, so if most lines are positive, the file used 'positive = charge'."""
    cards = [t for t in txns if t["kind"] == "credit"]
    if len(cards) >= 3 and sum(t["amount"] > 0 for t in cards) / len(cards) > 0.6:
        for t in cards:
            t["amount"] = -t["amount"]
        warnings.append("Card amounts were listed as positive charges; flipped so charges are money out.")


# ------------------------------------------------------------------------ CSV
def parse_csv(raw, filename=""):
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    warnings = []

    # Find the header row (BMO puts a "data is valid as of" line or two first).
    hdr_i = None
    for i, r in enumerate(rows[:30]):
        low = [c.strip().lower() for c in r]
        if any("date" in c for c in low) and any(
                c in ("cad$", "amount", "transaction amount", "debit", "credit", "withdrawals",
                      "deposits", "withdrawal", "deposit", "funds out", "funds in") or "amount" in c
                for c in low):
            hdr_i = i
            break
    if hdr_i is None:
        return parse_headerless_csv(rows, filename)

    hdr = [c.strip().lower() for c in rows[hdr_i]]

    def col(*names, contains=None):
        for n in names:
            if n in hdr:
                return hdr.index(n)
        if contains:
            for i, c in enumerate(hdr):
                if contains in c:
                    return i
        return None

    c_date = col("transaction date", "date posted", "posting date", "date", contains="date")
    desc_cols = [i for i, c in enumerate(hdr) if c.startswith("description") or c in ("payee", "merchant", "memo", "details", "name")]
    c_amt = col("cad$", "transaction amount", "amount", contains="amount")
    c_out = col("debit", "withdrawals", "withdrawal", "funds out", "money out")
    c_in = col("credit", "deposits", "deposit", "funds in", "money in")
    c_type = col("transaction type")
    c_acct = col("account number", "card #", "card number", "first bank card", "account")
    c_atype = col("account type")

    joined = ",".join(hdr)
    if "cad$" in hdr or "description 1" in hdr:
        bank = "RBC"
    elif "first bank card" in hdr or ("item #" in hdr and "card #" in hdr):
        bank = "BMO"
    else:
        bank = guess_bank(filename + " " + text[:500])
    file_kind = "credit" if ("card #" in hdr or "item #" in hdr or guess_kind(filename) == "credit") else "bank"

    txns = []
    for r in rows[hdr_i + 1:]:
        if not r or all(not c.strip() for c in r):
            continue
        get = lambda i: r[i].strip() if i is not None and i < len(r) else ""
        d = parse_date(get(c_date))
        if not d:
            continue
        if c_amt is not None and get(c_amt):
            amt = to_amount(get(c_amt))
        else:
            out_v, in_v = to_amount(get(c_out)), to_amount(get(c_in))
            if out_v is None and in_v is None:
                continue
            amt = (abs(in_v) if in_v else 0) - (abs(out_v) if out_v else 0)
        if amt is None:
            continue
        ttype = get(c_type).upper()
        if ttype in ("DEBIT", "DR", "WITHDRAWAL"):
            amt = -abs(amt)
        elif ttype in ("CREDIT", "CR", "DEPOSIT"):
            amt = abs(amt)
        kind = file_kind
        atype = get(c_atype).lower()
        if atype:
            kind = "credit" if re.search(r"visa|master|credit|amex", atype) else "bank"
        desc = " ".join(get(i) for i in desc_cols if get(i)) or "(no description)"
        txns.append({"date": d.isoformat(), "desc": desc, "amount": round(amt, 2),
                     "bank": bank, "kind": kind, "last4": last4_of(get(c_acct))})

    flip_credit_if_needed(txns, warnings)
    if not txns:
        warnings.append("Header found, but no rows had a readable date and amount.")
    return {"format": f"csv ({bank})", "transactions": txns, "warnings": warnings}


def parse_headerless_csv(rows, filename=""):
    """TD (accountactivity.csv) and CIBC: date, description, money out, money in[, balance or card #].

    Chequing vs credit card is worked out from the running balance when there is one:
    on a bank account money out lowers it, on a card money out raises it."""
    warnings = []
    bank = guess_bank(filename)
    if bank == "?":
        bank = "TD"  # TD is the common headerless export; rename the account if not
    parsed = []
    for r in rows:
        if len(r) < 4:
            continue
        d = parse_date(r[0])
        if not d:
            continue
        out_v, in_v = to_amount(r[2]), to_amount(r[3])
        if out_v is None and in_v is None:
            continue
        bal = to_amount(r[4]) if len(r) > 4 else None
        card = last4_of(r[4]) if len(r) > 4 and re.fullmatch(r"[\d*xX ]{12,19}", r[4].strip()) else ""
        parsed.append((d, r[1].strip() or "(no description)", abs(out_v or 0), abs(in_v or 0), None if card else bal, card))
    if not parsed:
        return {"format": "csv", "transactions": [], "warnings": ["Couldn't find a header row with date and amount columns."]}

    # balance check: compare consecutive rows in file order
    bank_votes = card_votes = 0
    for prev, cur in zip(parsed, parsed[1:]):
        if prev[4] is None or cur[4] is None:
            continue
        for a, b, row in ((prev, cur, cur), (cur, prev, prev)):  # files may be newest- or oldest-first
            if abs(a[4] - row[2] + row[3] - b[4]) < 0.011:
                bank_votes += 1
            if abs(a[4] + row[2] - row[3] - b[4]) < 0.011:
                card_votes += 1
    if card_votes > bank_votes or any(p[5] for p in parsed):
        kind = "credit"
    elif bank_votes > card_votes:
        kind = "bank"
    else:
        kind = guess_kind(filename + " " + " ".join(p[1] for p in parsed[:50]))
    last4 = next((p[5] for p in parsed if p[5]), "")
    txns = [{"date": d.isoformat(), "desc": desc, "amount": round(i - o, 2), "bank": bank, "kind": kind, "last4": last4}
            for d, desc, o, i, _, _ in parsed]
    warnings.append(f"No header row: read as {bank} {'credit card' if kind == 'credit' else 'chequing/savings'}. "
                    "If that's wrong, fix it on the Files tab.")
    return {"format": f"csv ({bank})", "transactions": txns, "warnings": warnings}


# ------------------------------------------------------------------- QFX / OFX
def parse_ofx(raw, filename=""):
    text = raw.decode("latin-1", errors="replace")
    warnings = []

    def tag(block, name):
        m = re.search(r"<%s>([^<\r\n]*)" % name, block, re.I)
        return m[1].strip() if m else ""

    org = tag(text, "ORG") + " " + tag(text, "FID") + " " + filename
    bank = guess_bank(org) if guess_bank(org) != "?" else guess_bank(text[:2000])
    kind = "credit" if re.search(r"<CCACCTFROM>|<CREDITCARDMSGSRSV1>", text, re.I) else "bank"
    last4 = last4_of(tag(text, "ACCTID"))
    txns = []
    for block in re.findall(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>)", text, re.S | re.I):
        d = parse_date(tag(block, "DTPOSTED")[:8])
        amt = to_amount(tag(block, "TRNAMT"))
        if not d or amt is None:
            continue
        desc = " ".join(x for x in (tag(block, "NAME"), tag(block, "MEMO")) if x) or "(no description)"
        txns.append({"date": d.isoformat(), "desc": desc, "amount": round(amt, 2),
                     "bank": bank, "kind": kind, "last4": last4})
    if not txns:
        warnings.append("No <STMTTRN> transactions found in this file.")
    return {"format": f"ofx ({bank})", "transactions": txns, "warnings": warnings}


# ------------------------------------------------------------------------ PDF
OUT_WORDS = ("withdrawal", "withdrawals", "deducted", "debits", "debit", "cheques", "withdrawn", "paid out", "funds out")
IN_WORDS = ("deposits", "deposit", "added", "credits", "credit", "paid in", "funds in")
SKIP_LINE = re.compile(
    r"opening balance|closing balance|balance forward|previous (statement )?balance|new balance|"
    r"^total|subtotal|total (of )?(deposits|withdrawals|credits|debits|purchases)|calculating your balance|"
    r"minimum payment|credit limit|available credit|interest rate|statement balance|page \d+ of", re.I)
AMT_TOKEN = re.compile(r"^\(?-?\$?\d{1,3}(?:,\d{3})*\.\d{2}\)?(?:CR|-)?$|^\(?-?\$?\d+\.\d{2}\)?(?:CR|-)?$", re.I)
LEAD_DATE = re.compile(r"^(?:" + MON_RE + r" ?\d{1,2}|\d{1,2} ?" + MON_RE + r")\b", re.I)


def _lines(page):
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False, x_tolerance=1.5, y_tolerance=2)
    words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    lines, cur, top = [], [], None
    for w in words:
        if top is None or abs(w["top"] - top) <= 3:
            cur.append(w)
            top = w["top"] if top is None else top
        else:
            lines.append(sorted(cur, key=lambda w: w["x0"]))
            cur, top = [w], w["top"]
    if cur:
        lines.append(sorted(cur, key=lambda w: w["x0"]))
    return lines


def _statement_end(text):
    """Find the statement period's closing date (gives years to 'Oct 14' rows)."""
    full = r"(" + MON_RE + r" ?\d{1,2},? ?\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2} " + MON_RE + r" ?\d{4})"
    m = re.search(r"(?:from|period)[^\n]{0,40}?" + full + r"[^\n]{0,15}?(?:to|-|–)\s*" + full, text, re.I)
    if m:
        d = parse_date(_last_group(m).replace(".", ""))
        if d:
            return d
    found = []
    for m in re.finditer(full, text[:6000], re.I):
        d = parse_date(m[1].replace(".", ""))
        if d:
            found.append(d)
    return max(found) if found else None


def _last_group(m):
    groups = [g for g in m.groups() if g and re.search(r"\d", g) and len(g) > 6]
    return groups[-1] if groups else ""


def _find_columns(lines, i):
    """If lines[i] (plus its neighbours) is a table header, return column x-positions."""
    window = lines[max(0, i - 2): i + 3]
    text = " ".join(w["text"] for w in lines[i]).lower()
    if not re.search(r"\bdate\b|description|activity|transaction", text):
        return None
    cols = {}
    for ln in window:
        for w in ln:
            t = w["text"].lower().strip("()$:")
            role = None
            if t in OUT_WORDS:
                role = "out"
            elif t in IN_WORDS:
                role = "in"
            elif t == "balance":
                role = "balance"
            elif t == "amount":
                role = "amount"
            if role and role not in cols:
                # amounts are right-aligned: use the right edge of the header phrase
                right = w["x1"]
                for x in ln:  # include a trailing "($)" so the edge matches right-aligned numbers
                    if x["x0"] >= w["x1"] - 1 and x["x0"] - right < 8 and x["text"].strip() in ("($)", "$", "(CAD)", "($CAD)"):
                        right = x["x1"]
                cols[role] = {"x0": w["x0"], "x1": right}
    if "out" in cols or "in" in cols or "amount" in cols:
        return cols
    return None


def parse_pdf(raw, filename=""):
    import pdfplumber  # imported here so CSV-only use doesn't need it loaded

    warnings = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        pages_text = [(p.extract_text() or "") for p in pdf.pages]
        alltext = "\n".join(pages_text)
        bank = guess_bank(alltext[:4000] + " " + filename)
        head = (pages_text[0] if pages_text else "")[:1500] + " " + filename
        # Judge the account type from the page header only: a chequing statement
        # can contain "RBC VISA" as a payment line.
        if re.search(r"chequing|savings|day to day|no limit banking|performance plan|bank account", head, re.I):
            kind = "bank"
        else:
            kind = guess_kind(head)
        m = re.search(r"(?:\d{4}|\*{4}|x{4})[\s*x]{1,12}(\d{4})\b", alltext[:4000], re.I) \
            or re.search(r"account(?: number| no\.?| #)?:?\s*([\d\- ]{7,})", alltext[:4000], re.I)
        last4 = last4_of(m[1]) if m else ""
        end = _statement_end(alltext) or date.today()

        txns, cols = [], None
        cur_date = None
        buf, buf_dated = [], False

        def flush_to_prev():
            nonlocal buf
            if buf and txns:
                txns[-1]["desc"] = (txns[-1]["desc"] + " " + " ".join(buf)).strip()
            buf = []

        for page in pdf.pages:
            lines = _lines(page)
            in_table = False
            for i, ln in enumerate(lines):
                text = " ".join(w["text"] for w in ln)
                found = _find_columns(lines, i)
                if found:
                    cols, in_table = found, True
                    flush_to_prev()
                    continue
                if not cols:
                    continue
                if SKIP_LINE.search(text):
                    flush_to_prev()
                    continue
                left_edge = min(c["x0"] for c in cols.values()) - 25
                amt_words = [w for w in ln if AMT_TOKEN.match(w["text"]) and w["x1"] > left_edge]
                desc_words = [w for w in ln if w not in amt_words]

                # leading date(s): one for chequing, two (trans + posted) for cards
                dtext = " ".join(w["text"] for w in desc_words)
                lead = LEAD_DATE.match(dtext)
                row_date = None
                if lead:
                    row_date = parse_date(lead[0].replace(".", ""), end)
                    dtext = dtext[lead.end():].strip()
                    second = LEAD_DATE.match(dtext)
                    if second:
                        dtext = dtext[second.end():].strip()
                if row_date:
                    flush_to_prev()
                    cur_date = row_date

                money = []
                for w in amt_words:
                    role = min(cols, key=lambda r: abs(cols[r]["x1"] - w["x1"]))
                    if role != "balance":
                        money.append((role, w["text"]))

                if not money:
                    if dtext and cur_date and in_table:
                        if row_date:
                            buf, buf_dated = [dtext], True
                        else:
                            if not buf:
                                buf_dated = False
                            buf.append(dtext)
                    continue
                if not cur_date:
                    continue

                for role, tok in money:
                    v = to_amount(tok)
                    if v is None:
                        continue
                    if role == "out":
                        amt = -abs(v)
                    elif role == "in":
                        amt = abs(v)
                    elif kind == "credit":  # single Amount column on a card: plain = charge, -/CR = credit
                        amt = abs(v) if v < 0 else -abs(v)
                    else:
                        amt = v
                    if buf_dated or not dtext:
                        desc = " ".join(buf + ([dtext] if dtext else []))
                        buf = []
                    else:
                        flush_to_prev()
                        desc = dtext
                    txns.append({"date": cur_date.isoformat(), "desc": desc.strip() or "(no description)",
                                 "amount": round(amt, 2), "bank": bank, "kind": kind, "last4": last4})
                    buf_dated = False
            flush_to_prev()

    if not cols:
        warnings.append("Couldn't find the transaction table in this PDF. A CSV download from online banking will work.")
    elif not txns:
        warnings.append("Found the table but no transaction rows. A CSV download from online banking will work.")
    return {"format": f"pdf ({bank} {kind})", "transactions": txns, "warnings": warnings,
            "period_end": end.isoformat()}


def pdf_text(raw):
    import pdfplumber
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n\n----- page break -----\n\n".join((p.extract_text() or "") for p in pdf.pages)


# --------------------------------------------------------------------- router
def parse_file(raw, filename):
    name = filename.lower()
    head = raw[:600].lstrip()
    if head.startswith(b"%PDF") or name.endswith(".pdf"):
        return parse_pdf(raw, filename)
    if b"OFXHEADER" in head or b"<OFX>" in raw[:4000].upper() or name.endswith((".qfx", ".ofx", ".qbo")):
        return parse_ofx(raw, filename)
    return parse_csv(raw, filename)
