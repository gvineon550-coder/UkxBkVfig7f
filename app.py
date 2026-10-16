#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IPTV: parse + categorize + EPG + RU-NAME + check + build. One file."""
import asyncio, aiohttp, json, re, sys, gzip, io, random, subprocess, time, os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
from urllib.parse import urlparse
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
MSK = timezone(timedelta(hours=3))
def now_msk(): return datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")

def _load(p):
    p = Path(p)
    if p.exists():
        try: return json.loads(p.read_text("utf-8"))
        except Exception as e: print(f"  {p}: {e}")
    return {}

CFG = _load(ROOT / "config.json")
UA_POOL = _load(ROOT / "useragents.json")
OVERRIDES = _load(ROOT / "overrides.json")
BLOCKLIST = _load(ROOT / "blocklist.json")

CATS = CFG.get("category_order", ["Федеральные","Новостные","Региональные","Кино","Сериалы","Спорт","Познавательные","Документальные","Музыкальные","Развлекательные","Религиозные","Радио","Разное"])
EMOJI = CFG.get("category_emoji", {})
CAT_KW = CFG.get("category_keywords", {})
UA_W = CFG.get("ua_weights", {})
WL_GROUPS_ORDER = CFG.get("whitelist_groups_order", ["Kinowalk", "Rutube"])

API_VER = 3  # v3: отдельная загрузка logos.json

RAW_KEYWORDS = (
    ("нтв", "Федеральные"), ("рен тв", "Федеральные"), ("рен-тв", "Федеральные"),
    ("рен ", "Федеральные"), ("стс", "Федеральные"), ("тнт", "Федеральные"),
    ("channel one", "Федеральные"), ("channel one europa", "Федеральные"),
    ("channel one hd", "Федеральные"), ("1tv", "Федеральные"),
    ("ntv (russia)", "Федеральные"), ("ntv russia", "Федеральные"),
    ("ntv mir", "Федеральные"), ("ntv pravo", "Федеральные"),
    ("tv-3", "Федеральные"), ("tv 3", "Федеральные"),
    ("tnt international", "Федеральные"), ("tnt4", "Федеральные"),
    ("friday!", "Федеральные"), ("friday international", "Федеральные"),
    ("sts international", "Федеральные"), ("sts kids", "Федеральные"),
    ("mir kazakhstan", "Федеральные"), ("mir belarus", "Федеральные"),
    ("о! international", "Федеральные"), ("o! international", "Федеральные"),
    ("звезда плюс", "Федеральные"), ("солнце", "Федеральные"),
    ("тв центр", "Федеральные"), ("tv centr", "Федеральные"),
    ("rtr-planeta", "Новостные"), ("rtr planeta", "Новостные"),
    ("красная линия", "Новостные"), ("вместе-рф", "Новостные"),
    ("вместе рф", "Новостные"), ("дождь", "Новостные"),
    ("соловьёв", "Новостные"), ("соловьев", "Новостные"),
    ("думатв", "Новостные"), ("время", "Новостные"),
    ("channel 8", "Региональные"), ("8 канал", "Региональные"),
    ("yalta tv", "Региональные"), ("kentron tv", "Региональные"),
    ("бст", "Региональные"), ("беларусь 4", "Региональные"),
    ("волга", "Региональные"), ("губерния", "Региональные"),
    ("дагестан", "Региональные"), ("мир белогорья", "Региональные"),
    ("мир белагорья", "Региональные"), ("санкт-петербург", "Региональные"),
    ("своё тв", "Региональные"), ("свое тв", "Региональные"),
    ("север (", "Региональные"), ("тдк 42", "Региональные"),
    ("тнв-планета", "Региональные"), ("тнв", "Региональные"),
    ("югра-тв", "Региональные"), ("югра тв", "Региональные"),
    ("тооку", "Региональные"), ("tooku", "Региональные"),
    (".sci-fi", "Кино"), ("sci-fi", "Кино"), ("scifi", "Кино"),
    ("sony тв", "Кино"), ("sony tv", "Кино"),
    ("дом кино international", "Кино"), ("душевное", "Кино"),
    ("нст", "Кино"), ("nst", "Кино"), ("победа", "Кино"),
    ("прима", "Кино"), ("black", "Кино"), ("red", "Кино"),
    ("start air", "Кино"), ("start world", "Кино"),
    ("кинопремьера", "Кино"), ("настоящее страшное", "Кино"),
    ("премиальное", "Кино"), ("шокирующее", "Кино"),
    ("ani (", "Детские"), ("anime", "Детские"),
    ("mult international", "Детские"), ("мульт international", "Детские"),
    ("nicktoons", "Детские"), ("sevimli tv", "Детские"), ("tiji", "Детские"),
    ("детский", "Детские"), ("мультиландия", "Детские"), ("супергерои", "Детские"),
    ("big planet", "Познавательные"), ("love nature", "Познавательные"),
    ("travelxp", "Познавательные"), ("travel channel", "Познавательные"),
    ("zooпарк", "Познавательные"), ("myzen", "Познавательные"),
    ("большая азия", "Познавательные"), ("в мире животных", "Познавательные"),
    ("дикая охота", "Познавательные"), ("дикая рыбалка", "Познавательные"),
    ("дикий", "Познавательные"), ("живая планета", "Познавательные"),
    ("живая природа", "Познавательные"), ("загородная жизнь", "Познавательные"),
    ("инсайт", "Познавательные"), ("медздрав", "Познавательные"),
    ("medzdrav", "Познавательные"),
    ("лапки live", "Познавательные"), ("моя планета", "Познавательные"),
    ("охота и рыбалка", "Познавательные"), ("охотник и рыболов", "Познавательные"),
    ("приключения hd", "Познавательные"), ("просвещение", "Познавательные"),
    ("рыбалка и охота", "Познавательные"), ("усадьба", "Познавательные"),
    ("rt д", "Документальные"), ("rtd", "Документальные"),
    ("неизвестная россия", "Документальные"),
    ("мультимузыка", "Музыкальные"), ("hit", "Музыкальные"),
    ("бобер", "Развлекательные"), ("magnat", "Развлекательные"),
    ("o! (", "Развлекательные"), ("o! (576", "Развлекательные"),
    ("витрина тв", "Развлекательные"), ("мой мир", "Развлекательные"),
    ("продвижение", "Развлекательные"), ("рыжий", "Развлекательные"),
    ("салям", "Развлекательные"), ("сарафан", "Развлекательные"),
    ("smile tv", "Развлекательные"),
    ("теледом", "Развлекательные"), ("тонус тв", "Развлекательные"),
    ("точка отрыва", "Развлекательные"), ("ювелирочка", "Развлекательные"),
    ("мама", "Развлекательные"), ("ретро", "Развлекательные"),
    ("tv pro", "Развлекательные"),
    ("angel tv", "Религиозные"), ("радость моя", "Религиозные"),
    ("aqlvoy", "Разное"),
)

