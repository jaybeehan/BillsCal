"""BillsCal — drop in bank / credit card statements, see the next two weeks of bills.

Everything stays in DATA_DIR on the NAS:
    files/<sha256>.<ext>   the statements you dropped in (originals)
    files.json             list of those files
    parsed/<sha256>.json   what was read from each file (re-read when the reader improves)
    settings.json          your edits: names, on/off, payday, extra bills
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import traceback
import uuid
from datetime import date, datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import analysis as A
import notify
import sheets
import parsers

PORT = int(os.environ.get("PORT", "18490"))
DATA = os.environ.get("DATA_DIR", "/data")
PASSWORD = os.environ.get("BILLSCAL_PASSWORD", "")
TZ = ZoneInfo(os.environ.get("TZ", "America/Edmonton"))
PUBLIC = os.path.join(os.path.dirname(__file__), "public")
MAX_UPLOAD = 25 * 1024 * 1024
COOKIE = "billscal_session"
lock = threading.RLock()


def log(*a):
    print(datetime.now(TZ).strftime("%H:%M:%S"), *a, flush=True)


def today():
    return datetime.now(TZ).date()


def path(*p):
    return os.path.join(DATA, *p)


def read_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, p)


# ----------------------------------------------------------------- storage
def load_files():
    return read_json(path("files.json"), [])


def parsed_for(f):
    """Read a stored file, using the cache unless the reader has changed."""
    cache = path("parsed", f["id"] + ".json")
    c = read_json(cache, None)
    if c and c.get("version") == parsers.PARSER_VERSION:
        return c["result"]
    with open(path("files", f["stored"]), "rb") as fh:
        raw = fh.read()
    try:
        result = parsers.parse_file(raw, f["name"])
    except Exception as e:  # a broken file shouldn't take the app down
        traceback.print_exc()
        result = {"format": "error", "transactions": [], "warnings": [f"Couldn't read this file: {e}"]}
    write_json(cache, {"version": parsers.PARSER_VERSION, "result": result})
    return result


_state = None


def state(force=False):
    """Everything the page needs, recomputed only when something changed."""
    global _state
    with lock:
        if _state is not None and not force:
            return _state
        settings = read_json(path("settings.json"), {})
        files = load_files()
        parsed = [{"id": f["id"], "result": parsed_for(f)} for f in files]
        txns = A.merge_transactions(parsed, settings)
        bills = A.detect_recurring(txns, settings, sheets.values())
        pay = A.detect_pay(txns, settings)
        file_rows = []
        for f, p in zip(files, parsed):
            ds = [t["date"] for t in p["result"]["transactions"]]
            file_rows.append({"id": f["id"], "name": f["name"], "uploaded": f["uploaded"],
                              "format": p["result"]["format"], "count": len(ds),
                              "first": min(ds) if ds else None, "last": max(ds) if ds else None,
                              "warnings": p["result"].get("warnings", [])})
        _state = {
            "settings": settings, "txns": txns, "bills": bills, "pay": pay,
            "accounts": A.account_list(parsed, settings), "files": file_rows,
            "manual": settings.get("manual_bills", []),
        }
        return _state


def save_settings(fn):
    with lock:
        s = read_json(path("settings.json"), {})
        fn(s)
        write_json(path("settings.json"), s)
        state(force=True)


def add_file(name, raw):
    sha = hashlib.sha256(raw).hexdigest()
    with lock:
        files = load_files()
        if any(f["id"] == sha[:16] for f in files):
            return {"name": name, "duplicate": True}
        ext = (os.path.splitext(name)[1] or ".dat").lower()[:6]
        if not re.fullmatch(r"\.[a-z0-9]+", ext):
            ext = ".dat"
        os.makedirs(path("files"), exist_ok=True)
        stored = sha[:16] + ext
        with open(path("files", stored), "wb") as fh:
            fh.write(raw)
        f = {"id": sha[:16], "name": os.path.basename(name)[:200], "stored": stored,
             "uploaded": datetime.now(TZ).isoformat(timespec="seconds")}
        files.append(f)
        write_json(path("files.json"), files)
        result = parsed_for(f)
        state(force=True)
        log("added", f["name"], result["format"], len(result["transactions"]), "rows")
        return {"name": f["name"], "format": result["format"], "count": len(result["transactions"]),
                "warnings": result.get("warnings", [])}


def delete_file(fid):
    with lock:
        files = load_files()
        f = next((x for x in files if x["id"] == fid), None)
        if not f:
            return False
        for p in (path("files", f["stored"]), path("parsed", fid + ".json")):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        write_json(path("files.json"), [x for x in files if x["id"] != fid])
        state(force=True)
        return True


# -------------------------------------------------------------------- auth
def secret():
    p = path(".secret")
    s = None
    try:
        s = open(p).read().strip()
    except FileNotFoundError:
        pass
    if not s or len(s) < 32:
        s = secrets.token_hex(32)
        os.makedirs(DATA, exist_ok=True)
        with open(p, "w") as fh:
            fh.write(s)
        os.chmod(p, 0o600)
    return s


def sign(exp):
    return hmac.new(secret().encode(), f"{exp}|{PASSWORD}".encode(), hashlib.sha256).hexdigest()


def signed_in(handler):
    if not PASSWORD:
        return True
    c = cookies.SimpleCookie(handler.headers.get("Cookie", ""))
    if COOKIE not in c:
        return False
    try:
        exp, sig = c[COOKIE].value.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, sign(exp))
    except ValueError:
        return False


failures = {}


def login_page(msg=""):
    msg = re.sub(r"[<>&]", "", msg)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>BillsCal · Sign in</title>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#1d1f1e;--line:#e3e0d9;--accent:#1f6f54}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121413;--card:#1b1e1d;--ink:#e8eae8;--line:#2c302e;--accent:#4cc196}}}}
html,body{{margin:0;height:100%;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
body{{display:grid;place-items:center;padding:16px;box-sizing:border-box}}
form{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px;width:min(340px,100%);box-sizing:border-box}}
h1{{font-size:20px;margin:0 0 16px}} input{{width:100%;box-sizing:border-box;font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:11px 12px}}
button{{margin-top:12px;width:100%;font:inherit;font-weight:600;background:var(--accent);color:#fff;border:0;border-radius:10px;padding:11px;cursor:pointer}}
.msg{{color:#c0392b;min-height:20px;margin-top:10px;font-size:14px}}
</style></head><body><form method="post" action="login"><h1>BillsCal</h1>
<input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
<button>Sign in</button><div class="msg">{msg}</div></form></body></html>"""


