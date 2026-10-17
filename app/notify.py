"""Payday push notification through ntfy.

Set NTFY_URL to a full topic URL, e.g. https://ntfy.sh/jason-bills-x7k2 (or your own
ntfy server). On each payday at NTFY_TIME (default 06:00, local TZ) one message is sent
with every bill in the two weeks starting that day and the total. If the NAS or the
container was off at that time, it is sent when it comes back, the same day.
"""

import json
import os
import threading
import time
import traceback
import urllib.request
from datetime import date, datetime
from urllib.parse import urlparse

import analysis as A

NTFY_URL = os.environ.get("NTFY_URL", "").strip().rstrip("/")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "").strip()
NTFY_TIME = os.environ.get("NTFY_TIME", "06:00").strip()
PAGE_URL = os.environ.get("BILLSCAL_URL", "").strip()


def enabled():
    return bool(NTFY_URL)


def money(n):
    return f"${n:,.2f}"


def build(fc, pay):
    start, end = date.fromisoformat(fc["start"]), date.fromisoformat(fc["end"])
    title = f"Payday: {money(fc['total'])} in bills, {start:%b} {start.day} to {end:%b} {end.day}"
    lines = []
    for b in fc["bills"]:
        d = date.fromisoformat(b["date"])
        tilde = "~" if b.get("varies") and b["status"] == "expected" else ""
        paid = " (paid)" if b["status"] == "posted" else ""
        lines.append(f"{d:%a} {d:%b} {d.day}  {b['name']}  {tilde}{money(b['amount'])}{paid}")
    if not lines:
        lines.append("No recurring bills in these two weeks.")
    lines.append("")
    lines.append(f"Total: {money(fc['total'])}")
    if fc.get("pay_total"):
        lines.append(f"Pay (guess): {money(fc['pay_total'])}  ·  Left after bills: {money(fc['left'])}")
    return title, "\n".join(lines)


def send(title, message):
    u = urlparse(NTFY_URL)
    topic = u.path.strip("/")
    if not topic or "/" in topic:
        raise ValueError("NTFY_URL must look like https://ntfy.sh/your-topic")
    body = {"topic": topic, "title": title, "message": message, "tags": ["calendar", "moneybag"]}
    if PAGE_URL:
        body["click"] = PAGE_URL
    req = urllib.request.Request(f"{u.scheme}://{u.netloc}/", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    if NTFY_TOKEN:
        req.add_header("Authorization", f"Bearer {NTFY_TOKEN}")
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def start(get_state, today_fn, now_fn, data_dir, log):
    """Background loop: check twice a minute whether today's payday message is due."""
    if not enabled():
        log("ntfy: NTFY_URL not set, payday notifications off")
        return
    hh, mm = (int(x) for x in NTFY_TIME.split(":"))
    marker = os.path.join(data_dir, "ntfy-last.txt")
    log(f"ntfy: payday notifications at {hh:02d}:{mm:02d} to {NTFY_URL}")

    def loop():
        while True:
            try:
                now, t = now_fn(), today_fn()
                try:
                    last = open(marker).read().strip()
                except FileNotFoundError:
                    last = ""
                if (now.hour, now.minute) >= (hh, mm) and last != t.isoformat():
                    s = get_state()
                    if A.paydays_between(s["pay"], t, t):
                        fc = A.forecast(s["bills"], s["pay"], s["manual"], t, 14)
                        send(*build(fc, s["pay"]))
                        log("ntfy: sent payday bills for", t.isoformat())
                    with open(marker, "w") as f:  # checked today either way
                        f.write(t.isoformat())
            except Exception:
                traceback.print_exc()
                time.sleep(600)  # retry in 10 min if ntfy was unreachable
            time.sleep(30)

    threading.Thread(target=loop, daemon=True, name="ntfy").start()