CITY_HINTS = (
    "вологда","грозный","белгород","уфа","башкортостан","сочи","казань","татарстан",
    "краснодар","ростов","омск","самара","пермь","саратов","воронеж","тула","иркутск",
    "томск","тверь","мурманск","архангельск","калининград","ставрополь","махачкала",
    "владикавказ","якутск","хабаровск","владивосток","югра","сургут","тюмень","челябинск",
    "оренбург","крым","севастополь","донбасс","астрахань","волгоград","липецк","курск",
    "брянск","смоленск","псков","новгород","карелия","коми","бурятия","тыва","хакасия",
    "алтай","камчатка","сахалин","приморье","амур","магадан","салехард","нижневартовск",
    "курган","магнитогорск","сибирь","урал","дагестан","ингушетия","осетия","кабард",
    "калмык","марий","мордов","чуваш","удмурт","башк","рязан","тамбов","пенз",
    "ульянов","чебоксар","саранск","ставропол","элист","барнаул","кемеров","новокузнец",
    "краснояр","абакан","кызыл","горно-алтай","биробидж","благовещен","южно-сахалин",
    "петропавлов","анадыр","нальчик","черкесск","майкоп","петрозаводск","сыктывкар",
    "архангел","череповец","кострома","иваново","владимир","калуга","ярослав",
    "рыбинск","орёл","симферополь"
)

RAW_DELETE = ("латвия", "latvia", "360 latvia")

NOISE = {"тв","tv","hd","uhd","fhd","sd","4k","1080p","720p","full","канал","channel","online","live","stream"}
def norm_name(s):
    n = (s or "").lower().replace("ё","е").replace("й","и")
    n = re.sub(r'\(.*?\)|\[.*?\]', ' ', n)
    n = re.sub(r'[^a-zа-я0-9\s]', ' ', n)
    return " ".join(w for w in n.split() if w and w not in NOISE)

TRANSLIT = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya","ї":"i","є":"e","і":"i","ґ":"g"}
def translit(s): return "".join(TRANSLIT.get(c, c) for c in (s or ""))

def has_cyrillic(s): return bool(re.search(r'[а-яА-ЯёЁ]', s or ''))

def extract_suffix_from_tvg(tvg_id):
    if not tvg_id or "@" not in tvg_id: return ""
    sfx = tvg_id.split("@", 1)[1]
    low = sfx.lower()
    if low == "sd": return ""
    if low.startswith("plus"):
        num = low.replace("plus", "")
        if num.isdigit(): return f"+{num}"
    if low in ("hd", "fhd", "uhd", "4k", "1080p", "720p"):
        return sfx.upper()
    return sfx

ATTR_RE = re.compile(r'([A-Za-z0-9_\-]+)="([^"]*)"')

def split_extinf(line):
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"': in_quote = not in_quote
        elif ch == ',' and not in_quote:
            return line[:i], line[i+1:].strip()
    return line, ""

