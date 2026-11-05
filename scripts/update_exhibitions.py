#!/usr/bin/env python3
"""Build exhibitions.json for the Yalla Nihao app.

Runs weekly from the GitHub Action in .github/workflows/exhibitions.yml
(in the news repository), and can be run by hand:

    python3 scripts/update_exhibitions.py

Sources, read from their public schedule pages (neither site restricts
robots), politely, one request at a time:
  * National Exhibition and Convention Center (Shanghai), 国家会展中心（上海）
    https://www.neccsh.com/cecsh/exhibitioninfo/exhibitionlist.jspx
  * Shanghai New International Expo Centre, 上海新国际博览中心
    https://www.sniec.net/visit_exhibition.php?month=YYYY-MM

Everything else (Beijing, Guangzhou, Shenzhen, Yiwu...) is kept by hand
in exhibitions-manual.json. A manual entry with the same venue and start
date as a scraped one lends it its Arabic name and category.

Ended exhibitions are dropped, and the list runs to the end of next year.
Each run rebuilds the list from the sources, so additions, date changes and
cancellations all show up the following week. If a source fails, the entries it gave
last time are kept, so a bad week never empties the list.
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "exhibitions.json")
MANUAL = os.path.join(ROOT, "exhibitions-manual.json")
UA = "YallaNihaoExhibitions/1.0 (weekly schedule check; github.com/sajjadqazzaz-maker/sajjad-nihao-news)"
TODAY = dt.date.today()
# Read every month through the end of next year (2026 -> December 2027).
LAST_MONTH = dt.date(TODAY.year + 1, 12, 1)

# Category keys: the app shows each in Arabic (ExhibitionCategory).
CATEGORIES = [
    ("medical", r"医疗|医药|制药|康复|口腔|健康|生物|生化|分析|实验室|medical|pharma|health|dental|bio|analytica|lab"),
    ("auto", r"汽车|汽配|零部件|车展|automotive|auto|automechanika|vehicle"),
    ("electronics", r"电子|电路|半导体|显示|光电|电子元器件|electronic|semicon|display|PCB|nepcon"),
    ("energy", r"能源|储能|电力|光伏|氢能|太阳能|电池|power|energy|solar|battery|hydrogen|EP "),
    ("machinery", r"机械|机床|工程机械|设备|轴承|阀门|压力容器|泵|压缩机|工厂|发泡|材料|bauma|machine|bearing|valve|equipment|ComVac|PTC|factory|foam|material"),
    ("logistics", r"物流|运输|包装|cemat|logistic|pack"),
    ("furniture", r"家具|家居|木工|门窗|建材|装饰|furniture|home|interior|fenestration|building"),
    ("textile", r"纺织|服装|面料|鞋|皮革|textile|fabric|apparel|garment|shoe|leather|ITMA"),
    ("food", r"食品|饮料|茶|酒|餐饮|烘焙|水产|food|beverage|tea|wine|bakery|seafood"),
    ("beauty", r"美容|化妆|美博|beauty|cosmetic"),
    ("tech", r"智能|数据|云计算|人工智能|数字|机器人|科技|物联网|互联网|AI|data|cloud|digital|robot|tech|internet|IoT"),
    ("culture", r"文化|动漫|游戏|艺术|教育|culture|anime|game|art|education|WEPLAY"),
    ("trade", r"进口|出口|贸易|博览会|品牌|礼品|import|export|trade|gift|brand|fair"),
]


def classify(*names):
    text = " ".join(n for n in names if n)
    for key, pattern in CATEGORIES:
        if re.search(pattern, text, re.I):
            return key
    return "general"


def _tokens(text):
    text = re.sub(r"20\d\d|第[^届]{1,4}届|\d+", " ", text or "")
    latin = {w.lower() for w in re.findall(r"[A-Za-z]{2,}", text)} - {"asia", "china", "the", "of", "and", "international", "expo", "exhibition", "show", "fair", "shanghai"}
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    cjk = re.sub(r"中国|国际|上海|展览会|博览会|展览|亚洲|暨", "", cjk)
    return latin, {cjk[i:i + 2] for i in range(len(cjk) - 1)}


def similarity(a, b):
    la, ca = _tokens(" ".join(filter(None, (a.get("name_zh"), a.get("name_en")))))
    lb, cb = _tokens(" ".join(filter(None, (b.get("name_zh"), b.get("name_en")))))
    return 3 * len(la & lb) + len(ca & cb)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        raw = r.read()
    time.sleep(1.5)
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "ignore")


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def iso(s):
    y, m, d = (int(x) for x in re.split(r"[-/.]", s.strip()))
    return dt.date(y, m, d).isoformat()


# --- National Exhibition and Convention Center (Shanghai) --------------
def necc():
    base = "https://www.neccsh.com/cecsh/exhibitioninfo/exhibitionlist.jspx?v=1&pageNo={}"
    items, seen = [], set()
    first = fetch(base.format(1))
    m = re.search(r"totalPage\s*=\s*(\d+)", first)
    pages = min(int(m.group(1)) if m else 1, 12)  # sorted nearest first
    for page in range(1, pages + 1):
        h = first if page == 1 else fetch(base.format(page))
        rows = re.findall(r"<tr>(.*?)</tr>", h, re.S)
        future_here = 0
        for row in rows:
            name = re.search(r'class="exhibit-name">(.*?)</div>', row, re.S)
            dates = re.search(r"(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})", row)
            if not (name and dates):
                continue
            zh = clean(name.group(1))
            start, end = iso(dates.group(1)), iso(dates.group(2))
            if end < TODAY.isoformat() or start > LAST_MONTH.replace(day=31).isoformat() or (zh, start) in seen:
                continue
            future_here += 1
            seen.add((zh, start))
            site = re.search(r'官网：\s*<a href="([^"]+)"', row)
            items.append({
                "id": "necc-" + hashlib.sha1(f"{zh}|{start}".encode()).hexdigest()[:10],
                "name_zh": zh,
                "start": start, "end": end,
                "city": "shanghai",
                "venue_zh": "国家会展中心（上海）",
                "venue_en": "National Exhibition and Convention Center (Shanghai)",
                "url": site.group(1).strip() if site else "",
                "source": "necc",
            })
        if page > 2 and future_here == 0:
            break
    return items


# --- Shanghai New International Expo Centre ----------------------------
def sniec_month(lang, month):
    prefix = "cn/" if lang == "zh" else ""
    h = fetch(f"https://www.sniec.net/{prefix}visit_exhibition.php?month={month}")
    out = {}
    for rel, body in re.findall(r'<li rel="(\d+)">(.*?)(?=<li rel="\d+">|</ul>\s*<!--|\Z)', h, re.S):
        name = re.search(r"<h1>(.*?)</h1>", body, re.S)
        dates = re.search(r"(\d{4}/\d{2}/\d{2})\s*-\s*(\d{4}/\d{2}/\d{2})", body)
        if not (name and dates):
            continue
        web = re.search(r"(?:Web|网址)[:：]?(?:&nbsp;|\s)*(?:</?[^>]+>|\s)*(?:<a[^>]*href=\"([^\"]+)\"|(https?://[^\s<]+|www\.[^\s<]+))", body)
        url = (web.group(1) or web.group(2)) if web else ""
        if url and not url.startswith("http"):
            url = "https://" + url
        out[rel] = {"name": clean(name.group(1)), "start": iso(dates.group(1)), "end": iso(dates.group(2)), "url": url}
    return out


def sniec():
    items, seen = [], set()
    d = TODAY.replace(day=1)
    while d <= LAST_MONTH:
        month = f"{d.year}-{d.month:02d}"
        zh, en = sniec_month("zh", month), sniec_month("en", month)
        for rel in set(zh) | set(en):
            a, b = zh.get(rel), en.get(rel)
            base = a or b
            if base["end"] < TODAY.isoformat() or rel in seen:
                continue
            seen.add(rel)
            items.append({
                "id": f"sniec-{rel}",
                "name_zh": a["name"] if a else "",
                "name_en": b["name"] if b else "",
                "start": base["start"], "end": base["end"],
                "city": "shanghai",
                "venue_zh": "上海新国际博览中心",
                "venue_en": "Shanghai New International Expo Centre",
                "url": (b or a)["url"],
                "source": "sniec",
            })
        d = (d + dt.timedelta(days=32)).replace(day=1)
    return items


def main():
    previous = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for e in json.load(f).get("exhibitions", []):
                previous.setdefault(e.get("source"), []).append(e)

    scraped, status = [], {}
    for name, fn in (("necc", necc), ("sniec", sniec)):
        try:
            got = fn()
            if not got:
                raise RuntimeError("no entries found (page layout changed?)")
            scraped += got
            status[name] = f"ok ({len(got)})"
        except Exception as exc:  # keep last week's entries for this source
            kept = [e for e in previous.get(name, []) if e["end"] >= TODAY.isoformat()]
            scraped += kept
            status[name] = f"FAILED: {exc} — kept {len(kept)} from last run"
            print(f"warning: {name}: {exc}", file=sys.stderr)

    manual = []
    if os.path.exists(MANUAL):
        with open(MANUAL, encoding="utf-8") as f:
            manual = json.load(f).get("exhibitions", [])

    # A manual entry for an automatic venue only lends its Arabic name,
    # category and link to the scraped exhibition it describes (same venue,
    # same start date, similar name); it is never listed on its own.
    auto_venues = {"国家会展中心（上海）", "上海新国际博览中心"}
    merged = list(scraped)
    for m in manual:
        if m.get("venue_zh") in auto_venues:
            twin = max(
                (e for e in scraped if e["venue_zh"] == m["venue_zh"] and e["start"] == m.get("start")),
                key=lambda e: similarity(m, e), default=None)
            if twin and similarity(m, twin) > 0:
                for k in ("name_ar", "category", "url"):
                    if m.get(k):
                        twin[k] = m[k]
            continue
        merged.append({**m, "source": "manual"})

    result = []
    for e in merged:
        if e["end"] < TODAY.isoformat() or e["start"] > LAST_MONTH.replace(day=31).isoformat():
            continue
        if not e.get("category"):
            e["category"] = classify(e.get("name_zh"), e.get("name_en"))
        result.append({k: v for k, v in e.items() if v not in ("", None)})
    result.sort(key=lambda e: (e["start"], e.get("city", ""), e.get("name_zh", "")))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"updated": TODAY.isoformat(), "sources": status, "exhibitions": result},
                  f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{len(result)} exhibitions written; sources: {status}")


if __name__ == "__main__":
    main()
