"""Парсер за брошурата на Лидл (PDF с текстов слой) -> JSON с оферти.

Употреба:  python parse_brochure.py brochure.pdf offers.json
"""
import json
import re
import sys
from datetime import date

import pymupdf

PRICE_RE = re.compile(r"^(\d+[.,]\d{2})\s*€\*?$")
OLD_PRICE_RE = re.compile(r"^(\d+[.,]\d{2})\s*€$")
DISCOUNT_RE = re.compile(r"^-\s?(\d{1,2})\s?%$")
UNIT_RE = re.compile(r"(\d[\d.,\sx]*\s?(g|kg|ml|l|бр\.?|см|cm|m|м)\b.*?/\s?(опаковка|бр\.|кг|kg|l|м|комплект|чифт))", re.I)
VALID_RE = re.compile(r"(От|от)\s+(понеделник|вторник|сряда|четвъртък|петък|събота|неделя)?\s*(\d{1,2}\.\d{1,2}\.)(\s*до\s*(\d{1,2}\.\d{1,2}\.))?")
SKIP_RE = re.compile(r"^(\d+ бр\.|Акция|по-евтино|безплатно|на всеки втори|втория продукт|С Lidl Plus|по съотношение.*|сред търговските.*|Да се консумира|и умерено|-?\x07?годишна|възраст|18|\*.*)$")

BIG_PRICE_SIZE = 20     # голямата цена е ~40pt
TITLE_SIZE = (9.5, 13)  # марка/име ~10pt (до 12.5 при акцентни продукти)
DETAIL_SIZE = (7.5, 8.5)  # грамаж/описание ~8pt


def _num(s):
    return float(s.replace(",", "."))


