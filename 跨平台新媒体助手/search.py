# -*- coding: utf-8 -*-
"""Web search and image search.

Primary providers are accessible in the local network (China): Baidu web search
with region-based snippets, Baidu image search (JSON), and Sogou WeChat search
for finding WeChat articles. Bing is kept as a fallback. Pages are untrusted
data: the parser only extracts titles/links/snippets, never executes scripts.
"""
from __future__ import annotations

import html as html_mod
import http.cookiejar
import json
import re
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import http.client


class SearchError(ValueError):
    """Safe-to-display search error."""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# A cookie-enabled session is needed for Baidu image search (it requires a
# BAIDUID obtained from the image homepage before the JSON API responds).
_JAR = http.cookiejar.CookieJar()
_SESSION = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_JAR),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()))
_PRIMED = False
_SESSION_LOCK = threading.Lock()


def _opener():
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def _prime_session():
    global _PRIMED
    with _SESSION_LOCK:
        if _PRIMED:
            return
        try:
            home = urllib.request.Request("https://image.baidu.com/",
                                          headers={"User-Agent": UA})
            _SESSION.open(home, timeout=15).read(1000)
        except Exception:
            pass
        _PRIMED = True


def _http_get(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        **(headers or {}),
    })
    with _opener().open(req, timeout=timeout) as resp:
        return resp.read(5 * 1024 * 1024)


# ============================= Web search =============================

def _strip_tags(seg):
    seg = re.sub(r"(?is)<script.*?</script>", "", seg)
    seg = re.sub(r"(?is)<style.*?</style>", "", seg)
    txt = re.sub(r"<[^>]+>", "", seg)
    return re.sub(r"\s+", " ", html_mod.unescape(txt)).strip()


def _resolve_baidu_link(link):
    """Follow a baidu redirect to the real URL; tolerate failure."""
    try:
        req = urllib.request.Request(html_mod.unescape(link),
                                      headers={"User-Agent": UA})
        with _opener().open(req, timeout=12) as r:
            return r.url
    except Exception:
        return html_mod.unescape(link)


def _baidu_search(query, max_results):
    url = "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query})
    try:
        data = _http_get(url).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise SearchError("网页搜索连接失败，请检查网络后重试。") from exc

    # Each result title is an <h3 ...> ... <a href="...">title</a> ... </h3>.
    title_iter = list(re.finditer(r"<h3[^>]*>.*?<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>.*?</h3>",
                                  data, re.S))
    results = []
    for i, m in enumerate(title_iter):
        title = _strip_tags(m.group(2))
        link = html_mod.unescape(m.group(1))
        # snippet region = between this title and the next title
        start = m.end()
        end = title_iter[i + 1].start() if i + 1 < len(title_iter) else start + 4000
        snippet = _strip_tags(data[start:end])
        # remove obvious tracking noise and truncate
        snippet = re.sub(r"^(?:百度首页|登录|注册|新闻|网页|贴吧|知道|视频|图片)\s*",
                         "", snippet)
        if not link.startswith("http"):
            continue
        real_link = _resolve_baidu_link(link) if "baidu.com/link" in link else link
        results.append({
            "title": title, "url": real_link,
            "snippet": snippet[:300], "source": "baidu",
        })
        if len(results) >= max_results:
            break
    return results


def _sogou_wechat(query, max_results):
    url = "https://weixin.sogou.com/weixin?" + urllib.parse.urlencode({
        "type": "2", "query": query, "ie": "utf8"})
    try:
        data = _http_get(url).decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    # WeChat article cards carry <h3 ...><a ...>title</a></h3> and an account.
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                         data, re.S):
        link = html_mod.unescape(m.group(1))
        if link.startswith("/"):
            link = "https://weixin.sogou.com" + link
        title = _strip_tags(m.group(2))
        out.append({"title": title, "url": link, "snippet": "",
                    "source": "sogou-wechat"})
        if len(out) >= max_results:
            break
    return out


def _bing_search(query, max_results):
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({
        "q": query, "count": max_results + 5, "setlang": "zh-CN"})
    try:
        data = _http_get(url).decode("utf-8", "replace")
    except Exception:
        return []
    results = []
    for block in re.findall(r'<li class="b_algo".*?</li>', data, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                      block, re.S)
        if not m:
            continue
        title = _strip_tags(m.group(2))
        snippet = ""
        sm = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        if sm:
            snippet = _strip_tags(sm.group(1))
        link = html_mod.unescape(m.group(1))
        if link.startswith("http"):
            results.append({"title": title, "url": link, "snippet": snippet,
                            "source": "bing"})
        if len(results) >= max_results:
            break
    return results


def web_search(query, max_results=6, settings=None, scope="web"):
    query = str(query or "").strip()
    if not query:
        raise SearchError("请输入搜索关键词。")
    settings = settings or {}
    if scope == "wechat":
        out = _sogou_wechat(query, max_results)
        if out:
            return out
    # Primary: Baidu; fall back to Bing if it comes back empty.
    try:
        results = _baidu_search(query, max_results)
    except Exception:
        results = []
    if results:
        return results
    return _bing_search(query, max_results)


# ============================ Image search ============================

def _baidu_images(query, max_results, pn=0):
    _prime_session()
    url = "https://image.baidu.com/search/acjson?" + urllib.parse.urlencode({
        "tn": "resultjson_com", "word": query, "rn": max(10, max_results + 5),
        "ie": "utf-8", "oe": "utf-8", "pn": pn,
    })
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Referer": "https://image.baidu.com/search/index?tn=baiduimage&word="
                       + urllib.parse.quote(query)})
        raw = _SESSION.open(req, timeout=20).read().decode(
            "utf-8", "replace")
        data = json.loads(raw)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SearchError("图片搜索连接失败，请检查网络后重试。") from exc
    out = []
    for item in data.get("data", []):
        murl = item.get("middleURL") or item.get("thumbURL")
        if not murl or not murl.startswith("http"):
            continue
        try:
            w = int(item.get("width", 0))
            h = int(item.get("height", 0))
        except (TypeError, ValueError):
            w = h = 0
        out.append({
            "image_url": murl,
            "thumb_url": item.get("thumbURL", murl),
            "source_url": item.get("fromURL", ""),
            "title": item.get("fromPageTitleEnc", ""),
            "width": w, "height": h, "source": "baidu-images",
        })
        if len(out) >= max_results:
            break
    return out


def image_search(query, max_results=8, settings=None):
    query = str(query or "").strip()
    if not query:
        raise SearchError("请输入图片搜索关键词。")
    try:
        return _baidu_images(query, max_results)
    except Exception:
        return _bing_images_fallback(query, max_results)


def _bing_images_fallback(query, max_results):
    url = "https://www.bing.com/images/async?" + urllib.parse.urlencode({
        "q": query, "count": max_results + 10, "mmasync": 1})
    try:
        data = _http_get(url).decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for m in re.finditer(r'\bm="(.*?)"', data, re.S):
        raw = html_mod.unescape(m.group(1)).replace("&quot;", '"')
        try:
            info = json.loads(raw)
        except ValueError:
            continue
        murl = info.get("murl", "")
        if murl.startswith("http"):
            out.append({
                "image_url": murl, "thumb_url": info.get("turl", murl),
                "source_url": info.get("purl", ""), "title": info.get("t", ""),
                "width": 0, "height": 0, "source": "bing-images"})
        if len(out) >= max_results:
            break
    return out