def parse_m3u(text):
    out, name, attrs = [], None, {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line: continue
        if line.startswith("#EXTINF"):
            m = re.match(r'#EXTINF:-?\d+\s*(.*)$', line)
            if m:
                attrs_str, name = split_extinf(m.group(1))
                attrs = dict(ATTR_RE.findall(attrs_str))
        elif line.startswith("#"): continue
        else:
            if name: out.append({"name": name, "url": line, "attrs": attrs})
            name, attrs = None, {}
    return out

PRIV_RE = re.compile(r'^https?://(localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)')
FAKE_RE = re.compile(r'\.(local|test|invalid|example|localhost)(\b|:|/)', re.I)
BAD_EXT = tuple(BLOCKLIST.get("bad_extensions", [".html",".zip",".exe",".apk",".jpg",".png",".gif"]))
BAD_DOMAINS = tuple(BLOCKLIST.get("bad_domains_suffix", []))

def is_junk_url(u):
    if not u or not re.match(r'^https?://', u): return True
    if PRIV_RE.search(u): return True
    if FAKE_RE.search(u): return True
    if u.lower().split("?")[0].endswith(BAD_EXT): return True
    host = urlparse(u).netloc.lower().split(":")[0]
    for bd in BAD_DOMAINS:
        if host == bd or host.endswith("." + bd):
            return True
    return False

def load_whitelist():
    p = ROOT / CFG.get("whitelist_file", "whitelist.m3u")
    if not p.exists(): print(f"[wl] {p} нет"); return []
    out = []
    for e in parse_m3u(p.read_text("utf-8")):
        a = e["attrs"]
        out.append({
            "name": e["name"], "url": e["url"],
            "tvg_id": a.get("tvg-id",""),
            "tvg_name": a.get("tvg-name",""),
            "logo": a.get("tvg-logo",""),
            "group": a.get("group-title","Whitelist"),
            "source": "whitelist", "prio": -1,
            "is_whitelist": True,
        })
    print(f"[wl] {len(out)} каналов")
    return out

def download(url, timeout=180, gz=False):
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r: data = r.read()
        if data[:2] == b'\x1f\x8b' or gz:
            try: data = gzip.decompress(data)
            except: pass
        return data.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  dl {url}: {e}"); return None

def build_epg_map(xml_text):
    bn, bt, bp, ic, ru = {}, {}, {}, {}, {}
    if not xml_text: return bn, bt, bp, ic, ru
    try:
        stream = io.BytesIO(xml_text.encode("utf-8"))
        for _, el in ET.iterparse(stream, events=("end",)):
            if el.tag == "channel":
                cid = el.get("id")
                if cid:
                    icon = el.find("icon")
                    if icon is not None and icon.get("src"): ic[cid] = icon.get("src")
                    for dn in el.findall("display-name"):
                        if dn.text:
                            orig = dn.text.strip()
                            if orig and cid not in ru and has_cyrillic(orig):
                                ru[cid] = orig
                            nn = norm_name(orig)
                            if nn and len(nn) >= 2:
                                bn.setdefault(nn, cid)
                                tr = translit(nn)
                                if tr: bt.setdefault(tr, cid)
                                if len(nn[:6]) >= 3: bp.setdefault(nn[:6], []).append((nn, cid))
                el.clear()
        print(f"   EPG: {len(bn)} имён, {len(ic)} иконок, {len(bp)} префиксов, {len(ru)} кириллических имён")
    except Exception as e:
        print(f"   EPG err: {e}")
    return bn, bt, bp, ic, ru

EPG_VER = 24
def load_epg_map():
    p = DATA / "epg_map.json"
    ttl = CFG.get("epg_ttl_hours", 168) * 3600
    if p.exists():
        try:
            d = json.loads(p.read_text("utf-8"))
            if d.get("version") == EPG_VER and time.time() - d.get("ts", 0) < ttl:
                print("   EPG из кэша")
                return (d["by_name"], d["by_translit"],
                        {k: [tuple(x) for x in v] for k, v in d["by_prefix"].items()},
                        d.get("icons", {}), d.get("ru_display", {}))
        except: pass
    print(f"   DL EPG: {CFG.get('epg_url','')}")
    txt = download(CFG.get("epg_url",""), gz=True) or download(CFG.get("epg_url_fallback",""), gz=True)
    if not txt: return {}, {}, {}, {}, {}
    bn, bt, bp, ic, ru = build_epg_map(txt)
    try:
        p.write_text(json.dumps({
            "version": EPG_VER, "ts": int(time.time()),
            "by_name": bn, "by_translit": bt,
            "by_prefix": {k: list(v) for k, v in bp.items()},
            "icons": ic, "ru_display": ru
        }, ensure_ascii=False), "utf-8")
    except Exception as e: print(f"   save: {e}")
    return bn, bt, bp, ic, ru

def resolve_epg(name, bn, bt, bp):
    nn = norm_name(name)
    if not nn: return "", "no_match"
    if nn in bn: return bn[nn], "by_name"
    tr = translit(nn)
    if tr and tr in bt: return bt[tr], "by_translit"
    if len(nn[:6]) >= 3 and nn[:6] in bp:
        c = bp[nn[:6]]
        if len(c) == 1: return c[0][1], "by_prefix1"
        if len(c) <= 3: return c[0][1], "by_prefix"
    first2 = " ".join(nn.split()[:2])
    for key in (first2, nn[:4], nn[:3]):
        if len(key) < 3: continue
        bucket = bp.get(key)
        if not bucket: continue
        best, best_r = None, 0
        for nm, cid in bucket:
            r = SequenceMatcher(None, nn, nm).ratio() * 100
            if r > best_r: best_r, best = r, cid
        if best and best_r >= 88: return best, "by_fuzzy"
    return "", "no_match"

def load_api():
    p = DATA / "api_cache.json"
    ttl = CFG.get("api_ttl_hours", 168) * 3600
    if p.exists():
        try:
            d = json.loads(p.read_text("utf-8"))
            if d.get("version") == API_VER and time.time() - d.get("ts", 0) < ttl and d.get("logos"):
                print(f"   API из кэша (logos: {len(d.get('logos',{}))})")
                return d["cats"], d["names"], d["countries"], d["ru_names"], d.get("logos", {})
        except: pass
    print("   DL API channels")
    txt = download(CFG.get("api_url",""))
    if not txt: return {}, {}, {}, {}, {}
    cats, names, countries, ru_names, logos = {}, {}, {}, {}, {}
    try:
        m = {"movies":"Кино","series":"Сериалы","sports":"Спорт","news":"Новостные","documentary":"Документальные","science":"Познавательные","education":"Познавательные","history":"Документальные","culture":"Познавательные","travel":"Познавательные","music":"Музыкальные","entertainment":"Развлекательные","comedy":"Развлекательные","general":"Федеральные","business":"Новостные","weather":"Новостные","kids":"Детские","family":"Детские","religious":"Религиозные","lifestyle":"Развлекательные","cooking":"Познавательные","auto":"Познавательные"}
        for ch in json.loads(txt):
            cid = ch.get("id")
            if not cid: continue
            if ch.get("country"): countries[cid] = ch["country"]
            for c in (ch.get("categories") or []):
                if c.lower() in m: cats[cid] = m[c.lower()]; break
            nm = ch.get("name")
            if nm: names[norm_name(nm)] = cid
            for alt in (ch.get("alt_names") or []): names[norm_name(alt)] = cid
            for n in ([nm] if nm else []) + list(ch.get("alt_names") or []):
                if n and has_cyrillic(n): ru_names[cid] = n.strip(); break
    except Exception as e: print(f"   API channels err: {e}")

    print("   DL API logos")
    txt2 = download("https://iptv-org.github.io/api/logos.json")
    if txt2:
        try:
            for rec in json.loads(txt2):
                cid = rec.get("channel")
                url = rec.get("url")
                if cid and url:
                    if cid not in logos or (".svg" in logos[cid] and ".svg" not in url):
                        logos[cid] = url
            print(f"   API: {len(logos)} логотипов")
        except Exception as e:
            print(f"   API logos err: {e}")

    try:
        p.write_text(json.dumps({
            "version": API_VER, "ts": int(time.time()),
            "cats": cats, "names": names, "countries": countries,
            "ru_names": ru_names, "logos": logos
        }, ensure_ascii=False), "utf-8")
    except Exception as e:
        print(f"   API save err: {e}")
    return cats, names, countries, ru_names, logos

def load_sources():
    p = ROOT / "sources.txt"
    if not p.exists(): return []
    out = []
    for line in p.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 2: continue
        prio = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else 5
        out.append((parts[0], parts[1], prio))
    return out

async def fetch_src(session, name, url):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200: print(f"[src] {name}: HTTP {r.status}"); return name, None
            txt = await r.text(errors="ignore")
            print(f"[src] {name}: {len(txt)//1024} KB")
            return name, txt
    except Exception as e:
        print(f"[src] {name}: {e}"); return name, None

def cmd_parse():
    print("=== PARSE ===")
    sources = load_sources()
    if not sources: print("sources.txt пустой"); return
    bn, bt, bp, _, ru = load_epg_map()
    api_cats, api_names, countries, ru_names, api_logos = load_api()
    wl = load_whitelist()

    async def go():
        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as s:
            return await asyncio.gather(*(fetch_src(s, n, u) for n, u, _ in sources))
    results = asyncio.run(go())

    channels, seen = [], set()
    st = Counter()
    blocked_ids = set(BLOCKLIST.get("blocked_tvg_ids", []))
    blocked_countries = set(CFG.get("blocked_countries", []))
    blocked_hosts = set(BLOCKLIST.get("blocked_url_hosts", []))
    blocked_exact = set(BLOCKLIST.get("blocked_urls_exact", []))
    name_del = {k.lower() for k, v in OVERRIDES.get("category_by_name", {}).items() if v == "DELETE"}
    child_bl = CFG.get("child_blacklist", [])
    trash_bl = CFG.get("trash_blacklist", [])
    clear_ids = set(OVERRIDES.get("clear_tvg_id", []))

    for w in wl:
        if w["url"] in seen: st["dup_wl"] += 1; continue
        seen.add(w["url"]); channels.append(w)

    for (sname, _, prio), (_, text) in zip(sources, results):
        if not text: continue
        kept = 0
        for ch in parse_m3u(text):
            n, u, a = ch["name"], ch["url"], ch["attrs"]
            if is_junk_url(u): st["junk_url"] += 1; continue

            low = n.lower()
            if any(k in low for k in RAW_DELETE):
                st["name_del"] += 1; continue

            if any(x in low for x in child_bl): st["child"] += 1; continue
            if any(x in low for x in trash_bl): st["trash"] += 1; continue
            if norm_name(n) in name_del: st["name_del"] += 1; continue

            tid = a.get("tvg-id", "")
            base_id = tid.split("@")[0] if tid else ""
            if base_id in blocked_ids: st["blocked_id"] += 1; continue
            if countries.get(base_id) in blocked_countries: st["blocked_country"] += 1; continue
            host = urlparse(u).netloc.lower().split(":")[0]
            if any(host == h or host.endswith("." + h) for h in blocked_hosts): st["blocked_host"] += 1; continue
            if u.strip() in blocked_exact: st["blocked_url"] += 1; continue
            if u in seen: st["dup_url"] += 1; continue
            seen.add(u)
            channels.append({
                "name": n, "url": u,
                "tvg_id": "" if base_id in clear_ids else tid,
                "tvg_name": a.get("tvg-name",""),
                "logo": a.get("tvg-logo",""),
                "group": a.get("group-title",""),
                "source": sname, "prio": prio,
                "is_whitelist": False,
            })
            kept += 1
        print(f"[parse] {sname}: {kept}")

    DATA.joinpath("channels.json").write_text(json.dumps({"updated": now_msk(), "channels": channels}, ensure_ascii=False), "utf-8")
    print(f"[parse] итого {len(channels)}, фильтры: {dict(st)}")

def categorize(name, group, tvg_id, api_cats, api_names):
    low = name.lower()
    nn = norm_name(name)
    for key, cat in RAW_KEYWORDS:
        if key in low: return cat
    base_id = tvg_id.split("@")[0] if tvg_id else ""
    if base_id and base_id in OVERRIDES.get("category_by_tvg_id", {}):
        return OVERRIDES["category_by_tvg_id"][base_id]
    if nn in OVERRIDES.get("category_by_name", {}):
        return OVERRIDES["category_by_name"][nn]
    if base_id and base_id in api_cats: return api_cats[base_id]
    if nn in api_names and api_names[nn] in api_cats: return api_cats[api_names[nn]]
    for city in CITY_HINTS:
        if city in nn: return "Региональные"
    for cat, kws in CAT_KW.items():
        for k in kws:
            if k in low: return cat
    g = (group or "").lower()
    for cat in CATS:
        if cat.lower() in g: return cat
    if "sport" in g: return "Спорт"
    if "news" in g: return "Новостные"
    if "kids" in g or "child" in g: return "Детские"
    if "movie" in g or "cinema" in g: return "Кино"
    if "region" in g: return "Региональные"
    return "Разное"

def find_russian_name(name, tvg_id, bn, bt, bp, ru_display, api_names, ru_names):
    base_id = tvg_id.split("@")[0] if tvg_id else ""
    suffix = extract_suffix_from_tvg(tvg_id)
    if base_id and base_id in ru_display:
        base_name = ru_display[base_id]
        return (f"{base_name} {suffix}".strip() if suffix else base_name), "epg_by_base_id"
    cid, _ = resolve_epg(name, bn, bt, bp)
    if cid and cid in ru_display:
        base_name = ru_display[cid]
        return (f"{base_name} {suffix}".strip() if suffix else base_name), "epg_by_name"
    api_cid = api_names.get(norm_name(name)) or base_id
    if api_cid and api_cid in ru_names:
        base_name = ru_names[api_cid]
        return (f"{base_name} {suffix}".strip() if suffix else base_name), "api"
    if cid and cid in ru_names:
        base_name = ru_names[cid]
        return (f"{base_name} {suffix}".strip() if suffix else base_name), "api_by_epg_id"
    tr = translit(norm_name(name))
    if tr and tr in bt:
        cid2 = bt[tr]
        if cid2 in ru_display:
            base_name = ru_display[cid2]
            return (f"{base_name} {suffix}".strip() if suffix else base_name), "epg_by_translit"
        if cid2 in ru_names:
            base_name = ru_names[cid2]
            return (f"{base_name} {suffix}".strip() if suffix else base_name), "api_by_translit"
    return None, None

def cmd_categorize():
    print("=== CATEGORIZE ===")
    p = DATA / "channels.json"
    if not p.exists(): print("нет channels.json"); return
    channels = json.loads(p.read_text("utf-8"))["channels"]
    api_cats, api_names, _, ru_names, api_logos = load_api()
    bn, bt, bp, ic, ru_display = load_epg_map()

    epg_stats = Counter()
    rename_stats = Counter()
    logo_stats = Counter()

    for ch in channels:
        if ch.get("is_whitelist"):
            if not ch.get("category"):
                ch["category"] = ch.get("group") or "Whitelist"
            continue
        if not has_cyrillic(ch["name"]):
            new_name, method = find_russian_name(
                ch["name"], ch.get("tvg_id",""),
                bn, bt, bp, ru_display, api_names, ru_names
            )
            if new_name:
                ch["name"] = new_name
                rename_stats[method] += 1
            else:
                rename_stats["not_found"] += 1
        if not ch.get("tvg_id"):
            eid, how = resolve_epg(ch["name"], bn, bt, bp)
            if eid: ch["tvg_id"] = eid; epg_stats[how] += 1
            else: epg_stats["no_match"] += 1
        else:
            epg_stats["already"] += 1
        ch["category"] = categorize(ch["name"], ch.get("group",""), ch.get("tvg_id",""), api_cats, api_names)

        if not ch.get("logo"):
            tid = ch.get("tvg_id","")
            base_tid = tid.split("@")[0] if tid else ""
            for cand in (tid, base_tid):
                if cand and cand in ic:
                    ch["logo"] = ic[cand]; logo_stats["epg"] += 1; break
            if not ch.get("logo") and base_tid and base_tid in api_logos:
                ch["logo"] = api_logos[base_tid]; logo_stats["api_by_base_id"] += 1
            if not ch.get("logo"):
                api_cid = api_names.get(norm_name(ch["name"]))
                if api_cid and api_cid in api_logos:
                    ch["logo"] = api_logos[api_cid]; logo_stats["api_by_name"] += 1
            if not ch.get("logo"):
                eid2, _ = resolve_epg(ch["name"], bn, bt, bp)
                if eid2 and eid2 in ic:
                    ch["logo"] = ic[eid2]; logo_stats["epg_by_resolve"] += 1
            if not ch.get("logo"):
                logo_stats["no_logo"] += 1

        tid = ch.get("tvg_id","")
        if tid in OVERRIDES.get("rename_by_tvg_id", {}):
            ch["name"] = OVERRIDES["rename_by_tvg_id"][tid]
            ch["category"] = categorize(ch["name"], ch.get("group",""), tid, api_cats, api_names)
            rename_stats["override"] += 1

    groups = defaultdict(list)
    for ch in channels:
        if ch.get("is_whitelist"):
            groups[("WL", ch["url"])].append(ch)
        else:
            groups[ch["name"].strip()].append(ch)

    merged_raw = [v for k, v in groups.items() if not (isinstance(k, tuple) and k[0] == "WL")]
    wl_groups = [v for k, v in groups.items() if isinstance(k, tuple) and k[0] == "WL"]

    def host_of(u):
        try:
            return urlparse(u).netloc.lower().split(":")[0]
        except Exception:
            return "unknown"

    merged = []
    for grp in merged_raw:
        if len(grp) == 1:
            merged.append(grp)
        else:
            for c in grp:
                new = dict(c)
                new["name"] = f'{c["name"]} [{host_of(c["url"])}]'
                merged.append([new])

    def best(grp):
        return sorted(grp, key=lambda c: (-1 if c.get("is_whitelist") else 0,
                                          c.get("prio",0),
                                          1 if re.search(r'\b(hd|1080|4k|fhd)\b', c["name"].lower()) else 0),
                      reverse=True)

    final, fallbacks = [], 0
    for grp in merged + wl_groups:
        ordered = best(grp)
        main = ordered[0]
        urls = []
        for c in ordered:
            if c["url"] not in urls: urls.append(c["url"])
        if len(urls) > 1: fallbacks += 1
        final.append({
            "name": main["name"],
            "category": main["category"],
            "logo": main.get("logo",""),
            "tvg_id": main.get("tvg_id",""),
            "group": main.get("group",""),
            "source": main.get("source",""),
            "urls": urls,
            "is_whitelist": main.get("is_whitelist", False),
        })
    p.write_text(json.dumps({"updated": now_msk(), "channels": final}, ensure_ascii=False), "utf-8")
    wl_count = sum(1 for c in final if c.get("is_whitelist"))
    print(f"[cat] каналов {len(merged)+len(wl_groups)} -> {len(final)}, whitelist {wl_count}, с резервом: {fallbacks}")
    print(f"[cat] EPG: {dict(epg_stats)}")
    print(f"[cat] RU-NAME: {dict(rename_stats)}")
    print(f"[cat] LOGO: {dict(logo_stats)}")

def pick_ua(host, ua_cache):
    saved = ua_cache.get(host)
    order = []
    if saved and saved in UA_POOL: order.append(saved)
    keys = [k for k in UA_POOL if k not in order]
    if "HlsWinkPlayer" in keys:
        keys.remove("HlsWinkPlayer"); order.append("HlsWinkPlayer")
    random.shuffle(keys)
    order += sorted(keys, key=lambda k: -UA_W.get(k, 1))
    return order

def ffprobe_ok(url, ua):
    cmd = ["ffprobe","-hide_banner","-loglevel","error","-of","json",
           "-select_streams","v:0","-show_entries","stream=codec_name,width,height",
           "-probesize", str(CFG.get("ffprobe_probesize", 32768)),
           "-analyzeduration", str(CFG.get("ffprobe_analyzeduration", 1000000)),
           "-rw_timeout", str(int(CFG.get("ffprobe_timeout", 8) * 1_000_000)),
           "-user_agent", ua, "-i", url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=CFG.get("ffprobe_timeout", 8) + 3)
        if r.returncode != 0: return False
        return bool(json.loads(r.stdout or b"{}").get("streams"))
    except Exception: return False

async def http_pre(session, url, ua):
    try:
        h = {"User-Agent": ua, "Range": "bytes=0-2048"}
        async with session.get(url, headers=h, timeout=aiohttp.ClientTimeout(total=CFG.get("http_timeout", 8)), allow_redirects=True) as r:
            if r.status not in (200, 206): return False
            ct = r.headers.get("Content-Type","").lower()
            if ("text" in ct or "html" in ct) and "mpegurl" not in ct: return False
            return True
    except Exception: return False

async def check_one(session, ch, sem, ua_cache, results, loop):
    async with sem:
        for u in ch["urls"]:
            host = urlparse(u).netloc.lower().split(":")[0]
            for k in pick_ua(host, ua_cache):
                ua = UA_POOL[k]
                if not await http_pre(session, u, ua): continue
                ok = await loop.run_in_executor(None, ffprobe_ok, u, ua)
                if ok:
                    ua_cache[host] = k
                    results[ch["name"]] = {"ok": True, "url": u, "ua": k}
                    return
        results[ch["name"]] = {"ok": False}

def migrate_cache():
    old = ROOT / "cache.json"
    if not old.exists(): return
    try: d = json.loads(old.read_text("utf-8"))
    except Exception: return
    mapping = {"Wink":"Wink","TiviMate":"TiviMate","Chrome":"Chrome_Win",
               "AndroidTV":"AndroidTV","VLC":"VLC","SmartTV":"SmartTV"}
    new = {}
    for url, v in d.items():
        host = urlparse(url).netloc.lower().split(":")[0]
        new[host] = mapping.get(v.get("ua_name",""), "VLC")
    DATA.joinpath("ua_cache.json").write_text(json.dumps(new, ensure_ascii=False, indent=1), "utf-8")
    print(f"[migrate] cache.json -> data/ua_cache.json, хостов: {len(new)}")

def cmd_check():
    print("=== CHECK ===")
    if (ROOT / "cache.json").exists() and not (DATA / "ua_cache.json").exists():
        migrate_cache()
    p = DATA / "channels.json"
    if not p.exists(): print("нет channels.json"); return
    channels = json.loads(p.read_text("utf-8"))["channels"]
    to_check = [c for c in channels if not c.get("is_whitelist")]
    wl_count = len(channels) - len(to_check)
    total = len(to_check)
    print(f"   whitelist {wl_count} пропускаем, проверяем {total}", flush=True)

    cp = DATA / "ua_cache.json"
    ua_cache = json.loads(cp.read_text("utf-8")) if cp.exists() else {}
    results = {}
    done = [0]
    ok_count = [0]
    sem = asyncio.Semaphore(CFG.get("concurrency", 100))
    session_holder = [None]
    loop_holder = [None]

    async def worker(c):
        await check_one(session_holder[0], c, sem, ua_cache, results, loop_holder[0])
        done[0] += 1
        if results.get(c["name"], {}).get("ok"): ok_count[0] += 1
        if done[0] % 50 == 0 or done[0] == total:
            pct = done[0] * 100 // total
            bar_len = 30
            filled = bar_len * done[0] // total
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"   [{bar}] {done[0]}/{total} ({pct}%) живых: {ok_count[0]}", flush=True)

    async def go():
        loop_holder[0] = asyncio.get_event_loop()
        async with aiohttp.ClientSession() as s:
            session_holder[0] = s
            await asyncio.gather(*(worker(c) for c in to_check))
    asyncio.run(go())

    cp.write_text(json.dumps(ua_cache, ensure_ascii=False, indent=1), "utf-8")
    DATA.joinpath("check.json").write_text(json.dumps({"updated": now_msk(), "results": results}, ensure_ascii=False), "utf-8")
    ok = sum(1 for r in results.values() if r.get("ok"))
    print(f"[check] {ok}/{total} живых")

