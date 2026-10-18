"""Sort transactions into spending categories.

Order of decisions for each transaction:
  1. a category you set on that one transaction
  2. a rule you saved for that payee ("always put SOBEYS in Groceries")
  3. the built-in keyword list below (Canadian merchants, first match wins)
  4. a recurring bill from the Bills tab -> "Bills & services"
  5. "Other"
"""

import hashlib
import re

import analysis as A

# id, name, group. group: "spend" (counted), "bills" (counted only if you turn bills on),
# "none" (never spending: transfers, card payments, income)
CATEGORIES = [
    ("groceries", "Groceries", "spend"),
    ("restaurants", "Restaurants & takeout", "spend"),
    ("coffee", "Coffee & snacks", "spend"),
    ("gas", "Gas & fuel", "spend"),
    ("auto", "Car & transport", "spend"),
    ("shopping", "Shopping", "spend"),
    ("home", "Home & garden", "spend"),
    ("health", "Health & pharmacy", "spend"),
    ("subscriptions", "Subscriptions & apps", "spend"),
    ("entertainment", "Entertainment", "spend"),
    ("travel", "Travel", "spend"),
    ("kids", "Kids & childcare", "spend"),
    ("personal", "Personal care", "spend"),
    ("pets", "Pets", "spend"),
    ("gifts", "Gifts & donations", "spend"),
    ("sent", "E-transfers sent", "spend"),
    ("cash", "Cash", "spend"),
    ("fees", "Fees & interest", "spend"),
    ("government", "Government & taxes", "spend"),
    ("other", "Other", "spend"),
    ("housing", "Housing", "bills"),
    ("utilities", "Utilities", "bills"),
    ("phone", "Phone & internet", "bills"),
    ("insurance", "Insurance", "bills"),
    ("billsvc", "Bills & services", "bills"),
    ("transfer", "Transfers between accounts", "none"),
    ("cardpay", "Credit card payments", "none"),
    ("income", "Income & money in", "none"),
]
CAT_IDS = {c[0] for c in CATEGORIES}
GROUP = {c[0]: c[2] for c in CATEGORIES}

