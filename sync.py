# -*- coding: utf-8 -*-
"""自动抓取 TVBox 公开配置 -> 提取 Maccms 采集接口 -> 实测 -> 输出 dongguaTV 的 db.json

在 GitHub Actions 上每天定时跑（见 .github/workflows/sync.yml），
也可以在 NAS / 本机手动跑：python sync.py

数据源优先级：
  1. feeds.txt 里每行一个配置地址（自己维护，留空则只用内置默认源）
  2. 内置默认源（几个长期维护的自动抓取仓库）
  3. 仓库里已通过的 db.json（保底，不会因为某天网络抖动把源全清空）

只输出 dongguaTV 认的字段：key / name / api
"""
import json, os, re, ssl, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlsplit

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TIMEOUT_PROBE = 12
TIMEOUT_FETCH = 25

DEFAULT_FEEDS = [
    # 每天自动抓 90+ 配置，专门提取 type 0/1 采集站
    "https://gcore.jsdelivr.net/gh/lubin776/tvbox-api-backup@main/tvbox/"
    + quote("海量采集聚合接口.json"),
    # 跨源合并 + 探测可用线路
    "https://gcore.jsdelivr.net/gh/TVboxorg/TVbox@main/dist/official.json",
]

# 私有源放在 feeds.local.txt（已 gitignore），不会进仓库
LOCAL_FEEDS = "feeds.local.txt"

BAD_HOST = ("gh-proxy.com", "127.0.0.1", "localhost", "example.com")


def opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=CTX)
    )


def feeds():
    """feeds.txt（公开示例）+ feeds.local.txt（私有，gitignore）+ 内置默认源，去重保序"""
    out = []
    for fn in ("feeds.txt", LOCAL_FEEDS):
        if not os.path.exists(fn):
            continue
        try:
            for l in open(fn, encoding="utf-8"):
                l = l.strip()
                if l.startswith("http") and l not in out:
                    out.append(l)
        except Exception:
            pass
    for u in DEFAULT_FEEDS:
        if u not in out:
            out.append(u)
    return out


def fetch(url):
    try:
        return opener().open(
            urllib.request.Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT_FETCH
        ).read()
    except Exception as e:
        print(f"  x {url[:74]} :: {type(e).__name__}")
        return None


def parse(raw):
    text = raw.decode("utf-8", "replace")
    s, e = text.find("{"), text.rfind("}")
    if s < 0:
        return None
    body = text[s:e + 1]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"(^|\n)\s*//[^\n]*", "", body)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def host_of(api):
    return (urlsplit(api).hostname or "").lower().replace("www.", "")


def probe(item):
    name, api = item
    h = host_of(api)
    if not h or any(b in h for b in BAD_HOST):
        return item, False
    u = api.rstrip("/") + ("&" if "?" in api else "?") + "ac=list&pg=1"
    try:
        b = opener().open(
            urllib.request.Request(u, headers={"User-Agent": UA}), timeout=TIMEOUT_PROBE
        ).read()[:500]
        return item, (b'"list"' in b or b"vod_name" in b or b'"data"' in b)
    except Exception:
        return item, False


def prettify(name, host):
    n = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", name or "")
    for cut in ["| 采集", "| 影视", "「", "精选", "·ok线路1", "·1号VIP源"]:
        n = n.split(cut)[0]
    # 去掉 [直连] / (2) / （3） 这类标注
    n = re.sub(r"[\[\(（][^\]\)）]*[\]\)）]", "", n)
    n = n.replace("tv", "").replace("TV", "")
    n = re.sub(r"[┃|｜\s\-]+", "", n).strip()
    # 「tv影视 | 建安…」清洗后会剩「影视建安」，去掉误导性前缀
    if n.startswith("影视") and len(n) > 3:
        n = n[2:]
    return n or host


def main():
    print("=== 1. 抓取数据源 ===")
    pool = {}
    for url in feeds():
        raw = fetch(url)
        cfg = parse(raw) if raw else None
        got = 0
        for s in (cfg or {}).get("sites", []):
            if not isinstance(s, dict) or s.get("type") not in (0, 1):
                continue
            api = str(s.get("api") or "").strip()
            if not api.startswith("http") or api.endswith((".py", ".js", ".txt")):
                continue
            h = host_of(api)
            if h and h not in pool:
                pool[h] = (str(s.get("name") or h), api)
                got += 1
        print(f"  ok {url[:74]}  (+{got})")

    # 保底：仓库里已有的
    try:
        old = json.load(open("db.json", encoding="utf-8"))["sites"]
    except Exception:
        old = []
    for s in old:
        h = host_of(s.get("api", ""))
        if h and h not in pool:
            pool[h] = (s.get("name") or h, s["api"])
    print(f"  已有 db.json {len(old)} 条并入候选池")

    print(f"\n候选域名 {len(pool)} 个\n=== 2. 并发实测（{TIMEOUT_PROBE}s 超时）===")
    alive = []
    with ThreadPoolExecutor(16) as ex:
        for (name, api), ok in ex.map(probe, pool.values()):
            if ok:
                alive.append({"name": prettify(name, host_of(api)), "api": api.rstrip("/")})
                print(f"  ok {host_of(api)}")

    # 重名加序号
    cnt = {}
    for i, a in enumerate(sorted(alive, key=lambda x: x["name"])):
        base = a["name"]
        cnt[base] = cnt.get(base, 0) + 1
        if cnt[base] > 1:
            a["name"] = f"{base}{cnt[base]}"
        a["key"] = f"s{i+1}"

    sites = sorted(alive, key=lambda x: x["key"])
    print(f"\n候选 {len(pool)} -> 存活 {len(sites)}")
    if not sites:
        print("!! 一个都没活，保留原 db.json，不覆盖")
        return 1
    json.dump({"sites": sites}, open("db.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"已写入 db.json: {len(sites)} 个源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
