# 音乐平台排行榜采集 (music_charts)

## 目标

采集网易云音乐、QQ音乐、Apple Music、Billboard 四个平台的排行榜歌曲基本信息
(歌名、专辑、歌手),以及附加信息(评论数、热度/播放量,平台不支持的字段留空)。
不涉及登录态、不涉及下载音频,只做榜单元数据抓取。

## 范围

覆盖平台与榜单:

| 平台 | 主榜 | 分类榜(国语/粤语/欧美) | 获取方式 |
|---|---|---|---|
| 网易云音乐 | 热歌榜 | 内地榜 / 粤语榜 / 欧美榜(固定 playlist id) | 非官方公开接口(playlist detail),免鉴权 |
| QQ音乐 | 流行指数榜 | 内地榜 / 粤语榜 / 欧美榜(固定 topid) | 非官方公开接口(toplist),免鉴权 |
| Apple Music | 各国 Top Songs | storefront=cn/hk/us 对应国语/粤语/欧美 | 官方免费 RSS Feed API,免鉴权 |
| Billboard | Hot 100 | 无(Billboard 无国语/粤语/欧美细分) | 网页抓取 billboard.com,无官方免费 API |

Spotify 暂不实现(需要付费/需注册开发者账号,用户明确要求跳过)。后续如需加入,
按同样的 fetcher 接口补一个 `fetchers/spotify.py` 即可。

## 目录结构

```
music_charts/
  SKILL.md                   # 使用说明(何时用、怎么用、已知限制)
  fetchers/
    __init__.py
    netease.py                # 网易云音乐 fetcher
    qqmusic.py                 # QQ音乐 fetcher
    apple_music.py              # Apple Music fetcher
    billboard.py                 # Billboard fetcher
  scripts/
    fetch_charts.py              # CLI 入口,编排 fetcher,写 JSON
  output/                         # 运行产物,.gitignore 忽略
```

## 数据模型

每首歌统一输出为一条记录,平台不支持的字段填 `null`:

```json
{
  "platform": "netease",
  "chart": "内地榜",
  "rank": 1,
  "title": "歌名",
  "artist": "歌手",
  "album": "专辑",
  "play_count": 12345678,
  "comment_count": 9876,
  "popularity": null,
  "source_url": "https://...",
  "fetched_at": "2026-09-03T10:00:00+08:00"
}
```

字段可用性:

- `play_count` / `comment_count`:网易云音乐、QQ音乐可获取;Apple Music、Billboard 为 `null`。
- `popularity`:四个平台均无直接可用的"热度分数"字段,统一为 `null`(为后续接入
  Spotify 的 track popularity 预留位置)。
- `source_url`:该曲目在对应平台的详情页/播放页链接,便于人工核对。

## 各 fetcher 实现要点

### netease.py

- 通过网易云音乐非官方公开接口按 playlist id 拉取榜单详情(热歌榜、内地榜、
  粤语榜、欧美榜四个固定 playlist id,写在模块常量里)。
- 榜单歌曲列表接口本身通常不直接带 `playCount`/评论数,需要对每首歌再调一次
  详情/评论接口取得这两个字段——每首歌一次请求,注意限速(每次请求间 sleep
  一小段时间),避免被限流或封禁。
- 网络请求需要设置常见浏览器 User-Agent 请求头(接口对无 UA 的请求可能拒绝)。

### qqmusic.py

- 通过 QQ音乐非官方公开 toplist 接口按 topid 拉取榜单(流行指数榜、内地榜、
  粤语榜、欧美榜四个固定 topid)。
- 榜单接口本身可能已经带有播放数/热度相关字段,评论数需要额外调用评论接口
  按歌曲 id 查询,同样注意限速。
- 同样需要设置常见浏览器 UA、可能还需要 `Referer` 头。

### apple_music.py

- 调用官方免费 RSS Feed:
  `https://rss.applemarketingtools.com/api/v2/{storefront}/music/most-played/{limit}/songs.json`
- storefront 分别用 `cn`(国语)、`hk`(粤语)、`us`(欧美/主榜)。
- 无需鉴权、无限速要求(但仍加基本的请求间隔,做个良好公民)。
- 返回数据里没有播放量/评论数/热度,这三个字段固定为 `null`。

### billboard.py

- 抓取 `https://www.billboard.com/charts/hot-100/` 页面 HTML,解析榜单结构
  (排名、歌名、歌手)。没有专辑、播放量、评论数、热度字段,均为 `null`。
- 页面结构可能随官网改版变化,解析逻辑要有清晰的失败提示(而不是静默返回空
  列表),方便后续手动修复选择器。
- 需要设置常见浏览器 UA,避免被网站的基础反爬拦截。

## CLI 设计 (scripts/fetch_charts.py)

```bash
python3 scripts/fetch_charts.py --platform all
python3 scripts/fetch_charts.py --platform netease,qqmusic
```

- `--platform`:逗号分隔,可选值 `netease,qqmusic,apple_music,billboard,all`,
  默认 `all`。
- 每个平台每个榜单各自独立写一个 JSON 文件到
  `music_charts/output/<platform>_<chart>_<YYYYMMDD>.json`(chart 用英文或拼音
  短标识,如 `netease_neidi_20260903.json`)。
- 单个平台/榜单抓取失败不应中断整体运行——记录错误、跳过、继续下一个,最后
  打印一份"成功/失败清单"汇总。
- 不做历史去重/增量对比,每次运行就是当天的完整快照。

## 错误处理

- 网络请求失败、接口返回结构不符合预期:捕获异常,该榜单标记失败,不写文件,
  继续下一个,不让整体运行崩溃。
- 不重试无限次——每个请求最多重试 1 次(等待几秒后),超过则放弃该项。

## 测试方式

- 无法做严格的单元测试(依赖真实外部接口/页面结构),采用手动运行验证:
  对每个平台各跑一次,人工检查输出 JSON 里字段是否合理(歌名/歌手/专辑非空、
  排名连续、可用字段有值)。
- 后续如果某平台接口/页面结构变化导致解析失败,报错信息需要足够定位问题
  (是网络错误还是解析逻辑对不上新结构)。

## 不做的事

- 不做定时任务/调度(用户手动运行）。
- 不做历史数据存储/趋势分析。
- 不做 Spotify(用户明确跳过)。
- 不下载音频、不涉及登录态。
