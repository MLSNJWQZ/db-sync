# ETV电影源列表（自动更新）

每天由 GitHub Actions 自动抓取公开 TVBox 配置 → 提取 Maccms 采集接口 → 逐个实测 → 只保留能用的，写进 `db.json`。

产出直接给 [dongguaTV](https://github.com/ednovas/dongguaTV) 用，格式只有三个字段：

```json
{ "sites": [ { "key": "s1", "name": "量子资源", "api": "https://.../api.php/provide/vod" } ] }
```

## ☕ 支持这个项目

这份列表是我每天自动抓取、逐个实测维护的，服务器和带宽都是自费。如果它帮到了你，可以请我喝杯咖啡 —— **金额自选，不是包月，几块钱也是心意**：

[![爱发电](https://img.shields.io/badge/%E7%88%B1%E5%8F%91%E7%94%B5-%E8%AF%B7%E6%88%91%E5%96%9D%E6%9D%AF%E5%92%96%E5%95%A1-946ce6?style=for-the-badge)](https://afdian.com/a/mlsnjwqz)

👉 **https://afdian.com/a/mlsnjwqz**

也欢迎点右上角的 **Sponsor** 按钮，或给仓库点个 **Star** —— 都是对持续维护的鼓励。

## 用法（三选一）

### 1. 让 NAS 自己拉（推荐，一劳永逸）

dongguaTV 支持 `REMOTE_DB_URL`，把本仓库的 `db.json` 地址填进去，5 分钟缓存，失败自动回退本地文件：

```yaml
- REMOTE_DB_URL=https://gcore.jsdelivr.net/gh/MLSNJWQZ/db-sync@main/db.json
```

之后 NAS 上的源列表跟着本仓库每天自动变，**不用再手动传文件、不用重启容器**。

> 仓库必须是 **public**，jsDelivr 才能取到。

### 2. 手动跑一次，把 db.json 拷到 NAS

```bash
python sync.py
# 然后把 db.json 覆盖到 /vol1/1000/docker/ETV/db.json
# docker compose -p ETV up -d --force-recreate
```

### 3. 在 NAS 上定时跑（结果最准）

```bash
# crontab -e，每天 11:00
0 11 * * * cd /vol1/1000/docker/ETV && /usr/bin/python3 sync.py && docker restart donggua-tv
```

## 为什么建议在 NAS 上跑而不是只用 Actions

GitHub Actions 的机器在**海外**。海外能通的采集站，在国内可能被墙；反过来国内能用的，海外未必测得出。所以：

- **Actions 跑出来的列表**：当"候选池"用，覆盖广
- **NAS 上跑出来的列表**：当"最终结果"用，符合你的实际网络

两者都用也没问题——`sync.py` 会先把原有 `db.json` 里的源并入候选池，不会因为某次网络抖动把列表清空。

## 改数据源

### 公开源 —— `feeds.txt`

每行一个配置地址（TVBox 单仓/多仓都行，多仓会自动递归展开一层），会提交进仓库：

```
https://gcore.jsdelivr.net/gh/别人/仓库@main/config.json
```

改完 `git push` 会自动触发一次同步（workflow 里配了 `paths: feeds.txt`）。

### 私有源 —— `feeds.local.txt` / 仓库 Secret

不想公开的地址（自建服务、内网地址等）有两处可以放：

**① 本地 / NAS 上跑**：写进 `feeds.local.txt`，已被 `.gitignore` 排除，不会进仓库。

```bash
echo "https://你的私有聚合站/" >> feeds.local.txt
```

**② 让 GitHub Actions 也能用**：仓库 → Settings → Secrets and variables → Actions → New repository secret
- Name: `FEEDS_EXTRA`
- Secret: 你的地址（多个用逗号或换行分隔）

workflow 把它作为环境变量传给脚本，代码和日志里只显示 `[私有源#1]`，**地址不会出现在仓库和 Actions 日志中**。

## 自动发现（GitHub 搜索）

workflow 里设了 `DISCOVER=1`，脚本会自己搜 GitHub：

1. 搜 4 组关键词（`tvbox` / `tvbox 接口` / `影视仓` / `tvbox 配置`），只取 **≥15 星、非 fork、近期有更新**的仓
2. 每个仓：先读 README 扒里面的 `.json` 配置地址，再探测常见文件名（`tvbox.json` / `config.json` / `api.json` …）
3. 这些地址和固定源一起进候选池实测

不消耗 API 额度（只有搜索走 API，README 和文件探测都走 CDN 直连）。本地想试：

```bash
DISCOVER=1 python sync.py
```

## 内置默认源

| 源 | 说明 |
|---|---|
| `lubin776/tvbox-api-backup` | 每天抓 90+ 配置，专门提取 type 0/1 采集站 |
| `TVboxorg/TVbox` | 跨源合并 + 探测可用线路 |

## 免责

本仓库只做公开接口的收集与连通性探测，不托管、不分发任何影视内容。接口由第三方维护，可用性随时变化。
