"""Fake RBC/BMO exports (CSV, QFX, PDF) for testing. Not real data."""
import random, sys, os
from datetime import date, timedelta
random.seed(4)
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

start, end = date(2026, 6, 20), date(2026, 10, 16)
def days():
    d = start
    while d <= end:
        yield d; d += timedelta(days=1)

def clamp(y, m, dom):
    import calendar
    return date(y, m, min(dom, calendar.monthrange(y, m)[1]))

chq = []   # RBC chequing: (date, d1, d2, amount)
d = date(2026, 6, 24)
while d <= end:
    chq.append((d, "Payroll Deposit", "ACME HEALTH SERVICES", round(2843.17 + random.choice([0, 0, 0, 12.4]), 2)))
    d += timedelta(days=14)
for m in range(6, 11):
    for dom, d1, d2, amt in [(1, "Insurance", "MANULIFE", -142.30), (5, "Online Banking payment - 4231", "ROGERS", -95.50),
                             (15, "Mortgage Payment", "RBC MORTGAGE 8842", -1650.00), (22, "MISC PAYMENT", "ECHO ENERGY", -random.uniform(160, 240)),
                             (18, "Online Banking payment - 7711", "RBC VISA", -random.uniform(600, 1100)),
                             (28, "MISC PAYMENT", "CITY OF LACOMBE", -random.uniform(80, 110)),
                             (10, "Online Banking transfer - 3312", "", -300.0)]:
        dt = clamp(2026, m, dom)
        if start <= dt <= end:
            chq.append((dt, d1, d2, round(amt, 2)))
for dt in days():
    if random.random() < 0.08:
        chq.append((dt, "Interac purchase - 1234", "SOBEYS #4412", -round(random.uniform(20, 180), 2)))

visa = []
for m in range(6, 11):
    for dom, desc, amt in [(12, "NETFLIX.COM 866-579-7172 ON", -20.99), (3, "APPLE.COM/BILL 866-712-7753 ON", -4.51),
                           (19, "APPLE.COM/BILL 866-712-7753 ON", -15.81)]:
        dt = clamp(2026, m, dom)
        if start <= dt <= end:
            visa.append((dt, desc, amt))
    dt = clamp(2026, m, 18)
    if start <= dt <= end:
        visa.append((dt, "PAYMENT - THANK YOU / PAIEMENT - MERCI", 800.0))
for dt in days():
    if random.random() < 0.15:
        visa.append((dt, random.choice(["TIM HORTONS #2231 LACOMBE AB", "COSTCO WHOLESALE W539 RED DEER AB", "SHELL C02221 LACOMBE AB"]), -round(random.uniform(5, 200), 2)))

# RBC CSV with both accounts in one file (that's how RBC exports multiple accounts)
with open(f"{OUT}/rbc_export.csv", "w") as f:
    f.write('"Account Type","Account Number","Transaction Date","Cheque Number","Description 1","Description 2","CAD$","USD$"\n')
    for dt, d1, d2, a in sorted(chq):
        f.write(f'Chequing,01234-5678901,{dt.month}/{dt.day}/{dt.year},,"{d1}","{d2}",{a},\n')
    for dt, desc, a in sorted(visa):
        f.write(f'Visa,4510123412349876,{dt.month}/{dt.day}/{dt.year},,"{desc}","",{a},\n')

# BMO Mastercard CSV (positive = purchase)
mc = []
for m in range(6, 11):
    dt = clamp(2026, m, 20)
    if start <= dt <= end:
        mc.append((dt, "SPOTIFY P1A2B3C4D5 STOCKHOLM", 12.99))
    dt = clamp(2026, m, 8)
    if start <= dt <= end:
        mc.append((dt, "SHAW/ROGERS INTERNET CALGARY AB", 115.00))
for dt in days():
    if random.random() < 0.05:
        mc.append((dt, "AMZN Mktp CA*AB12CD3E4", round(random.uniform(15, 90), 2)))
with open(f"{OUT}/bmo_mastercard.csv", "w") as f:
    f.write("Following data is valid as of 20261016145212 (Year/Month/Day/Hour/Minute/Second)\n\n")
    f.write("Item #,Card #,Transaction Date,Posting Date,Transaction Amount,Description\n")
    for i, (dt, desc, a) in enumerate(sorted(mc), 1):
        f.write(f"{i},'5191230011112222',{dt:%Y%m%d},{dt:%Y%m%d},{a},{desc}\n")

