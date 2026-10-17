"""Optional: read 12-month averages from your Google Sheet (e.g. Lacombe Utilities).

Uses the same kind of Google service-account key as the UtilityBills app. Put the
key in the data folder as service-account.json, share the sheet with the key's
email (Viewer is enough), and set SHEET_ID. SHEET_VALUES lists what to read:
    Name=range;Name=range
Default: the "Average bill (12mo)" cell (I2) on each Lacombe Utilities tab.
Values are re-read every hour.
"""

import os
import re
import threading
import time
import traceback
from datetime import datetime

SHEET_ID = os.environ.get("SHEET_ID", "").strip()
SHEET_VALUES = os.environ.get(
    "SHEET_VALUES",
    "Electricity='Echo Electricity.power'!I2;Gas='Echo Gas'!I2;Water='Lacombe Utilities'!I2").strip()

_values, _error, _updated = {}, "", None
_lock = threading.Lock()


def specs():
    out = []
    for part in SHEET_VALUES.split(";"):
        if "=" in part:
            name, rng = part.split("=", 1)
            if name.strip() and rng.strip():
                out.append((name.strip(), rng.strip()))
    return out


def key_file(data_dir):
    return os.environ.get("GOOGLE_KEY_FILE") or os.path.join(data_dir, "service-account.json")


def enabled():
    return bool(SHEET_ID)


def to_number(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v or ""))
    try:
        return float(s)
    except ValueError:
        return None


def refresh(data_dir):
    """Read the cells. Returns True if any value changed."""
    global _values, _error, _updated
    import gspread
    try:
        kf = key_file(data_dir)
        if not os.path.exists(kf):
            raise FileNotFoundError(f"Put the Google key in the data folder as {os.path.basename(kf)}")
        book = gspread.service_account(filename=kf).open_by_key(SHEET_ID)
        sp = specs()
        res = book.values_batch_get([r for _, r in sp], params={"valueRenderOption": "UNFORMATTED_VALUE"})
        vals = {}
        for (name, _), vr in zip(sp, res.get("valueRanges", [])):
            cell = (vr.get("values") or [[None]])[0]
            vals[name] = to_number(cell[0] if cell else None)
        with _lock:
            changed = vals != _values
            _values, _error, _updated = vals, "", datetime.now().isoformat(timespec="minutes")
        return changed
    except Exception as e:
        with _lock:
            _error = f"{type(e).__name__}: {e}"[:300]
        traceback.print_exc()
        return False


def values():
    with _lock:
        return dict(_values)


def status():
    with _lock:
        return {"enabled": enabled(), "values": dict(_values), "error": _error, "updated": _updated,
                "names": [n for n, _ in specs()]}


def start(data_dir, on_change, log):
    if not enabled():
        return
    refresh(data_dir)
    log("sheet: read", values() or _error)

    def loop():
        while True:
            time.sleep(3600)
            if refresh(data_dir):
                log("sheet: values changed", values())
                on_change()

    threading.Thread(target=loop, daemon=True, name="sheet").start()