def cmd_build():
    print("=== BUILD ===")
    p = DATA / "channels.json"
    if not p.exists(): print("нет channels.json"); return
    channels = json.loads(p.read_text("utf-8"))["channels"]
    check = {}
    if (DATA / "check.json").exists():
        check = json.loads((DATA / "check.json").read_text("utf-8")).get("results", {})
    ua_cache = {}
    if (DATA / "ua_cache.json").exists():
        ua_cache = json.loads((DATA / "ua_cache.json").read_text("utf-8"))

    total = len(channels)
    checked = sum(1 for c in channels if c["name"] in check)
    if CFG.get("auto_cleanup") and total and checked / total >= CFG.get("auto_cleanup_ready_ratio", 0.7):
        before = total
        channels = [c for c in channels if c.get("is_whitelist") or check.get(c["name"], {}).get("ok", True)]
        print(f"[build] автоочистка: удалено {before - len(channels)} мёртвых")

    out_groups = []
    for grp in WL_GROUPS_ORDER:
        items = [c for c in channels if c.get("is_whitelist") and c.get("group") == grp]
        if items: out_groups.append((grp, items))
    other_wl_groups = {c["group"] for c in channels if c.get("is_whitelist")} - set(WL_GROUPS_ORDER)
    for grp in sorted(other_wl_groups):
        items = [c for c in channels if c.get("is_whitelist") and c.get("group") == grp]
        if items: out_groups.append((grp, items))
    by_cat = defaultdict(list)
    for c in channels:
        if c.get("is_whitelist"): continue
        r = check.get(c["name"])
        if r and not r.get("ok"): continue
        by_cat[c["category"]].append(c)
    for cat in CATS:
        items = by_cat.get(cat, [])
        if items: out_groups.append((f"{EMOJI.get(cat,'')} {cat}", items))

    unmatched = sorted({c["name"] for c in by_cat.get("Разное", [])})
    (DATA / "unmatched.json").write_text(
        json.dumps({"updated": now_msk(), "count": len(unmatched), "channels": unmatched},
                   ensure_ascii=False, indent=2),
        "utf-8"
    )
    print(f"[build] Разное: {len(unmatched)} -> data/unmatched.json")

    with_logo = sum(1 for c in channels if c.get("logo"))
    without_logo = len(channels) - with_logo

    lines = ['#EXTM3U url-tvg="' + CFG.get("epg_url", "") + '"']
    for grp_name, items in out_groups:
        for c in items:
            u = c["urls"][0] if c["urls"] else ""
            if not u: continue
            host = urlparse(u).netloc.lower().split(":")[0]
            ua = UA_POOL.get(ua_cache.get(host, "VLC"), "")
            tid = c.get("tvg_id", "")
            logo = c.get("logo", "")
            lines.append(f'#EXTINF:-1 tvg-id="{tid}" tvg-logo="{logo}" group-title="{grp_name}",{c["name"]}')
            if ua: lines.append(f'#EXTVLCOPT:http-user-agent={ua}')
            for u2 in c["urls"]: lines.append(u2)

    out_name = CFG.get("output_m3u", "clean_sorted_channels.m3u")
    (ROOT / out_name).write_text("\n".join(lines) + "\n", "utf-8")
    total_out = sum(len(v) for _, v in out_groups)
    print(f"[build] {out_name}: {total_out} каналов в {len(out_groups)} группах")
    print(f"[build] логотипы: {with_logo} с лого, {without_logo} без")

    dash = {
        "updated": now_msk(),
        "total": len(channels),
        "alive": sum(1 for r in check.values() if r.get("ok")),
        "with_fallback": sum(1 for c in channels if len(c["urls"]) > 1),
        "whitelist": sum(1 for c in channels if c.get("is_whitelist")),
        "ua_hosts": len(ua_cache),
        "unmatched": len(unmatched),
        "with_logo": with_logo,
        "without_logo": without_logo,
        "categories": {cat: len(by_cat.get(cat, [])) for cat in CATS},
    }
    (ROOT / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False, indent=2), "utf-8")

    idx = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IPTV Dashboard</title>