# (regex on the upper-cased description, category). First match wins, so specific
# patterns (COSTCO GAS) sit above general ones (COSTCO).
RULES = [
    # never-spending first
    (r"PAYMENT.{0,12}THANK YOU|PAIEMENT.{0,6}MERCI|PAYMENT RECEIVED|AUTOPAY|PAYMENT - THANK", "cardpay"),
    (r"E-?TRANSFER.{0,20}(SENT|SEND|ENVOI)|SEND E-?TFR|INTERAC E-?TRANSFER TO|E-TFR TO|ETRANSFER SENT", "sent"),
    (r"E-?TRANSFER|E-?TFR|AUTODEPOSIT|INTERAC.{0,10}(RECEIVED|DEPOSIT)", "income"),
    (r"PAYROLL|PAY ?ROLL|SALARY|DIRECT DEP|\bDIR DEP|CANADA (CHILD|CCB)|CCB\b|GST ?CREDIT|GST/HST|CANADA FED|FED(ERAL)? REFUND|TAX REFUND|INTEREST (EARNED|PAID TO)", "income"),
    # gas before groceries/shopping (Costco Gas, Canadian Tire Gas+, Co-op Gas)
    (r"COSTCO GAS|CDN TIRE GAS|CANADIAN TIRE GAS|GAS ?\+|GAS BAR|CO-?OP GAS|PETRO-?CAN|PETRO CANADA|\bSHELL\b|ESSO|HUSKY|\bMOBIL\b|CHEVRON|FAS GAS|CENTEX|DOMO GAS|ULTRAMAR|PIONEER|SUNCOR|7-ELEVEN.{0,10}GAS|GAS STATION|\bFUEL\b|\bGAS #|CIRCLE K", "gas"),
    (r"TIM HORTON|STARBUCKS|SECOND CUP|BLENZ|VAN HOUTTE|COFFEE|CAFE|CAFÉ|ESPRESSO|DAVIDSTEA|BAKERY|DONUT|KRISPY|\bDQ\b|DAIRY QUEEN|COLD STONE|MARBLE SLAB|BOOSTER JUICE|JUGO JUICE", "coffee"),
    (r"SKIP ?THE ?DISHES|SKIPTHEDISHES|DOORDASH|UBER ?EATS|UBEREATS|MCDONALD|A ?& ?W|\bA&W\b|WENDY|BURGER KING|SUBWAY|KFC|TACO|PIZZA|DOMINO|BOSTON PIZZA|EARLS|CACTUS CLUB|JOEY|BROWNS SOCIAL|MOXIE|DENNY|SMITTY|HUMPTY|BOOTLEGGER|WHITE SPOT|KELSEY|MONTANA|EAST SIDE MARIO|OLIVE GARDEN|RED ROBIN|APPLEBEE|CHIPOTLE|FIVE GUYS|MARY BROWN|POPEYES|HARVEY|FATBURGER|\bOPA\b|MR SUB|QUIZNOS|PITA PIT|FRESHII|SUSHI|RAMEN|\bPHO\b|THAI|NOODLE|GRILL|BISTRO|DINER|RESTAURANT|\bPUB\b|TAPHOUSE|BREWING|BREWERY|STEAKHOUSE|BBQ|WINGS|BURGER|CHICKEN|SHAWARMA|DONAIR|KITCHEN|EATERY|CANTINA", "restaurants"),
    (r"INSURANCE|\bINS\b|ASSURANCE|INTACT|AVIVA|WAWANESA|BELAIR|TD INSURANCE|SGI|AMA INS|COOPERATORS|CO-OPERATORS|MANULIFE|SUN LIFE|CANADA LIFE|GREAT-WEST|BLUE CROSS|DESJARDINS INS|ECONOMICAL", "insurance"),
    (r"DAYCARE|DAY CARE|CHILD ?CARE|PRESCHOOL|SCHOOL|SCHOLASTIC|TUTOR|KUMON|SYLVAN|MINOR HOCKEY|HOCKEY|SOCCER|SWIM|DANCE|GYMNASTIC|SPORTS REG|REGISTRATION|\bCAMP\b|BABYSIT|TOYS R US|MASTERMIND", "kids"),
    (r"SOBEYS|SAFEWAY|SUPERSTORE|SUPER STORE|NO FRILLS|NOFRILLS|SAVE-?ON|\bIGA\b|FRESHCO|FOOD BASICS|\bMETRO\b|LOBLAW|ZEHRS|T ?& ?T|REAL CDN|WHOLE FOODS|SPROUTS|FARM BOY|CO-?OP|COOP\b|FREEDOM MART|BULK BARN|COSTCO|WALMART|WAL-MART|GROCER|MARKET\b|MEAT|BUTCHER|FRUIT|PRODUCE|LIQUOR|WINE|BEER|ACE LIQUOR|LIQUOR DEPOT|WINE AND BEYOND|BOTTLE", "groceries"),
    # bills
    (r"MORTGAGE|\bRENT\b|PROPERTY TAX|TAX ROLL|CONDO FEE|STRATA|LANDLORD", "housing"),
    (r"UTILNET|UTILITY|ECHO|ENMAX|EPCOR|ATCO|DIRECT ENERGY|JUST ENERGY|ENCOR|FORTIS|CITY OF|TOWN OF|COUNTY OF|\bWATER\b|HYDRO|ELECTRIC\b", "utilities"),
    (r"ROGERS|TELUS|\bBELL\b|BELL CANADA|BELL MOBILITY|SHAW|KOODO|FIDO|VIRGIN|FREEDOM MOBILE|PUBLIC MOBILE|CHATR|LUCKY MOBILE|STARLINK|XPLORE|INTERNET|WIRELESS|MOBILITY", "phone"),
    # everything else
    (r"NETFLIX|SPOTIFY|DISNEY|CRAVE|APPLE\.COM|APPLE COM|ITUNES|GOOGLE \*|GOOGLE PLAY|GOOGLE ONE|YOUTUBE|PRIME VIDEO|AMAZON PRIME|AMZN PRIME|PRIME MEMBER|MICROSOFT|XBOX|PLAYSTATION|SONY|NINTENDO|STEAM|PATREON|AUDIBLE|KINDLE|DROPBOX|ADOBE|ANTHROPIC|CLAUDE\.AI|OPENAI|CHATGPT|PARAMOUNT|SIRIUS|DAZN|SPORTSNET|\bTSN\b|PELOTON|ICLOUD|GITHUB|NAME-?CHEAP|GODADDY", "subscriptions"),
    (r"CINEPLEX|LANDMARK CINEMA|TICKETMASTER|EVENTBRITE|OILERS|ROGERS PLACE|FLAMES|LIVE NATION|GOLF|BOWL|ARCADE|MUSEUM|ZOO|WATERPARK|\bSKI\b|THEATRE|THEATER|CONCERT|CASINO|LOTTO|LOTTERY|AGLC|PLAYALBERTA", "entertainment"),
    (r"AIR CANADA|WESTJET|FLAIR|SWOOP|LYNX|PORTER|SUNWING|TRANSAT|EXPEDIA|BOOKING\.COM|HOTELS\.COM|AIRBNB|HOTEL|MOTEL|\bINN\b|RESORT|MARRIOTT|HILTON|SANDMAN|BEST WESTERN|HOLIDAY INN|SUPER 8|TRAVEL|AIRPORT|YEG|YYC|DUTY FREE|ROYALTON|ENTERPRISE RENT|BUDGET RENT|HERTZ|AVIS|NATIONAL CAR", "travel"),
    (r"HOME DEPOT|HOMEDEPOT|RONA|LOWE'?S|CANADIAN TIRE|CDN TIRE|IKEA|PEAVEY|PRINCESS AUTO|HOME HARDWARE|TSC STORES|\bKENT\b|STRUCTUBE|WAYFAIR|BED BATH|HOMESENSE|HOME SENSE|LEON'?S|THE BRICK|SLEEP COUNTRY|GARDEN|GREENHOUSE|NURSERY|HARDWARE|LUMBER|PLUMB|HVAC|FURNACE|ELECTRICIAN|LAWN|SNOW|PEST|CLEANING", "home"),
    (r"UBER(?! ?EATS)|LYFT|TAXI|\bCAB\b|PARKING|IMPARK|INDIGO PARK|PARK ?PLUS|TRANSIT|\bETS\b|CALGARY TRANSIT|CAR WASH|AUTOWASH|JIFFY|MR\.? LUBE|LUBE|TIRE|KAL TIRE|OK TIRE|FOUNTAIN TIRE|MIDAS|HONDA|TOYOTA|\bFORD\b|AUTO|REGISTRY|\bAMA\b|ALBERTA MOTOR|AUTOMOTIVE|COLLISION|NAPA|PART SOURCE|PARTSOURCE|CARQUEST", "auto"),
    (r"SHOPPERS DRUG|SHOPPERS|REXALL|LONDON DRUGS|PHARMA|PHARMACY|DRUG MART|DRUGS|GUARDIAN|PHARMASAVE|DENTAL|DENTIST|ORTHO|OPTOM|EYE ?CARE|VISIONMAX|SPECSAVERS|CLEARLY|PHYSIO|CHIRO|MASSAGE|CLINIC|MEDICAL|HOSPITAL|\bLAB\b|DYNALIFE|PSYCH|COUNSEL|WELLNESS", "health"),
    (r"PETSMART|PET VALU|GLOBAL PET|PETLAND|\bPETS?\b|\bVET\b|VETERINAR|ANIMAL HOSP|BOSLEY", "pets"),
    (r"SALON|BARBER|HAIR|\bSPA\b|NAILS|ESTHETIC|SEPHORA|ULTA|LUSH|BATH & BODY|BATH AND BODY|COSMETIC|GOODLIFE|FITNESS|\bGYM\b|ANYTIME FIT|PLANET FITNESS|YOGA|CROSSFIT|ORANGETHEORY|F45", "personal"),
    (r"CANADAHELPS|CHARITY|DONATION|FOUNDATION|UNITED WAY|RED CROSS|SALVATION ARMY|CHURCH|PARISH|GOFUNDME|FLOWERS|FLORIST|1-800-FLOWERS|HALLMARK|GIFT", "gifts"),
    (r"AMAZON|AMZN|BEST BUY|BESTBUY|STAPLES|WINNERS|MARSHALLS|SPORT ?CHEK|ATMOSPHERE|MARK'?S|OLD NAVY|\bGAP\b|H ?& ?M|ZARA|UNIQLO|AMERICAN EAGLE|ARDENE|SKECHERS|FOOTLOCKER|FOOT LOCKER|SPORT ?EXPERTS|DOLLARAMA|DOLLAR TREE|DOLLAR|INDIGO|CHAPTERS|MICHAELS|HOBBY|TEMU|SHEIN|ALIEXPRESS|ETSY|EBAY|PAYPAL|SHOPIFY|APPLE STORE|THE SOURCE|LULULEMON|ROOTS|SIMONS|HUDSON'?S BAY|\bTHE BAY\b|GIANT TIGER|LONDON DRUGS|VISIONS|MEMORY EXPRESS|CANADA COMPUTERS|EB GAMES|GAMESTOP|\bMALL\b", "shopping"),
    (r"\bATM\b|\bABM\b|CASH WITHDRAWAL|WITHDRAWAL - CASH|WITHDRAWAL CASH|CASH ADV", "cash"),
    (r"SERVICE CHARGE|MONTHLY FEE|ACCOUNT FEE|ANNUAL FEE|PLAN FEE|NSF|OVERDRAFT|OD INTEREST|INTEREST CHARGE|PURCHASE INTEREST|CASH ADVANCE INTEREST|FOREIGN (EXCHANGE|TRANSACTION) FEE|\bFEE\b|INTEREST", "fees"),
    (r"\bCRA\b|CANADA REVENUE|REC(EIVER)? GEN|RECEIVER GENERAL|GOVT|GOVERNMENT|SERVICE ALBERTA|ALBERTA REG|PASSPORT|COURT|\bFINE\b|TICKET PAYMENT", "government"),
]
COMPILED = [(re.compile(p), c) for p, c in RULES]