# BMO chequing as QFX: daycare biweekly + gym monthly
with open(f"{OUT}/bmo_chequing.qfx", "w") as f:
    f.write("OFXHEADER:100\nDATA:OFXSGML\n\n<OFX><SIGNONMSGSRSV1><SONRS><FI><ORG>BMO<FID>0001</FI></SONRS></SIGNONMSGSRSV1>")
    f.write("<BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>0123456789<ACCTTYPE>CHECKING</BANKACCTFROM><BANKTRANLIST>\n")
    d = date(2026, 6, 26)
    while d <= end:
        f.write(f"<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>{d:%Y%m%d}120000<TRNAMT>-410.00<NAME>[PR]LITTLE SPROUTS DAYCARE</STMTTRN>\n")
        d += timedelta(days=14)
    for m in range(7, 11):
        f.write(f"<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>2026{m:02d}03120000<TRNAMT>-64.99<NAME>[DN]GOODLIFE FITNESS</STMTTRN>\n")
    f.write("</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>\n")

# RBC-style chequing PDF for September (overlaps the CSV — must not double count)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
c = canvas.Canvas(f"{OUT}/rbc_chequing_sep.pdf", pagesize=letter)
c.setFont("Helvetica", 9)
y = 740
c.drawString(40, y, "Royal Bank of Canada   RBC Day to Day Banking"); y -= 14
c.drawString(40, y, "Your account number: 01234-5678901"); y -= 14
c.drawString(40, y, "Your account statement From August 15, 2026 to September 14, 2026"); y -= 30
c.drawString(40, y, "Details of your account activity"); y -= 16
c.drawString(40, y, "Date"); c.drawString(90, y, "Description")
c.drawRightString(390, y, "Withdrawals ($)"); c.drawRightString(470, y, "Deposits ($)"); c.drawRightString(560, y, "Balance ($)"); y -= 14
c.drawString(40, y, "15 Aug"); c.drawString(90, y, "Opening Balance"); c.drawRightString(560, y, "4,120.55"); y -= 14
bal = 4120.55
rows = [r for r in sorted(chq) if date(2026, 8, 15) <= r[0] <= date(2026, 9, 14)]
prev = None
for dt, d1, d2, a in rows:
    if y < 80:
        c.showPage(); c.setFont("Helvetica", 9); y = 740
        c.drawString(40, y, "Date"); c.drawString(90, y, "Description")
        c.drawRightString(390, y, "Withdrawals ($)"); c.drawRightString(470, y, "Deposits ($)"); c.drawRightString(560, y, "Balance ($)"); y -= 14
    bal += a
    if dt != prev:
        c.drawString(40, y, dt.strftime("%d %b").lstrip("0"))
    prev = dt
    c.drawString(90, y, d1)
    if d2:
        y -= 11; c.drawString(90, y, d2)
    c.drawRightString(390 if a < 0 else 470, y, f"{abs(a):,.2f}")
    c.drawRightString(560, y, f"{bal:,.2f}")
    y -= 14
c.drawString(40, y, "14 Sep"); c.drawString(90, y, "Closing Balance"); c.drawRightString(560, y, f"{bal:,.2f}")
c.save()

# BMO Mastercard style PDF (two dates, single Amount column, CR for credits)
c = canvas.Canvas(f"{OUT}/bmo_mc_sep.pdf", pagesize=letter)
c.setFont("Helvetica", 9); y = 740
c.drawString(40, y, "BMO Bank of Montreal  BMO CashBack Mastercard"); y -= 14
c.drawString(40, y, "Card number 5191 23** **** 2222"); y -= 14
c.drawString(40, y, "Statement period Aug. 21, 2026 - Sep. 20, 2026"); y -= 30
c.drawString(40, y, "Trans date"); c.drawString(100, y, "Posting date"); c.drawString(170, y, "Description"); c.drawRightString(560, y, "Amount ($)"); y -= 14
for dt, desc, a in sorted(mc):
    if date(2026, 8, 21) <= dt <= date(2026, 9, 20):
        c.drawString(40, y, dt.strftime("%b. %d")); c.drawString(100, y, dt.strftime("%b. %d")); c.drawString(170, y, desc)
        c.drawRightString(560, y, f"{a:,.2f}"); y -= 14
c.drawString(40, y, "Sep. 1"); c.drawString(100, y, "Sep. 2"); c.drawString(170, y, "PAYMENT RECEIVED - THANK YOU"); c.drawRightString(560, y, "300.00 CR".replace(" ", "")); y -= 14
c.save()
print("ok")