<style>body{font-family:system-ui;margin:1rem;max-width:640px}h1{font-size:1.3rem}
.row{display:flex;justify-content:space-between;border-bottom:1px solid #eee;padding:.35rem 0}
a{color:#0366d6}</style></head><body>
<h1>IPTV Dashboard</h1><div id="s">загрузка...</div>
<script>fetch('dashboard.json').then(r=>r.json()).then(d=>{
let h='<div class="row"><b>Обновлено</b><span>'+d.updated+'</span></div>';
h+='<div class="row"><b>Всего</b><span>'+d.total+'</span></div>';
h+='<div class="row"><b>Живых</b><span>'+d.alive+'</span></div>';
h+='<div class="row"><b>Whitelist</b><span>'+d.whitelist+'</span></div>';
h+='<div class="row"><b>С логотипом</b><span>'+d.with_logo+'</span></div>';
h+='<div class="row"><b>Без логотипа</b><span>'+d.without_logo+'</span></div>';
h+='<div class="row"><b>Разное</b><span>'+d.unmatched+'</span></div>';
h+='<h1 style="margin-top:1rem">Категории</h1>';
for(const[k,v] of Object.entries(d.categories))h+='<div class="row">'+k+'<span>'+v+'</span></div>';
h+='<p style="margin-top:1rem"><a href="clean_sorted_channels.m3u">clean_sorted_channels.m3u</a></p>';
document.getElementById('s').innerHTML=h;});</script></body></html>"""
    (ROOT / "index.html").write_text(idx, "utf-8")

    repo = os.getenv("GITHUB_REPOSITORY", "gvineon550-coder/lindsben_iptv-")
    user, rn = repo.split("/", 1) if "/" in repo else ("gvineon550-coder", "lindsben_iptv-")
    pages = f"https://{user.lower()}.github.io/{rn.lower()}/{out_name}"
    rd = ["# IPTV Playlist", "", "Только русскоязычные каналы. Автообновление.", "",
          "## Dashboard", "",
          f"- Updated: **{dash['updated']}**",
          f"- Всего: {dash['total']}",
          f"- Живых: **{dash['alive']}**",
          f"- Whitelist: {dash['whitelist']}",
          f"- С логотипом: {dash['with_logo']}",
          f"- Без логотипа: {dash['without_logo']}",
          f"- В «Разное»: {dash['unmatched']}", "",
          "## Категории", "", "| Категория | Каналов |", "|---|---|"]
    for cat in CATS:
        rd.append(f"| {EMOJI.get(cat,'')} {cat} | {dash['categories'].get(cat,0)} |")
    rd += ["", "## Ссылки", "",
           f"- Плейлист: `{pages}`",
           f"- EPG: `{CFG.get('epg_url','')}`", ""]
    (ROOT / "README.md").write_text("\n".join(rd), "utf-8")

def main():
    cmds = {"parse": cmd_parse, "categorize": cmd_categorize, "check": cmd_check,
            "build": cmd_build, "migrate-cache": migrate_cache}
    if len(sys.argv) < 2 or (sys.argv[1] not in cmds and sys.argv[1] != "all"):
        print("usage: app.py {parse|categorize|check|build|all|migrate-cache}"); return
    c = sys.argv[1]
    if c == "all":
        cmd_parse(); cmd_categorize(); cmd_check(); cmd_build()
    else:
        cmds[c]()

if __name__ == "__main__":
    main()
