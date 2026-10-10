# NAS BT 种子下载自动化 · 完整设计方案

> 版本 v2 · 2026-08-19 · 状态：待评审
> 核心：**BT 资源站搜索 → 磁力/种子 → qBittorrent Web API 添加下载**，人工确认 + 全自动双档。
> 与现有 video_search HLS 管线并行；归档钩子为可选收尾模块（另见 NAS_归档设计方案.md）。

---

## 1. 目标、边界与成功标准

**目标**：一条命令完成「搜站 → 挑资源 → 交给 qBittorrent 下载」，下载由 qBittorrent 完成（NAS 侧）。

**成功标准**：
1. `search_bt.py --site nyaa --query "关键词"` 输出可读列表（标题/大小/种子数/磁力摘要），人工选择后任务进入 qBittorrent
2. `--auto` 模式无人值守：符合规则（番号精确匹配或种子数最多）自动添加
3. 全程不影响现有 HLS 管线与 qBittorrent 现有功能
4. 每个失败路径有清晰错误信息（网络/凭证/站点改版/无结果）

**边界（不做）**：不做转码、不做内容截图/缩略图、不改 qBittorrent 配置（除已加的钩子键）、不做多站聚合搜索（解析器模式预留）。

---

## 2. 总体架构

```
┌─ Mac 本地 ─────────────────────────────────────────────────────┐
│  video_search/scripts/                                         │
│  ├─ search_bt.py        # 入口 CLI                            │
│  ├─ site/               # 站点解析器包                         │
│  │  ├─ base.py          #   SearchResult + Parser 接口         │
│  │  ├─ nyaa.py          #   nyaa.si RSS 实现                   │
│  │  └─ dmhy.py          #   动漫花园 (预留, 空实现抛 NotImplemented)│
│  ├─ qbt_client.py       # qBittorrent Web API 客户端            │
│  └─ config.py           # 凭证/默认值加载 (~/.qbt_auth)         │
└──────────┬─────────────────────────────────────────────────────┘
           │ HTTPS 直连 (unset 沙箱代理)
           ▼
      nyaa.si ?page=rss&q=关键词 ──→ RSS XML 解析
           │  magnet / .torrent
           ▼
┌─ NAS ──────────────────────────────────────────────────────────┐
│  qBittorrent Web API http://192.168.3.112:8999                │
│   auth/login → SID → torrents/add (urls+category)              │
│   qBittorrent 下载 → [可选] 完成钩子归档去重                    │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. 数据模型（site/base.py）

```python
@dataclass(frozen=True)
class SearchResult:
    title: str            # 完整标题 (站点原文)
    size_bytes: int       # 字节数, 解析失败 = 0
    seeders: int          # 做种数, 未知 = -1
    leechers: int         # 下载数, 未知 = -1
    publish_date: str     # "YYYY-MM-DD" 或空
    magnet: str           # magnet 链接, 缺失 = "" (fallback 下载 torrent)
    torrent_url: str      # .torrent 直链, 缺失 = ""
    site: str             # "nyaa" / "dmhy" ...

@dataclass
class ParserResult:
    items: list[SearchResult]
    total: int            # 站点返回总数
    error: str | None     # 站点级错误信息
```

**接口（每个站点实现）**：
```python
class SiteParser(Protocol):
    name: str
    def search(self, query: str, category: str = "") -> ParserResult: ...
    def resolve_magnet(self, item: SearchResult) -> str:
        """item.magnet 为空时, 下载 torrent_url 解析出 magnet; 失败抛 SiteError"""
```

---

## 4. search_bt.py CLI 设计

### 4.1 参数表

| 参数 | 默认 | 说明 |
|---|---|---|
| `--site` | `nyaa` | `nyaa` / `dmhy`（未实现报清晰错误） |
| `--query` | 必填 | 搜索关键词（番号/作品名，支持空格，自动 URL 编码） |
| `--auto` | 关 | 全自动模式；缺省为交互模式 |
| `--limit` | `10` | 展示/处理条数上限（1-50） |
| `--min-size` / `--max-size` | 不限制 | 大小过滤（支持 `2G`/`1500M`/`800MB`） |
| `--keyword-match` | 关 | 标题须包含 query（全自动模式的硬过滤） |
| `--category` | 空 | 添加到 qBittorrent 的分类（映射你的 save_path） |
| `--qb-url` | `http://192.168.3.112:8999` | qBittorrent WebUI 地址 |
| `--qb-user` / `--qb-pass` | 读 `~/.qbt_auth` | 显式传入优先于文件 |
| `--pick N` | 空 | 非交互直接选第 N 条（配合脚本化） |
| `--verbose` | 关 | 输出调试信息 |

### 4.2 交互模式终端会话示例

