# BillsCal

A small web page on your NAS: drop in RBC and BMO statements, and it shows the recurring bills
coming up in your next two weeks (calendar + list + total), plus a best guess at your biweekly pay.
Add new statements whenever you like — it learns from all of them and never counts anything twice.

Your statements stay in the `data` folder on the NAS. Nothing is sent anywhere; the code on
GitHub has no data in it.

## What it reads

| Bank | Best | Also works |
|---|---|---|
| RBC | **CSV** from Online Banking → *Download transactions* (chequing and Visa, one file can hold both) | QFX, PDF statements |
| BMO | **CSV** from Online Banking → *Download transactions* (chequing or Mastercard) | QFX, PDF statements |
| TD | **CSV** from EasyWeb → account → *Download* (`accountactivity.csv`, no header row; chequing vs Visa is worked out from the balance column) | QFX, PDF statements |

CSV/QFX downloads are the most reliable. PDFs are read by finding the transaction table and its
columns; if a PDF comes back with 0 rows or odd amounts, tap **Text** next to it on the *Files* tab
and send that (with account numbers blanked out) so the reader can be tuned.

Three to six months of history gives good guesses. Overlapping files are fine: for each account,
a transaction is kept once no matter how many files include it.

## How it decides

- **Recurring bill** = money out to the same payee on a regular schedule (weekly, every 2 weeks,
  monthly, every 2/3/6 months, yearly). Amount = the latest one, or a typical one if it varies (shown with ~).
- **Not counted by default:** credit card payments (the card's own subscriptions are already listed),
  transfers between your accounts, things whose amount jumps around (shopping), and bills that seem to have stopped.
  Flip any of them on the *Bills* tab. You can rename bills, set your own amount, and add bills that
  never show on these statements (e.g. rent paid elsewhere).
- **Pay** = the largest deposit that arrives every two weeks. If it guesses wrong, set a payday
  and amount on the *Bills* tab.
- **The two weeks** start on your payday: paid Oct 14 → Oct 14 to Oct 27. Use ‹ › to move a pay period,
  or *Pick a start date*. Bills already taken out this period show as *Paid*.
- An account whose charges show up as deposits can be fixed with **Signs backwards** on the *Files* tab.

## Install on Synology (from a phone browser)

1. **Make the image public (one time).** After the first build finishes (GitHub → *Actions* tab):
   your profile → **Packages** → `billscal` → **Package settings** → **Change visibility** → Public.
   The image holds no data or passwords.
2. **Make the folders** in File Station: `/docker/billscal` and inside it an empty `data` folder.
3. **Container Manager → Project → Create.**
   - Project name: `billscal`, Path: `/docker/billscal`
   - Source: *Create docker-compose.yml*, paste [`docker-compose.yml`](docker-compose.yml)
   - Change `BILLSCAL_PASSWORD`.
4. **Next → Done.** Open **http://NAS-IP:18490** (or `http://jtnas:18490` over Tailscale).

This is financial data: reach it at home or over Tailscale rather than adding it to the router's
port forwarding.

### Spending tab

A separate look at where money goes, built from the same statements:

- **Quick ranges**: this month (with "on pace for"), last month, 3 / 6 / 12 months, this year, or custom dates.
  Each is compared with the same stretch just before it.
- **Chart**: one bar per month (per week for ranges up to two months), dashed average line; striped
  bars are partial (part of a month, or outside the dates your statements cover).
- **Categories**: groceries, restaurants, coffee, gas, shopping, home, health, subscriptions, kids, etc.,
  from a built-in list of Canadian merchants. Tap a category to filter everything to it; tap a payee to
  narrow further.
- **Fixing categories**: tap any transaction → pick a category → optionally apply it to every transaction
  from that payee (now and future). *Automatic* undoes it.
- **Manage categories**: add your own (with optional keywords, e.g. `GOLF, PRO SHOP`), rename any category,
  give any category keywords, or remove one (its transactions go back to automatic; built-ins can be restored).
  Bank fees (service charges, NSF, overdraft, foreign-exchange and e-transfer fees) have their own category,
  separate from interest charges.
- **Search**: words and/or a dollar range (*$ from* / *$ to*) search every transaction you have, all dates and
  categories, with a total for what's found.
- **What counts?**: bills (housing, utilities, phone, insurance) are left out by default since the Bills tab
  covers them; transfers between your accounts, credit card payments and money in are never counted.
  Card purchases are counted on the card, not again when you pay the card off.

### Payday notification

At 6 am on every payday it sends a [ntfy](https://ntfy.sh) push with each bill in the two weeks
starting that day and the total (plus the pay guess and what's left).

1. Install the **ntfy** app on your phone and subscribe to a topic with a hard-to-guess name,
   e.g. `billscal-jason-x7k2` (anyone who knows a public ntfy.sh topic name can read it).
2. In the compose file set `NTFY_URL: "https://ntfy.sh/billscal-jason-x7k2"`.
3. On the *Bills* tab, tap **send a test now**.

`NTFY_TIME` changes the time. If the NAS was off at 6 am, the message goes out when it's back the
same day. Paydays come from the detected pay (or the payday you set on the *Bills* tab).

### Utility amounts (12-month average)

Bills whose amount varies (utilities) are planned at their average over the last 12 months.
Bank downloads only reach back 3–6 months, so BillsCal can also read the "Average bill (12mo)"
cells (I2) from the **Lacombe Utilities** Google Sheet:

1. In File Station, copy `service-account.json` from `/docker/utility-bills/data` to `/docker/billscal/data`.
   The sheet is already shared with that key, so nothing else to share.
2. Keep `SHEET_ID` in the compose file (it's the Lacombe Utilities sheet).
3. On the *Bills* tab, tap a utility's amount → **12-month average from my sheet** and tick
   Electricity, Gas or Water (tick Electricity + Gas if Echo takes both in one payment).

The sheet is re-read every hour, or tap **re-read** on the *Bills* tab. `SHEET_VALUES` changes which
cells are read (`Name=range;Name=range`). If the sheet can't be read, the statement average is used.

### Updating

Every push to `main` rebuilds the image (~3 min). Then **Container Manager → Project → billscal →
Action → Build** — `pull_policy: always` fetches the new image. Your data folder is untouched.
When the statement reader improves, stored files are re-read automatically on the next start.

## Files in `data/`

- `files/` — the statements you added (originals)
- `parsed/` — what was read from each (rebuilt automatically)
- `settings.json` — your names, on/off switches, payday and added bills

Back up the `data` folder with the rest of the NAS if you want to keep history.

## Developing

Plain Python 3.12 + `pdfplumber`, no web framework. `python app/server.py` with `DATA_DIR=./data`.
`tests/make_samples.py OUT` writes fake RBC/BMO files; `tests/run_test.py OUT` runs the reader and
detection on them.
