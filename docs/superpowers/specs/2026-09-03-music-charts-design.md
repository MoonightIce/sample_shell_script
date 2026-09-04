# 音乐平台排行榜采集 (music_charts)

## 目标

采集网易云音乐、QQ音乐、Apple Music、Billboard 四个平台的排行榜歌曲基本信息
(歌名、专辑、歌手),以及附加信息(评论数、热度/播放量,平台不支持的字段留空)。
不涉及登录态、不涉及下载音频,只做榜单元数据抓取。

## 范围

覆盖平台与榜单(以下 id/接口均已实测验证):

| 平台 | 榜单 | 获取方式 |
|---|---|---|
| 网易云音乐 | 热歌榜(playlist id 3778678,当国语/主榜) + 欧美热歌榜(playlist id 2809513713) | 非官方公开接口(`/api/v6/playlist/detail`),免鉴权 |
| QQ音乐 | 流行指数榜(topid 4,主榜) + 内地榜(topid 5,国语) + 香港地区榜(topid 59,粤语) + 欧美榜(topid 3,欧美) | 非官方公开接口(`fcg_v8_toplist_cp.fcg`),免鉴权 |
| Apple Music | Top Songs,storefront=us(欧美/主榜)、cn(国语)、hk(粤语) | 官方免费 RSS Feed API + iTunes lookup API 补专辑名,免鉴权 |
| Billboard | Hot 100 | 网页抓取 billboard.com,无官方免费 API |

Spotify 暂不实现(需要付费/需注册开发者账号,用户明确要求跳过)。后续如需加入,
按同样的 fetcher 接口补一个 `fetchers/spotify.py` 即可。

**网易云音乐没有精确匹配的"粤语榜"**(实测其官方榜单列表里没有粤语相关榜单),
按用户确认,网易云音乐就只做热歌榜(国语/主榜)+ 欧美热歌榜两个,不勉强凑粤语。

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
  "chart": "热歌榜",
  "rank": 1,
  "title": "歌名",
  "artist": "歌手",
  "album": "专辑",
  "play_count": null,
  "comment_count": 9876,
  "popularity": 100.0,
  "source_url": "https://music.163.com/#/song?id=3399839173",
  "fetched_at": "2026-09-03T10:00:00+08:00"
}
```

字段可用性(实测结果,平台/榜单之间不完全一致):

- 网易云音乐:`comment_count` 有真实值(评论总数接口已验证);`play_count` 拿不到
  (公开接口不提供单曲播放数),固定 `null`;`popularity` 用歌曲详情接口自带的
  `pop` 字段(0-100 热度分)。
- QQ音乐:`comment_count` 拿不到(评论接口测试返回恒为 0/无版权),固定
  `null`;`popularity`/`play_count` 只有"流行指数榜"(主榜,topid 4)有真实的
  `cur_count` 热度值,内地榜/香港地区榜/欧美榜(topid 5/59/3)这三个榜单该字段
  是恒为 1 的占位值,视为不可用,固定 `null`。
- Apple Music:`play_count`/`comment_count`/`popularity` 均拿不到,固定 `null`。
- Billboard:`play_count`/`comment_count`/`popularity` 均拿不到,固定 `null`;
  另外 Billboard 页面本身不提供专辑信息,`album` 也固定 `null`。
- `source_url`:该曲目在对应平台的详情页/播放页链接,便于人工核对。

## 各 fetcher 实现要点

### netease.py

- 榜单常量:`{"热歌榜": 3778678, "欧美热歌榜": 2809513713}`。
- 第一步:对每个 playlist id 请求
  `GET https://music.163.com/api/v6/playlist/detail?id={id}`(需要
  `User-Agent` 请求头,否则可能被拒绝),从响应 `playlist.trackIds` 里取出全部
  歌曲 id 列表(按榜单顺序,即排名顺序)。
- 第二步:把 id 列表分批(每批建议 <= 100 个)传给
  `GET https://music.163.com/api/v3/song/detail?c=[{"id":1},{"id":2},...]`
  (`c` 参数是 JSON 数组字符串,需要 URL 编码),批量拿到 `name`(歌名)、
  `ar[].name`(歌手,多个用 `/` 连接)、`al.name`(专辑)、`pop`(0-100 热度,
  填入 `popularity`)。
- 第三步:只对**前 N 首**(CLI `--limit`,默认 50,避免跑太久)逐首请求
  `GET https://music.163.com/api/v1/resource/comments/R_SO_4_{songId}?limit=1&offset=0`,
  取响应体的 `total` 字段填入 `comment_count`;超出 `--limit` 的歌曲
  `comment_count` 为 `null`。每次请求间 sleep 0.3~0.5 秒。
- `play_count` 固定 `null`(公开接口不提供单曲播放数)。
- `source_url` 用 `https://music.163.com/#/song?id={songId}`。