```
$ python3 search_bt.py --site nyaa --query "某作品 1080p"

[nyaa] 搜索 "某作品 1080p" → 共 23 条, 显示前 10 (按种子数排序)
  # | 大小    | 种子 | 日期       | 标题
  1 | 12.4 GB | 321  | 2026-08-01 | [Group] 某作品 1080p HEVC ...
  2 |  8.1 GB | 210  | 2026-07-28 | 某作品 1080p AVC ...
  3 |  1.2 GB |  55  | 2026-06-12 | 某作品 720p ...
  ...
  0 | 放弃
输入序号(可逗号分隔/区间 1-3): 1,2
[添加] #1 某作品 1080p HEVC → qBittorrent ✓ (hash=abcd...)
[添加] #2 某作品 1080p AVC  → qBittorrent ✓ (hash=ef01...)
完成: 添加 2 个任务, 失败 0
```

### 4.3 全自动模式评分算法（--auto）

```
硬过滤: min/max-size 越界剔除; --keyword-match 开启时标题不含 query 剔除
评分:   score = seeders(0 归一化, log1p 平滑) * 0.6 + 标题相关度 * 0.4
        标题相关度: query 完整出现在标题=1.0; 分词命中率=0.6; 无命中=0
        (seeders <=0 的资源视为可疑, 分数×0.3)
选择:   Top N (--limit) 按分数降序, 自动添加
```

---

## 5. 站点解析器设计

### 5.1 nyaa.si（首个实现，v1 必做）
- **接口**：`GET https://nyaa.si/?page=rss&q={urlencode(query)}`（RSS，无 JS）
- **解析**（标准库 `xml.etree.ElementTree`，注意命名空间）：
  - `<item><title>` → title
  - `<item><link>` → 详情页 URL（种子页）
  - `<item><enclosure url>` → **magnet**（RSS 直接给 magnet，主路径无需再解析 torrent）
  - `<nyaa:seeders>` / `<nyaa:leechers>` → 种子数
  - `<nyaa:size>` → 字节
  - `<nyaa:infoHash>` → hash（备用）
  - `<pubDate>` → 日期
- **分类可选**：`&c=1_0`（动画）等，v1 不默认指定，全部分类搜
- **反爬对策**：UA 模拟浏览器；请求间隔 1s；429 → 退避重试 3 次
- **torrent 直链**：RSS 里 `enclosure` 若无 magnet，用 `<item><link>` 详情页拿种子；详情页 HTML 里 `a[href$=".torrent"]`（v1 以 RSS magnet 为主路径）

### 5.2 动漫花园 dmhy（预留，v1 占位）
- 接口：`https://share.dmhy.org/topics/list?keyword={query}`
- HTML 解析（BeautifulSoup 依赖新增）；v1 抛 `NotImplementedError`，CLI 输出「站点暂未支持」

### 5.3 解析器注册表
```python
SITES = {"nyaa": nyaa.NyaaParser, "dmhy": dmhy.DmhyParser}
# 新增站点: 实现 SiteParser 后注册一行即可, 主脚本零改动
```

---

## 6. qBittorrent Web API 客户端（qbt_client.py）

| 操作 | 方法 | 端点 | 关键字段 |
|---|---|---|---|
| 登录 | `login()` | `POST /api/v2/auth/login` | form: `username,password` → 响应 `Ok.` + cookie `SID` |
| 添加 | `add_torrents(urls, category, savepath=None)` | `POST /api/v2/torrents/add` | form: `urls`（magnet 多行 `\n` 分隔）, `category`, `paused=false` |
| 确认 | `get_torrents(hashes)` | `GET /api/v2/torrents/info` | query: `hashes=hash1\|hash2` → 列表非空即成功 |
| 登出 | `logout()` | `POST /api/v2/auth/logout` | 会话结束 |

**错误处理矩阵**：
| 现象 | 判定 | 动作 |
|---|---|---|
| 登录返回非 `Ok.` | 凭证错误 | 报「登录失败: 检查 ~/.qbt_auth 或 --qb-pass」 |
| 401/403 | SID 失效 | 自动重新登录一次再重试 |
| 添加后查不到任务 | 添加失败 | 报「任务未出现在队列: 检查 magnet 是否有效」 |
| 连接拒绝 | WebUI 未起/地址错 | 报「无法连接 qBittorrent: 检查 --qb-url 与容器状态」 |

**凭证存储（config.py）**：
```ini
# ~/.qbt_auth   (chmod 600)
[qbittorrent]
url = http://192.168.3.112:8999
username = admin
password = adminadmin
```
优先级：CLI 参数 > 环境变量 `QBT_USER/QBT_PASS/QBT_URL` > 文件。

---

## 7. 交互流程与异常路径