def keyword_category(desc):
    d = " " + str(desc).upper() + " "
    for rx, cat in COMPILED:
        if rx.search(d):
            return cat
    return None


def txn_id(t, n):
    raw = f"{t['account']}|{t['date']}|{t['amount']}|{t['desc']}|{n}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def categorize(txns, bills, settings):
    """Returns a list of rows: {id, date, desc, amount, account, merchant, cat, why}."""
    sp = settings.get("spend", {})
    tx_over = sp.get("tx", {})
    rules = sp.get("rules", {})
    bill_keys = {" ".join(b["key"].split()[:2]) for b in bills if b.get("include")}
    seen = {}
    out = []
    for t in txns:
        base = (t["account"], t["date"], t["amount"], t["desc"])
        n = seen.get(base, 0)
        seen[base] = n + 1
        tid = txn_id(t, n)
        # payee = first two words, so "Costco Wholesale Red Deer" and "Costco Wholesale" group together
        merchant = " ".join(A.merchant_key(t["desc"]).split()[:2]) or "unknown"
        why = "you set this one"
        cat = tx_over.get(tid)
        if cat not in CAT_IDS:
            cat, why = rules.get(merchant), "your rule for this payee"
        if cat not in CAT_IDS:
            cat, why = None, "built-in list"
            if A.is_internal(t):
                cat = "transfer"
            elif A.is_card_payment(t) or (t["kind"] == "credit" and t["amount"] > 0 and re.search(r"PAYMENT|PAIEMENT", t["desc"], re.I)):
                cat = "cardpay"
            else:
                cat = keyword_category(t["desc"])
            if cat is None and merchant in bill_keys:
                cat, why = "billsvc", "recurring bill"
            if cat is None:
                cat, why = ("income", "money in") if t["amount"] > 0 and t["kind"] == "bank" else ("other", "no match")
        # Money into chequing that matched a shop by name is only a refund if it says so;
        # otherwise it's income (stops "HEALTH SERVICES PAYROLL" landing in Health).
        if why == "built-in list" and t["amount"] > 0 and t["kind"] == "bank" and GROUP.get(cat) != "none" \
                and not re.search(r"REFUND|RETURN|REVERS|RFND|CREDIT ADJ|RBT|REBATE", t["desc"], re.I):
            cat, why = "income", "money in"
        out.append({"id": tid, "date": t["date"], "desc": t["desc"], "amount": t["amount"],
                    "account": t["account"], "merchant": merchant, "cat": cat, "why": why})
    return out


def payload(txns, bills, settings, accounts):
    sp = settings.get("spend", {})
    rows = categorize(txns, bills, settings)
    return {
        "categories": [{"id": c, "name": n, "group": g} for c, n, g in CATEGORIES],
        "include_bills": bool(sp.get("include_bills", False)),
        "hidden": [c for c in sp.get("hidden", []) if c in CAT_IDS],
        "rules": sp.get("rules", {}),
        "rows": rows,
        "accounts": [{"id": a["id"], "label": a["label"], "first": a["first"], "last": a["last"],
                      "ignore": a["ignore"]} for a in accounts],
    }