### qqmusic.py

- 榜单常量:`{"流行指数榜": 4, "内地榜": 5, "香港地区榜": 59, "欧美榜": 3}`。
- 请求
  `GET https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_cp.fcg?format=json&topid={topid}&type=top&page=detail&tpl=3&needNewCode=1`,
  需要 `User-Agent` 和 `Referer: https://y.qq.com/` 请求头。
- 响应 `songlist` 数组即为完整排名列表(数组顺序 = 排名),每项取
  `data.songname`(歌名)、`data.singer[].name`(歌手,多个用 `/` 连接)、
  `data.albumname`(专辑)、`data.songmid`(拼 `source_url` 用)。
- `popularity`/`play_count`:**只有 topid=4(流行指数榜)** 时,取该项的
  `cur_count` 字段(转成数字)填入两者;topid 为 5/59/3 时该字段是恒为 `"1"`
  的占位值,视为不可用,固定 `null`。
- `comment_count` 固定 `null`(评论接口实测拿不到有效数据,详见调研记录)。
- `source_url` 用 `https://y.qq.com/n/ryqq/songDetail/{songmid}`。

### apple_music.py

- 榜单常量:`{"us": "欧美/主榜", "cn": "国语", "hk": "粤语"}`(storefront -> 榜单
  名)。
- 第一步:对每个 storefront 请求
  `GET https://rss.applemarketingtools.com/api/v2/{storefront}/music/most-played/{limit}/songs.json`
  (需要 `-L` 跟随重定向,该域名会 301 到 `rss.marketingtools.apple.com`;
  `requests` 默认跟随重定向,无需特殊处理),`results` 数组顺序即排名顺序,
  取每项的 `id`(track id)、`name`(歌名)、`artistName`(歌手)、`url`
  (详情页链接,填 `source_url`)。
- 第二步:把这一批 `id` 用逗号拼接,一次性请求
  `GET https://itunes.apple.com/lookup?id={id1},{id2},...&country={storefront}`
  拿到每个 track 的 `collectionName`(专辑名),按 `trackId` 匹配回第一步的
  歌曲列表填入 `album`。
- `play_count`/`comment_count`/`popularity` 固定 `null`。
- 无需鉴权、无限速要求(每个 storefront 之间加 0.3~0.5 秒间隔,做个良好公民)。

### billboard.py

- 抓取 `https://www.billboard.com/charts/hot-100/` 页面 HTML(需要设置浏览器
  `User-Agent`,避免被基础反爬拦截),用 BeautifulSoup 解析。
- 每一行榜单是一个 `<ul class="o-chart-results-list-row ...">`;在其中:
  - 排名:第一个 `<span class="c-label a-font-basic ...">` 的文本(strip 后转
    `int`)。
  - 歌名:`<h3 class="c-title ...">` 的文本(strip)。
  - 歌手:`<span class="c-label a-no-trucate ...">` 内部 `<a>` 标签的文本
    (strip);如果没有 `<a>` 就用该 span 本身的文本。
- `album`/`play_count`/`comment_count`/`popularity` 均固定 `null`(页面本身不
  提供这些信息)。
- `source_url` 固定为 `https://www.billboard.com/charts/hot-100/`(页面没有
  单曲详情链接)。
- 页面结构可能随官网改版变化——如果找不到任何 `o-chart-results-list-row`
  元素,要抛出明确的异常(如 `RuntimeError("billboard hot-100 页面结构解析失败,可能改版")`),
  不能静默返回空列表。

## CLI 设计 (scripts/fetch_charts.py)

```bash
python3 scripts/fetch_charts.py --platform all
python3 scripts/fetch_charts.py --platform netease,qqmusic
```

- `--platform`:逗号分隔,可选值 `netease,qqmusic,apple_music,billboard,all`,
  默认 `all`。
- `--limit`:整数,默认 50。含义因平台而异:网易云音乐用它控制"逐首查评论数"
  的歌曲数量;其余平台用它截断每个榜单只保留前 N 名(Apple Music 直接传给
  RSS Feed 的 `{limit}` 路径参数;QQ音乐/Billboard 拿到完整榜单后在本地截断)。
- 每个平台每个榜单各自独立写一个 JSON 文件到
  `music_charts/output/<platform>_<chart_slug>_<YYYYMMDD>.json`,chart_slug 用
  英文短标识,例如:`netease_hot.json` → `netease_hot_20260903.json`、
  `qqmusic_neidi_20260903.json`、`qqmusic_hongkong_20260903.json`、
  `qqmusic_oumei_20260903.json`、`qqmusic_index_20260903.json`、
  `apple_music_us_20260903.json`、`apple_music_cn_20260903.json`、
  `apple_music_hk_20260903.json`、`billboard_hot100_20260903.json`。
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