```
main()
 ├─ 加载配置 → 解析 CLI
 ├─ 实例化站点解析器 (未知站 → 报错退出)
 ├─ parser.search(query) → ParserResult
 │    ├─ error → 报站点错误, 提示可能改版, 退出码 3
 │    └─ items 空 → 报「无结果」, 退出码 2
 ├─ 过滤+排序 (size 过滤 / seeders 降序)
 ├─ 交互模式: 打印列表 → 读输入 → 解析序号 → 提交
 │    └─ 输入非法 → 重新提示 (不崩溃)
 ├─ --auto: 评分 → 取 Top N → 提交
 ├─ qbt.login() → qbt.add_torrents() → qbt.get_torrents() 确认
 ├─ 打印结果汇总
 └─ 退出码: 0=全部成功 1=部分失败 2=无结果 3=站点错误 4=凭证错误
```

**退出码约定**（脚本化友好）：
`0` 成功 / `1` 部分失败 / `2` 无结果 / `3` 站点错误 / `4` qBittorrent 凭证或连接错误 / `5` 参数错误

---

## 8. 依赖与运行环境

- Python 3.13（现有 managed 环境：`~/.workbuddy/binaries/python/envs/default/bin/python`）
- 依赖：`requests`（HTTP）；解析用标准库 `xml.etree`（nyaa RSS）
- 新增依赖策略：v1 只加 `requests`；dmhy 需要时再评估 `beautifulsoup4`
- 网络：Mac 直连 nyaa.si；**沙箱运行必须 unset 代理**（入口脚本内 `os.environ.pop` HTTP(S)/ALL_PROXY，已验证可行）
- 与现有管线共存：`video_search/scripts/` 下新增文件，不触碰 search_exact.py/download_browser.py 等

---

## 9. 测试计划

| 层级 | 内容 | 方法 |
|---|---|---|
| 单元 | 评分算法、大小解析（`2G`/`1500M`）、序号输入解析 | pytest + 纯函数 |
| 解析 mock | nyaa RSS 样例 XML → SearchResult 字段正确 | fixtures 固定 RSS |
| qb mock | 本地 mock HTTP 服务模拟 /api/v2（登录/添加/info） | python http.server 或 responses |
| 真站 | nyaa 搜索一个关键词，验证列表字段非空 | 手动 + 网络可用 |
| 端到端 | 小文件种子 → 添加 → qBittorrent 队列出现 → 下载完成 → 钩子归档 | 真实链路 |
| 归档收尾 | busybox stat 修复版上传后幂等重测（第二次触发应 skip 非 _1） | NAS 容器内 |

**测试用例清单（关键）**：
1. `--min-size 2G` 过滤掉 1.2GB 结果
2. 交互输入 `1,2` / `1-3` / 非法 `abc` → 行为正确
3. magnet 缺失时 fallback 下载 .torrent 解析
4. 登录失败 → 退出码 4 + 清晰报错
5. 添加成功但队列无任务 → 报「任务未出现在队列」
6. 幂等：同一内容二次触发归档 → skip（防 _1 重复）

---

## 10. 部署与使用步骤

```bash
# 1. 安装依赖 (managed venv)
pip install requests

# 2. 写凭证
echo -e "[qbittorrent]\nurl = http://192.168.3.112:8999\nusername = admin\npassword = adminadmin" > ~/.qbt_auth && chmod 600 ~/.qbt_auth

# 3. 使用 (交互)
python3 search_bt.py --site nyaa --query "关键词"
# 4. 使用 (全自动, 带分类)
python3 search_bt.py --site nyaa --query "关键词" --auto --category "Group-观看"
```

---

## 11. 里程碑与交付清单

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 骨架 | base.py / config.py / qbt_client.py / search_bt.py CLI | 参数解析+mock 测试过 |
| M2 首个站点 | nyaa.py 解析器 | 真站搜索出列表 |
| M3 链路打通 | 搜索 → 添加 → qBittorrent 队列 | 小文件端到端 |
| M4 全自动 | 评分+过滤+--auto | 规则用例过 |
| M5 收尾 | 归档钩子幂等重测 + 测试残留清理 + 文档 | 全部通过 |

**交付物**：`search_bt.py`、`site/{base,nyaa,dmhy}.py`、`qbt_client.py`、`config.py`、README 段落更新。

---

## 12. 风险与回滚

| 风险 | 等级 | 缓解/回滚 |
|---|---|---|
| nyaa 改版/封锁 | 中 | 解析器隔离；RSS 稳定多年；失败时报错清晰 |
| WebUI 凭证错误 | 低 | 退出码 4 + 提示改 ~/.qbt_auth |
| 误加任务（--auto） | 低 | 硬过滤 + 评分；--keyword-match 强制标题包含 |
| 沙箱代理劫持 | 已解决 | 入口统一 unset 代理（已验证） |
| 归档模块 busybox 兼容 | 低 | stat 修复完成，M5 重测闭环 |
| 全部可回滚 | — | 新增脚本不触碰现有文件；NAS 侧无新增持久改动 |

---
**待确认项**（不阻塞方案评审）：① 首个站点 nyaa vs dmhy vs 其他；② qBittorrent WebUI 凭证是否仍为 admin/adminadmin（M1 会用登录接口验证）。确认后按 M1→M5 推进。