# ------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "BillsCal"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self, limit=1024 * 1024):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("too large")
        return self.rfile.read(n)

    def ip(self):
        return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()

    # -- routing
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        u = urlparse(self.path)
        p = u.path
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if p == "/healthz":
                return self.send(200, {"ok": True})
            if p == "/login" and method == "POST":
                return self.login()
            if p == "/logout":
                return self.send(302, "", headers={"Location": "./", "Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})
            if not signed_in(self):
                if p.startswith("/api/"):
                    return self.send(401, {"error": "sign in"})
                return self.send(200, login_page(), "text/html; charset=utf-8")
            if method == "POST" and not self.same_origin():
                return self.send(403, {"error": "bad origin"})
            if p in ("/", "/index.html") and method == "GET":
                with open(os.path.join(PUBLIC, "index.html"), "rb") as fh:
                    return self.send(200, fh.read(), "text/html; charset=utf-8")
            if p == "/api/state" and method == "GET":
                return self.send(200, self.api_state())
            if p == "/api/forecast" and method == "GET":
                return self.send(200, self.api_forecast(q))
            if p == "/api/transactions" and method == "GET":
                return self.send(200, state()["txns"])
            if p == "/api/upload" and method == "POST":
                name = q.get("name") or "statement"
                raw = self.body(MAX_UPLOAD)
                if not raw:
                    return self.send(400, {"error": "empty file"})
                return self.send(200, add_file(name, raw))
            m = re.fullmatch(r"/api/files/([0-9a-f]{16})(/text)?", p)
            if m and method == "DELETE":
                return self.send(200 if delete_file(m[1]) else 404, {"ok": True})
            if m and m[2] and method == "GET":
                return self.send(200, self.file_text(m[1]), "text/plain; charset=utf-8")
            if p == "/api/notify-test" and method == "POST":
                if not notify.enabled():
                    return self.send(400, {"error": "Set NTFY_URL in the compose file first"})
                st = state()
                t = today()
                start = A.default_start(st["pay"], t)
                title, msg = notify.build(A.forecast(st["bills"], st["pay"], st["manual"], start, 14), st["pay"])
                notify.send("Test · " + title, msg)
                return self.send(200, {"ok": True})
            if p == "/api/sheet-refresh" and method == "POST":
                if sheets.enabled():
                    sheets.refresh(DATA)
                    state(force=True)
                return self.send(200, sheets.status())
            if p == "/api/settings" and method == "POST":
                self.api_settings(json.loads(self.body() or b"{}"))
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "not found"})
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": str(e)})

    def same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == self.headers.get("Host")

    def login(self):
        ip = self.ip()
        f = failures.get(ip, {"n": 0, "until": 0})
        if f["until"] > time.time():
            return self.send(200, login_page(f"Too many tries. Wait {int(f['until'] - time.time())} s."), "text/html; charset=utf-8")
        form = parse_qs(self.body().decode())
        pw = form.get("password", [""])[0]
        if PASSWORD and hmac.compare_digest(hashlib.sha256(pw.encode()).digest(), hashlib.sha256(PASSWORD.encode()).digest()):
            failures.pop(ip, None)
            exp = str(int(time.time()) + 90 * 86400)
            ck = f"{COOKIE}={exp}.{sign(exp)}; Path=/; HttpOnly; SameSite=Lax; Max-Age={90 * 86400}"
            return self.send(302, "", headers={"Location": "./", "Set-Cookie": ck})
        f["n"] += 1
        if f["n"] >= 5:
            f["until"] = time.time() + 30
        failures[ip] = f
        return self.send(200, login_page("Wrong password."), "text/html; charset=utf-8")

    # -- api
    def api_state(self):
        s = state()
        t = today()
        return {
            "today": t.isoformat(), "default_start": A.default_start(s["pay"], t).isoformat(),
            "pay": s["pay"], "bills": s["bills"], "accounts": s["accounts"], "files": s["files"],
            "manual": s["manual"], "txn_count": len(s["txns"]),
            "data_last": max((x["date"] for x in s["txns"]), default=None),
            "password": bool(PASSWORD), "ntfy": notify.enabled(), "ntfy_time": notify.NTFY_TIME,
            "sheet": sheets.status(),
        }

    def api_forecast(self, q):
        s = state()
        start = date.fromisoformat(q["start"]) if q.get("start") else A.default_start(s["pay"], today())
        days = max(1, min(62, int(q.get("days") or 14)))
        return A.forecast(s["bills"], s["pay"], s["manual"], start, days)

    def api_settings(self, b):
        def apply(s):
            if "account" in b:
                a = b["account"]
                cur = s.setdefault("accounts", {}).setdefault(str(a["id"]), {})
                for k in ("label", "flip", "ignore"):
                    if k in a:
                        cur[k] = a[k]
            if "bill" in b:
                x = b["bill"]
                cur = s.setdefault("bills", {}).setdefault(str(x["id"]), {})
                for k in ("include", "name", "amount", "source"):
                    if k in x:
                        if x[k] in (None, ""):
                            cur.pop(k, None)
                        else:
                            cur[k] = x[k]
            if "pay" in b:
                cur = s.setdefault("pay", {})
                for k in ("anchor", "amount", "every"):
                    if k in b["pay"]:
                        v = b["pay"][k]
                        if v in (None, ""):
                            cur.pop(k, None)
                        else:
                            if k == "anchor":
                                date.fromisoformat(v)
                            cur[k] = float(v) if k == "amount" else int(v) if k == "every" else v
            if "manual_add" in b:
                m = b["manual_add"]
                date.fromisoformat(m["anchor"])
                s.setdefault("manual_bills", []).append({
                    "id": "m-" + uuid.uuid4().hex[:8], "name": str(m["name"])[:80],
                    "amount": round(float(m["amount"]), 2), "anchor": m["anchor"],
                    "cadence": m.get("cadence") if m.get("cadence") in ("weekly", "biweekly", "monthly", "quarterly", "yearly") else "monthly"})
            if "manual_delete" in b:
                s["manual_bills"] = [m for m in s.get("manual_bills", []) if m["id"] != b["manual_delete"]]
        save_settings(apply)

    def file_text(self, fid):
        f = next((x for x in load_files() if x["id"] == fid), None)
        if not f:
            return "not found"
        with open(path("files", f["stored"]), "rb") as fh:
            raw = fh.read()
        if raw[:5] == b"%PDF-":
            return parsers.pdf_text(raw)
        return raw[:200000].decode("utf-8", errors="replace")


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    if not PASSWORD:
        log("WARNING: BILLSCAL_PASSWORD is empty, so anyone who can reach the page can see your statements.")
    sheets.start(DATA, lambda: state(force=True), log)
    state(force=True)
    notify.start(state, today, lambda: datetime.now(TZ), DATA, log)
    log(f"BillsCal on port {PORT}, data in {DATA}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
