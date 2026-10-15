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

CATS = CFG.get("category_order", ["Федеральные","Новостные","Региональные","Кино","Сериалы","Спорт","Познавательные","Документальные","Музыкальные","Развлекательные","Детские","Религиозные","Радио","Разное"])
EMOJI = CFG.get("category_emoji", {})
CAT_KW = CFG.get("category_keywords", {})
UA_W = CFG.get("ua_weights", {})
WL_GROUPS_ORDER = CFG.get("whitelist_groups_order", ["Kinowalk", "Rutube"])

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
FAMILY_RULES = (
    ("нтв", "Федеральные"),
    ("тнт", "Федеральные"),
    ("стс", "Федеральные"),
    ("рен", "Федеральные"),
    ("пятниц", "Федеральные"),
    ("россия 1", "Федеральные"),
    ("россия 24", "Новостные"),
    ("первый", "Федеральные"),
    ("матч", "Спорт"),
    ("discovery", "Познавательные"),
    ("viasat", "Познавательные"),
    ("tv1000", "Кино"),
    ("кинопоказ", "Кино"),
)

NOISE = {"тв","tv","hd","uhd","fhd","sd","4k","1080p","720p","full","канал","channel","online","live","stream"}
def norm_name(s):
    n = (s or "").lower().replace("ё","е").replace("й","и")
    n = re.sub(r'\(.*?\)|\[.*?\]', ' ', n)
    n = re.sub(r'[^a-zа-я0-9\s]', ' ', n)
    return " ".join(w for w in n.split() if w and w not in NOISE)

TRANSLIT = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya","ї":"i","є":"e","і":"i","ґ":"g"}
def translit(s): return "".join(TRANSLIT.get(c, c) for c in (s or ""))

def has_cyrillic(s): return bool(re.search(r'[а-яА-ЯёЁ]', s or ''))

ATTR_RE = re.compile(r'([A-Za-z0-9_\-]+)="([^"]*)"')
EXTINF_RE = re.compile(r'#EXTINF:-?\d+\s*(.*?),(.*)$')

