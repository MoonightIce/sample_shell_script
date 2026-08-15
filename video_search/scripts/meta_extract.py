#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meta_extract.py — 详情页元信息抓取与归一化。

职责: 从视频详情页提取 标题/描述/分类(genre)/系列(series)/演员(actress)/
      制作商(maker)/字幕标记 等元信息, 并归一化为统一 dict。

两级抓取:
  1) extract_meta_from_html(html, url)  — 纯函数, 用 BeautifulSoup 解析 HTML
     (可离线单测, 不依赖浏览器)
  2) fetch_meta_in_page(page)           — 在 Playwright 页面上下文内抓取原始素材
     (处理动态渲染站点), 然后交 normalize_meta() 归一化

字幕:
  - subtitles: 页面上的字幕标记文本(如"中文字幕"/"字幕")
  - subtitle_tracks: HLS master 里的 #EXT-X-MEDIA:TYPE=SUBTITLES 轨
    (由 download_browser.py 解析 m3u8 后传入, 本模块负责归一化)

用法(库模块):
    from meta_extract import extract_meta_from_html, fetch_meta_in_page
"""
import json
import re
import time
from datetime import datetime, timezone

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


# ---------- 纯解析: HTML -> 原始素材 ----------

def _clean(t: str) -> str:
    """清洗: 去空白/控制字符, 压缩多余空格。"""
    if not t:
        return ""
    return re.sub(r"\s+", " ", t).strip()


def _strip_site_suffix(title: str) -> str:
    """去掉标题里的站点名后缀, 如 'XX - missav' / 'XX | missav'。"""
    t = _clean(title)
    for sep in (" - ", " | ", " – ", " — "):
        if sep in t:
            t = t.rsplit(sep, 1)[0]
    return t.strip()


def _parse_jsonld(soup):
    """解析 JSON-LD, 返回对象列表(失败项跳过)。"""
    out = []
    if soup is None:
        return out
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except Exception:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict):
                out.append(d)
    return out


def _pick_jsonld(blocks, types=("VideoObject", "Movie", "CreativeWork", "TVSeries")):
    """从 JSON-LD 块里挑出目标类型, 返回 (类型, 块) 或 (None, None)。"""
    if not blocks:
        return None, None
    # 优先具体类型
    for d in blocks:
        t = d.get("@type")
        t = t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else "")
        if t in types:
            return t, d
    # 兜底: 取第一个含 name/description 的块
    for d in blocks:
        if d.get("name") or d.get("description"):
            return d.get("@type", ""), d
    return None, None


def _names(v):
    """把 actor/director 等字段归一化为字符串列表(兼容 str / dict / list)。"""
    out = []
    if v is None:
        return out
    items = v if isinstance(v, list) else [v]
    for it in items:
        if isinstance(it, str):
            s = _clean(it)
        elif isinstance(it, dict):
            s = _clean(it.get("name") or it.get("alternateName") or "")
        else:
            s = ""
        if s and s not in out:
            out.append(s)
    return out


def _extract_links(soup, url=""):
    """从页面链接提取 genre/series/actress/maker/tag 分类链接的文本列表。"""
    res = {"genre": [], "series": [], "actress": [], "maker": [], "tag": []}
    if soup is None:
        return res
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = _clean(a.get_text())
        if not text or len(text) > 80:
            continue
        if "/genre/" in href:
            key = "genre"
        elif "/series/" in href:
            key = "series"
        elif "/actress/" in href:
            key = "actress"
        elif "/maker/" in href:
            key = "maker"
        elif "/tag/" in href or "/tags/" in href:
            key = "tag"
        else:
            continue
        if text not in res[key]:
            res[key].append(text)
    return res


def _extract_subtitle_tags(soup):
    """页面上的字幕标记(常见: 中文字幕/英文字幕/字幕/Subtitle)。"""
    tags = set()
    if soup is None:
        return []
    keywords = ("中文字幕", "英文字幕", "繁體字幕", "字幕", "Subtitle", "subtitle")
    for el in soup.find_all(["span", "div", "li", "button", "p"]):
        t = _clean(el.get_text())
        if not t or len(t) > 30:
            continue
        for k in keywords:
            if t == k or t.endswith(k):
                tags.add(t)
                break
    return sorted(tags)


# ---------- 归一化 ----------

def normalize_meta(raw: dict, page_url: str = "", title_fallback: str = "") -> dict:
    """把原始素材(HTML解析或页面抓取的结果)归一化为统一 dict。"""
    raw = raw or {}
    links = raw.get("links") or {}
    jsonld_blocks = raw.get("jsonld") or []
    jtype, jd = _pick_jsonld(jsonld_blocks)

    # 标题: og:title > JSON-LD name > 传入 fallback(title标签)
    title = _clean(raw.get("og:title") or raw.get("ogTitle") or "")
    if not title and jd:
        title = _clean(jd.get("name") or "")
    if not title:
        title = _strip_site_suffix(title_fallback or raw.get("title") or "")
    if not title:
        title = _clean(raw.get("h1") or "")

    # 描述: og:description > meta description > JSON-LD description > 页面 desc 文本
    desc = _clean(raw.get("og:description") or raw.get("ogDescription") or "")
    if not desc:
        desc = _clean(raw.get("meta_description") or raw.get("description") or "")
    if not desc and jd:
        desc = _clean(jd.get("description") or "")
    if not desc:
        desc = _clean(raw.get("page_desc") or "")

    # 分类/系列/演员/制作商: 链接优先, JSON-LD 兜底
    genres = [g for g in links.get("genre", []) if g]
    series = [s for s in links.get("series", []) if s]
    actresses = [a for a in links.get("actress", []) if a]
    makers = [m for m in links.get("maker", []) if m]
    if jd:
        if not genres:
            genres = [g for g in _names(jd.get("genre")) if g]
        if not actresses:
            actresses = [a for a in _names(jd.get("actor")) if a]
        if not makers and jd.get("publisher"):
            makers = _names(jd.get("publisher"))
        if not series and jd.get("partOfSeries"):
            s = jd.get("partOfSeries")
            if isinstance(s, dict):
                series = [_clean(s.get("name") or "")] if s.get("name") else []
            else:
                series = _names(s)

    # 字幕
    subtitle_tags = [s for s in (raw.get("subtitle_tags") or []) if s]
    subtitle_tracks = raw.get("subtitle_tracks") or []
    if isinstance(subtitle_tracks, dict):
        subtitle_tracks = list(subtitle_tracks.values())

    meta = {
        "title": title,
        "description": desc,
        "categories": genres,
        "series": series,
        "actress": actresses,
        "maker": makers,
        "subtitles": subtitle_tags,          # 页面字幕标记文本
        "subtitle_tracks": subtitle_tracks,  # m3u8 字幕轨 [{uri,name,lang}]
        "jsonld_type": jtype or "",
        "page_url": page_url,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return meta


# ---------- 浏览器上下文抓取 ----------

PAGE_EXTRACT_JS = r"""
() => {
  const res = {
    title: document.title || '',
    h1: (document.querySelector('h1')||{}).textContent || '',
    jsonld: [], meta: {},
    links: {genre:[], series:[], actress:[], maker:[], tag:[]},
    subtitle_tags: [], page_desc: ''
  };
  // JSON-LD
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s=>{
    try { res.jsonld.push(JSON.parse(s.textContent)); }
    catch(e) {}
  });
  // meta
  document.querySelectorAll('meta').forEach(m=>{
    const k = m.getAttribute('name') || m.getAttribute('property');
    const c = m.getAttribute('content');
    if (k && c) res.meta[k] = c.slice(0, 2000);
  });
  // 分类链接
  document.querySelectorAll('a[href]').forEach(a=>{
    const h = a.href, t = (a.textContent||'').trim().replace(/\s+/g,' ');
    if (!t || t.length > 80) return;
    if (/\/genre\//.test(h)) res.links.genre.push(t);
    else if (/\/series\//.test(h)) res.links.series.push(t);
    else if (/\/actress\//.test(h)) res.links.actress.push(t);
    else if (/\/maker\//.test(h)) res.links.maker.push(t);
    else if (/\/tag(s)?\//.test(h)) res.links.tag.push(t);
  });
  // 字幕标记
  const kw = ['中文字幕','英文字幕','繁體字幕','字幕','Subtitle','subtitle'];
  document.querySelectorAll('span,div,li,button,p').forEach(e=>{
    const t = (e.textContent||'').trim().replace(/\s+/g,' ');
    if (t && t.length <= 30 && kw.some(k=>t===k || t.endsWith(k))) {
      if (res.subtitle_tags.indexOf(t) < 0) res.subtitle_tags.push(t);
    }
  });
  // 描述兜底: 页面中最长的一段可见文本(排除导航/链接簇)
  let best = '';
  document.querySelectorAll('p, [class*=desc], [class*=detail], [class*=info]').forEach(e=>{
    const t = (e.textContent||'').trim().replace(/\s+/g,' ');
    if (t.length > best.length && t.length < 3000 && !/^(©|Copyright|About|Home)/i.test(t)) best = t;
  });
  res.page_desc = best;
  // 去重链接
  Object.keys(res.links).forEach(k=>{ res.links[k] = [...new Set(res.links[k])]; });
  return res;
}
"""


async def fetch_meta_in_page(page) -> dict:
    """在 Playwright 页面上下文内抓取原始素材。返回 normalize_meta 可直接消费的 dict。"""
    raw = await page.evaluate(PAGE_EXTRACT_JS)
    meta = raw.get("meta") or {}
    links = raw.get("links") or {}
    # 链接去重(大小写不敏感)
    for k, v in links.items():
        seen, out = set(), []
        for x in v:
            key = x.lower()
            if key not in seen:
                seen.add(key)
                out.append(x)
        links[k] = out
    return {
        "title": raw.get("title", ""),
        "h1": raw.get("h1", ""),
        "jsonld": raw.get("jsonld", []),
        "og:title": meta.get("og:title", ""),
        "og:description": meta.get("og:description", ""),
        "meta_description": meta.get("description", ""),
        "links": links,
        "subtitle_tags": raw.get("subtitle_tags", []),
        "page_desc": raw.get("page_desc", ""),
    }


def extract_meta_from_html(html: str, page_url: str = "", title_fallback: str = "") -> dict:
    """纯函数: 解析 HTML 文本, 返回归一化元信息 dict。"""
    soup = BeautifulSoup(html, "html.parser") if BeautifulSoup else None
    raw = {
        "title": "",
        "h1": _clean(soup.find("h1").get_text()) if soup and soup.find("h1") else "",
        "jsonld": _parse_jsonld(soup),
        "og:title": "",
        "og:description": "",
        "meta_description": "",
        "links": _extract_links(soup, page_url),
        "subtitle_tags": _extract_subtitle_tags(soup),
        "page_desc": "",
    }
    if soup:
        for m in soup.find_all("meta"):
            k = m.get("name") or m.get("property") or ""
            c = m.get("content") or ""
            if k == "og:title":
                raw["og:title"] = c
            elif k == "og:description":
                raw["og:description"] = c
            elif k == "description":
                raw["meta_description"] = c
        # 描述兜底
        best = ""
        for el in soup.find_all(["p", "[class*=desc]", "[class*=detail]", "[class*=info]"]):
            t = _clean(el.get_text())
            if len(t) > len(best) and len(t) < 3000:
                best = t
        raw["page_desc"] = best
    return normalize_meta(raw, page_url, title_fallback)


def merge_subtitle_tracks(meta: dict, tracks: list) -> dict:
    """把 m3u8 解析出的字幕轨并入已归一化元信息(去重)。tracks: [{uri,name,lang}]"""
    if not tracks:
        return meta
    exist = {(t.get("uri") or "") for t in meta.get("subtitle_tracks", [])}
    for t in tracks:
        if t.get("uri") and t["uri"] not in exist:
            exist.add(t["uri"])
            meta.setdefault("subtitle_tracks", []).append(t)
    return meta


# ---------- CLI(自测) ----------

if __name__ == "__main__":
    import sys
    sample = """<html><head>
      <title>ABCD-123 示例标题 - missav</title>
      <meta property="og:title" content="ABCD-123 示例标题">
      <meta property="og:description" content="一段描述文字。">
      <meta name="description" content="meta 描述">
      <script type="application/ld+json">{"@type":"VideoObject","name":"ABCD-123 JSONLD名","description":"JSONLD描述","genre":["G1","G2"],"actor":[{"name":"演员A"},{"name":"演员B"}]}</script>
    </head><body>
      <h1>ABCD-123 示例标题</h1>
      <a href="/genre/浪漫">浪漫</a><a href="/genre/剧情">剧情</a>
      <a href="/series/系列X">系列X</a>
      <a href="/actress/演员A">演员A</a>
      <a href="/maker/厂商Y">厂商Y</a>
      <span>中文字幕</span>
      <p>这是一段很长的页面描述文本, 用于兜底抓取。这是一段很长的页面描述文本, 用于兜底抓取。这是一段很长的页面描述文本, 用于兜底抓取。</p>
    </body></html>"""
    m = extract_meta_from_html(sample, "https://missav.ws/abcd123")
    print(json.dumps(m, ensure_ascii=False, indent=2))
