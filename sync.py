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
import json, os, re, ssl, sys, time, urllib.request
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

# ── 自动发现（环境变量 DISCOVER=1 才启用）─────────────────────────
# 用 GitHub 搜索找「高星 + 近期活跃」的 TVBox / 影视仓 合集仓，
# 再从仓库文件树里挑出像配置的文件（tvbox.json / api.json / config.json…）
DISCOVER_QUERIES = [
    "tvbox stars:>30 fork:false pushed:>2026-01-01",
    "tvbox 接口 stars:>20 fork:false pushed:>2026-01-01",
    "影视仓 stars:>20 fork:false pushed:>2026-01-01",
    "tvbox 配置 stars:>15 fork:false pushed:>2026-01-01",
]
DISCOVER_SKIP = {"lubin776/tvbox-api-backup", "TVboxorg/TVbox"}
CONFIG_NAME = re.compile(r"(tvbox|api|config|box|接口|配置|urls?|py|spider)", re.I)
MAX_REPOS = 12
MAX_FILES_PER_REPO = 2


def opener():
    """SYNC_PROXY 可指定代理（本地/NAS 上用），不设则直连"""
    px = os.environ.get("SYNC_PROXY")
    handler = (urllib.request.ProxyHandler({"http": px, "https": px})
               if px else urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(handler, urllib.request.HTTPSHandler(context=CTX))


def gh_json(url, timeout=20):
    """带 token（若有）的 GitHub API 请求"""
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    try:
        return json.load(opener().open(urllib.request.Request(url, headers=h), timeout=timeout))
    except Exception:
        return None


# 常见配置文件名（直接探测，不消耗 API 额度）
COMMON_PATHS = [
    "tvbox.json", "config.json", "api.json", "tv.json", "box.json",
    "json/tvbox.json", "json/config.json", "dist/official.json", "urls.json",
]
README_NAMES = ["README.md", "readme.md", "README.MD"]


def head_ok(url, timeout=10):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        return opener().open(req, timeout=timeout).status == 200
    except Exception:
        return False


def readme_urls(full):
    """从仓库 README 里扒出 .json 配置地址"""
    for name in README_NAMES:
        url = f"https://raw.githubusercontent.com/{full}/HEAD/{name}"
        raw = fetch(url)
        if not raw:
            continue
        text = raw.decode("utf-8", "replace")
        found = []
        for m in re.findall(r"https?://[^\s\"'<>\)\]，。]+?\.json", text):
            if "raw.githubusercontent.com" in m or "jsdelivr" in m or "gitee" in m or "gitcode" in m:
                found.append(m)
        return found[:4]
    return []


def discover_feeds():
    """搜索 GitHub 上活跃的 TVBox 合集仓，返回候选配置地址（不消耗 core API 额度）"""
    if os.environ.get("DISCOVER") != "1":
        return []

    repos, seen = [], set()
    for q in DISCOVER_QUERIES:
        d = gh_json("https://api.github.com/search/repositories?per_page=8&sort=updated&q="
                    + quote(q))
        for it in (d or {}).get("items", []):
            full = it["full_name"]
            if full in seen or full in DISCOVER_SKIP or it.get("fork"):
                continue
            seen.add(full)
            repos.append(full)
        if len(repos) >= MAX_REPOS:
            break
    repos = repos[:MAX_REPOS]
    print(f"  发现候选仓库 {len(repos)} 个")

    out, seen_url = [], set()

    def add(u):
        u = u.replace("http://", "https://", 1) if u.startswith("http://") else u
        if u not in seen_url:
            seen_url.add(u)
            out.append(u)

    for full in repos:
        picked = 0
        # ① README 里直接写的配置地址
        for u in readme_urls(full):
            add(u)
            picked += 1
            if picked >= 3:
                break
        # ② 常见文件名探测
        for p in COMMON_PATHS:
            if picked >= 3:
                break
            u = f"https://raw.githubusercontent.com/{full}/HEAD/{p}"
            if head_ok(u):
                add(u)
                picked += 1
        if picked:
            print(f"    {full:42s} +{picked}")
    print(f"  自动发现配置 {len(out)} 个")
    return out


def feeds():
    """返回 [(url, 是否私有), ...]

    公开：feeds.txt
    私有：feeds.local.txt（本地，gitignore）+ 环境变量 FEEDS_EXTRA（GitHub Secret）
    自动发现：DISCOVER=1 时从 GitHub 搜索得到
    内置默认源：DEFAULT_FEEDS
    """
    out = []
    seen = set()

    def add(u, private):
        if u.startswith("http") and u not in seen:
            seen.add(u)
            out.append((u, private))

    for fn in ("feeds.txt", LOCAL_FEEDS):
        if not os.path.exists(fn):
            continue
        try:
            for l in open(fn, encoding="utf-8"):
                add(l.strip(), fn == LOCAL_FEEDS)
        except Exception:
            pass
    for l in re.split(r"[\s,;]+", os.environ.get("FEEDS_EXTRA", "")):
        add(l.strip(), True)
    for u in DEFAULT_FEEDS:
        add(u, False)
    for u in discover_feeds():
        add(u, False)
    return out


def fetch(url):
    """抓取并做基础容错：两次尝试分别请求 gzip / identity，自动解压"""
    last = None
    for enc in ("gzip", "identity"):
        try:
            raw = opener().open(
                urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": enc}),
                timeout=TIMEOUT_FETCH,
            ).read()
            import gzip as _gz, zlib as _zl
            if raw[:2] == b"\x1f\x8b":                      # gzip 魔数
                raw = _gz.decompress(raw)
            elif raw[:1] == b"\x78":                        # zlib/deflate 魔数
                try:
                    raw = _zl.decompress(raw)
                except Exception:
                    pass
            return raw
        except Exception as e:
            last = e
            time.sleep(2)
    print(f"    -> {type(last).__name__}")
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
    priv_i = 0
    for url, private in feeds():
        label = None
        if private:
            priv_i += 1
            label = f"[私有源#{priv_i}]"
        print(f"  · {label or url[:74]}")
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
        print(f"    {'ok' if raw else 'fail'}  +{got}")

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