def spans_of(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = s["text"].replace("\xa0", " ").strip()
                if t:
                    x0, y0, x1, y1 = s["bbox"]
                    out.append({"t": t, "x": x0, "y": y0, "x1": x1, "size": s["size"]})
    return out


def build_blocks(spans):
    """Групира имена (10pt) и детайли (8pt), подредени един под друг, в продуктови блокове."""
    cand = [s for s in spans
            if (TITLE_SIZE[0] <= s["size"] <= TITLE_SIZE[1] or DETAIL_SIZE[0] <= s["size"] <= DETAIL_SIZE[1])
            and not OLD_PRICE_RE.match(s["t"]) and not PRICE_RE.match(s["t"])
            and not DISCOUNT_RE.match(s["t"]) and not SKIP_RE.match(s["t"])
            and not s["t"].startswith(("Виж ", "* "))]
    cand.sort(key=lambda s: (round(s["x"] / 6), s["y"]))
    blocks = []
    for s in cand:
        b = next((b for b in blocks if abs(b["x"] - s["x"]) <= 10 and 0 <= s["y"] - b["bottom"] <= 16), None)
        if b:
            b["spans"].append(s)
            b["bottom"] = s["y"]
        else:
            blocks.append({"x": s["x"], "top": s["y"], "bottom": s["y"], "spans": [s]})
    # блокът трябва да започва с име (10pt)
    return [b for b in blocks if TITLE_SIZE[0] <= b["spans"][0]["size"] <= TITLE_SIZE[1]]


def match_cost(p, b):
    px, py = p["x"], p.get("anchor_y", p["y"])
    # блок над цената, в същата колона
    if abs(b["x"] - px) <= 15 and 0 <= py - b["bottom"] <= 60:
        return py - b["bottom"]
    # блок вляво от цената, на същата височина
    if 30 <= px - b["x"] <= 150 and -35 <= b["top"] - py <= 15:
        return abs(b["top"] - py) + 10
    # по-висок блок вляво, който обхваща реда на цената
    if 30 <= px - b["x"] <= 150 and b["top"] - 75 <= py <= b["bottom"] + 15:
        return 60 + abs(b["top"] - py) / 2
    return None


def block_to_fields(b):
    titles = [s["t"] for s in b["spans"] if TITLE_SIZE[0] <= s["size"] <= TITLE_SIZE[1]]
    details = []
    for s in b["spans"]:
        if DETAIL_SIZE[0] <= s["size"] <= DETAIL_SIZE[1]:
            if details and (details[-1].endswith("/") or details[-1].endswith("-")):
                details[-1] = details[-1].rstrip("-") + s["t"] if details[-1].endswith("-") else details[-1] + s["t"]
            else:
                details.append(s["t"])
    brand_parts = []
    for t in titles[:-1]:
        letters = [c for c in t if c.isalpha()]
        if letters and t.upper() == t:
            brand_parts.append(t)
        else:
            break
    name = titles[len(brand_parts):]
    unit = next((d for d in details if "/" in d and UNIT_RE.search(d)), None) \
        or next((d for d in details if "/" in d), None)
    if not unit and any(d.startswith("Цена за бр.") for d in details):
        unit = "бр."
    desc = [d for d in details if d != unit and not d.startswith("Цена за бр.")]
    join = lambda parts: re.sub(r"-\s+(?=[а-яa-z])", "", " ".join(parts)) or None
    return join(brand_parts), join(name), join(desc), unit


def parse_page(page, pno):
    spans = spans_of(page)
    prices = [s for s in spans if s["size"] >= BIG_PRICE_SIZE - 1 and PRICE_RE.match(s["t"])]
    validity = next((VALID_RE.search(s["t"]).group(0) for s in spans
                     if s["size"] >= 15 and VALID_RE.search(s["t"])), None)
    # Lidl Plus: голяма цена с етикет "С Lidl Plus" над нея; обикновената цена е малко по-горе
    lp_labels = [s for s in spans if "Lidl Plus" in s["t"]]
    plus_price = {}
    for p in prices:
        stacked_small = [q for q in prices if q["size"] < 30 <= p["size"]
                         and abs(q["x"] - p["x"]) < 15 and 0 < p["y"] - q["y"] < 80]
        if stacked_small or any(abs(l["x"] - p["x"]) < 15 and 0 < p["y"] - l["y"] < 40 for l in lp_labels):
            reg = [q for q in prices if q is not p and abs(q["x"] - p["x"]) < 15 and 0 < p["y"] - q["y"] < 80]
            if reg:
                plus_price[id(max(reg, key=lambda q: q["y"]))] = _num(PRICE_RE.match(p["t"]).group(1))
                p["drop"] = True
    for q in prices:  # котвата за съвпадение е долната (Lidl Plus) цена — там стои текстът
        if id(q) in plus_price:
            below = [p for p in prices if p.get("drop") and abs(p["x"] - q["x"]) < 15 and 0 < p["y"] - q["y"] < 80]
            q["anchor_y"] = below[0]["y"]
    prices = [p for p in prices if not p.get("drop")]
    blocks = build_blocks(spans)
    pairs = sorted(((c, i, j) for i, p in enumerate(prices) for j, b in enumerate(blocks)
                    if (c := match_cost(p, b)) is not None))
    used_p, used_b, match = set(), set(), {}
    for c, i, j in pairs:
        if i not in used_p and j not in used_b:
            used_p.add(i); used_b.add(j); match[i] = j

    for i, p in enumerate(prices):
        if i not in match:
            above = [k for k, q in enumerate(prices) if k in match and abs(q["x"] - p["x"]) < 15
                     and 0 < p["y"] - q["y"] < 90]
            if above:
                match[i] = match[max(above, key=lambda k: prices[k]["y"])]

    offers = []
    for i, p in enumerate(prices):
        px, py = p["x"], p["y"]
        near = [s for s in spans if py - 25 <= s["y"] <= py + 2 and px - 5 <= s["x"] <= px + 100]
        old = next((_num(OLD_PRICE_RE.match(s["t"]).group(1)) for s in near
                    if s["size"] < 12 and OLD_PRICE_RE.match(s["t"])), None)
        disc = next((int(DISCOUNT_RE.match(s["t"]).group(1)) for s in near if DISCOUNT_RE.match(s["t"])), None)
        price = _num(PRICE_RE.match(p["t"]).group(1))
        if disc is None and old and old > price:
            disc = round((1 - price / old) * 100)
        if i in match:
            brand, product, desc, unit = block_to_fields(blocks[match[i]])
        else:
            brand, product, desc, unit = None, None, None, None
        offers.append({
            "brand": brand, "product": product, "description": desc, "unit": unit,
            "price_eur": price, "old_price_eur": old, "discount_pct": disc,
            "lidl_plus_price_eur": plus_price.get(id(p)),
            "limited_stock": p["t"].endswith("*"), "valid": validity, "page": pno + 1,
        })
    return offers


def parse(pdf_path):
    doc = pymupdf.open(pdf_path)
    offers = []
    unmatched = 0
    for i, page in enumerate(doc):
        for o in parse_page(page, i):
            if o["product"]:
                offers.append(o)
            else:
                unmatched += 1
    # дедупликация (един продукт може да се повтаря)
    seen, uniq = set(), []
    for o in offers:
        k = (o["brand"], o["product"], o["unit"], o["price_eur"])
        if k not in seen:
            seen.add(k)
            uniq.append(o)
    return {"source": pdf_path.split("/")[-1], "parsed_on": date.today().isoformat(),
            "pages": len(doc), "count": len(uniq), "unmatched_prices": unmatched, "offers": uniq}


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else "offers.json"
    data = parse(src)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"{data['count']} оферти от {data['pages']} страници -> {dst}")