def parse_m3u(text):
    out, name, attrs = [], None, {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line: continue
        if line.startswith("#EXTINF"):
            m = EXTINF_RE.search(line)
            if m: attrs, name = dict(ATTR_RE.findall(m.group(1))), m.group(2).strip()
        elif line.startswith("#"): continue
        else:
            if name: out.append({"name": name, "url": line, "attrs": attrs})
            name, attrs = None, {}
    return out

PRIV_RE = re.compile(r'^https?://(localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)')
FAKE_RE = re.compile(r'\.(local|test|invalid|example|localhost)(\b|:|/)', re.I)
BAD_EXT = tuple(BLOCKLIST.get("bad_extensions", [".html",".zip",".exe",".apk",".jpg",".png",".gif"]))

def is_junk_url(u):
    if not u or not re.match(r'^https?://', u): return True
    if PRIV_RE.search(u): return True
    if FAKE_RE.search(u): return True
    if u.lower().split("?")[0].endswith(BAD_EXT): return True
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
            if time.time() - d.get("ts", 0) < ttl:
                print("   API из кэша")
                return d["cats"], d["names"], d["countries"], d["ru_names"]
        except: pass
    print("   DL API")
    txt = download(CFG.get("api_url",""))
    if not txt: return {}, {}, {}, {}
    cats, names, countries, ru_names = {}, {}, {}, {}
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
        p.write_text(json.dumps({"ts":int(time.time()),"cats":cats,"names":names,"countries":countries,"ru_names":ru_names}, ensure_ascii=False), "utf-8")
    except Exception as e: print(f"   API err: {e}")
    return cats, names, countries, ru_names

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

# ============ PARSE ============
def cmd_parse():
    print("=== PARSE ===")
    sources = load_sources()
    if not sources: print("sources.txt пустой"); return
    bn, bt, bp, _, ru = load_epg_map()
    api_cats, api_names, countries, ru_names = load_api()
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

    # Whitelist первым — он неприкосновенен
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

# ============ CATEGORIZE ============
def categorize(name, group, tvg_id, api_cats, api_names):
    low = name.lower()
    nn = norm_name(name)
    if tvg_id and tvg_id in OVERRIDES.get("category_by_tvg_id", {}):
        return OVERRIDES["category_by_tvg_id"][tvg_id]
    if nn in OVERRIDES.get("category_by_name", {}):
        return OVERRIDES["category_by_name"][nn]
    if tvg_id and tvg_id in api_cats: return api_cats[tvg_id]
    if nn in api_names and api_names[nn] in api_cats: return api_cats[api_names[nn]]
    for stem, cat in FAMILY_RULES:
        if nn.startswith(stem) or (" " + stem + " ") in (" " + nn + " "):
            return cat
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
    if tvg_id and tvg_id in ru_display:
        return ru_display[tvg_id], "epg_by_id"
    cid, _ = resolve_epg(name, bn, bt, bp)
    if cid and cid in ru_display:
        return ru_display[cid], "epg_by_name"
    api_cid = api_names.get(norm_name(name)) or tvg_id
    if api_cid and api_cid in ru_names:
        return ru_names[api_cid], "api"
    if cid and cid in ru_names:
        return ru_names[cid], "api_by_epg_id"
    tr = translit(norm_name(name))
    if tr and tr in bt:
        cid2 = bt[tr]
        if cid2 in ru_display:
            return ru_display[cid2], "epg_by_translit"
        if cid2 in ru_names:
            return ru_names[cid2], "api_by_translit"
    return None, None

def cmd_categorize():
    print("=== CATEGORIZE ===")
    p = DATA / "channels.json"
    if not p.exists(): print("нет channels.json"); return
    channels = json.loads(p.read_text("utf-8"))["channels"]
    api_cats, api_names, _, ru_names = load_api()
    bn, bt, bp, ic, ru_display = load_epg_map()

    epg_stats = Counter()
    rename_stats = Counter()

    for ch in channels:
        if ch.get("is_whitelist"):
            if not ch.get("category"):
                ch["category"] = ch.get("group") or "Whitelist"
            continue
        ch["category"] = categorize(ch["name"], ch.get("group",""), ch.get("tvg_id",""), api_cats, api_names)

        if not ch.get("tvg_id"):
            eid, how = resolve_epg(ch["name"], bn, bt, bp)
            if eid: ch["tvg_id"] = eid; epg_stats[how] += 1
            else: epg_stats["no_match"] += 1
        else:
            epg_stats["already"] += 1

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

        if not ch.get("logo") and ch.get("tvg_id") and ch["tvg_id"] in ic:
            ch["logo"] = ic[ch["tvg_id"]]

        tid = ch.get("tvg_id","")
        if tid in OVERRIDES.get("rename_by_tvg_id", {}):
            ch["name"] = OVERRIDES["rename_by_tvg_id"][tid]
            rename_stats["override"] += 1

    # Группировка
    groups = defaultdict(list)
    for ch in channels:
        if ch.get("is_whitelist"):
            groups[("WL", ch["url"])].append(ch)
        else:
            groups[ch["name"].strip()].append(ch)

    # merged = только НЕ-whitelist группы; wl_groups = whitelist
    merged = [v for k, v in groups.items() if not (isinstance(k, tuple) and k[0] == "WL")]
    wl_groups = [v for k, v in groups.items() if isinstance(k, tuple) and k[0] == "WL"]

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
    print(f"[cat] групп {len(merged)+len(wl_groups)} -> {len(final)}, whitelist {wl_count}, с резервом: {fallbacks}")
    print(f"[cat] EPG: {dict(epg_stats)}")
    print(f"[cat] RU-NAME: {dict(rename_stats)}")

# ============ CHECK ============
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
    print(f"   whitelist {wl_count} пропускаем, проверяем {len(to_check)}")

    cp = DATA / "ua_cache.json"
    ua_cache = json.loads(cp.read_text("utf-8")) if cp.exists() else {}
    results = {}
    sem = asyncio.Semaphore(CFG.get("concurrency", 100))

    async def go():
        loop = asyncio.get_event_loop()
        async with aiohttp.ClientSession() as s:
            await asyncio.gather(*(check_one(s, c, sem, ua_cache, results, loop) for c in to_check))
    asyncio.run(go())

    cp.write_text(json.dumps(ua_cache, ensure_ascii=False, indent=1), "utf-8")
    DATA.joinpath("check.json").write_text(json.dumps({"updated": now_msk(), "results": results}, ensure_ascii=False), "utf-8")
    ok = sum(1 for r in results.values() if r.get("ok"))
    print(f"[check] {ok}/{len(to_check)} живых")

# ============ BUILD ============
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
        channels = [c for c in channels if c.get("is_whitelist") or check.get(c["name"], {}).get("ok", True)]

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
    wl_out = sum(len(v) for g, v in out_groups if g in WL_GROUPS_ORDER or (g and g not in CATS and not any(e in g for e in EMOJI.values())))
    print(f"[build] {out_name}: {total_out} каналов в {len(out_groups)} группах")

    dash = {
        "updated": now_msk(),
        "total": len(channels),
        "alive": sum(1 for r in check.values() if r.get("ok")),
        "with_fallback": sum(1 for c in channels if len(c["urls"]) > 1),
        "whitelist": sum(1 for c in channels if c.get("is_whitelist")),
        "ua_hosts": len(ua_cache),
        "unmatched": len(unmatched),
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
h+='<div class="row"><b>С резервом</b><span>'+d.with_fallback+'</span></div>';
h+='<div class="row"><b>UA-хостов</b><span>'+d.ua_hosts+'</span></div>';
h+='<div class="row"><b>Разное</b><span>'+d.unmatched+'</span></div>';
h+='<h1 style="margin-top:1rem">Категории</h1>';
for(const[k,v] of Object.entries(d.categories))h+='<div class="row">'+k+'<span>'+v+'</span></div>';
h+='<p style="margin-top:1rem"><a href="clean_sorted_channels.m3u">clean_sorted_channels.m3u</a></p>';
h+='<p><a href="data/unmatched.json">data/unmatched.json</a></p>';
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
          f"- С резервными ссылками: {dash['with_fallback']}",
          f"- UA-хостов: {dash['ua_hosts']}",
          f"- В «Разное» (требует разбора): {dash['unmatched']}", "",
          "## Категории", "", "| Категория | Каналов |", "|---|---|"]
    for cat in CATS:
        rd.append(f"| {EMOJI.get(cat,'')} {cat} | {dash['categories'].get(cat,0)} |")
    rd += ["", "## Ссылки", "",
           f"- Плейлист: `{pages}`",
           f"- EPG: `{CFG.get('epg_url','')}`",
           f"- Неклассифицированные: `data/unmatched.json`", ""]
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
