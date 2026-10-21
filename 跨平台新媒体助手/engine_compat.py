# -*- coding: utf-8 -*-
"""Minimal public-URL article fetcher (stdlib only). Untrusted pages are data:
the parser only extracts title/body, never executes scripts or subresources.
"""
from __future__ import annotations

import gzip
import io
import re
import ssl
import urllib.error
import urllib.request
from html import unescape
from html.parser import HTMLParser


class FetchError(ValueError):
    pass


class _Parser(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "aside",
            "form", "iframe", "svg", "canvas", "button"}
    BLOCK = {"p", "div", "section", "article", "main", "h1", "h2", "h3",
             "h4", "li", "br", "tr", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.candidates = []
        self.title = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        ident = attrs.get("id", "").lower()
        classes = attrs.get("class", "").lower()
        skipped = tag in self.SKIP or "hidden" in attrs
        priority = 0
        if ident == "js_content" or "rich_media_content" in classes:
            priority = 100
        elif tag == "article":
            priority = 80
        elif re.search(r"(article|post|news|content)[_-]?(body|content|detail)",
                       ident + " " + classes):
            priority = 70
        elif tag == "main" or attrs.get("role") == "main":
            priority = 50
        cand = [priority, []] if priority else None
        if cand:
            self.candidates.append(cand)
        if tag in self.BLOCK:
            self._append("\n")
        if tag not in {"area", "base", "br", "col", "hr", "img", "input",
                       "link", "meta", "source", "track", "wbr"}:
            self.stack.append((tag, skipped, cand))

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self._append("\n")
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def _append(self, text):
        if any(s[1] for s in self.stack):
            return
        self.parts.append(text)
        for _, _, cand in self.stack:
            if cand is not None:
                cand[1].append(text)

    def handle_data(self, data):
        if any(s[0] == "title" for s in self.stack):
            self.title.append(data)
            return
        self._append(data)

    def result(self):
        cands = [(p, _clean("".join(x))) for p, x in self.candidates]
        cands = [(p, c) for p, c in cands if len(c) >= 120]
        body = max(cands, key=lambda i: (i[0], len(i[1])))[1] \
            if cands else _clean("".join(self.parts))
        return _clean("".join(self.title)), body


def _clean(text):
    text = unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", l).strip()
             for l in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def fetch_article(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"),
        "Accept": "text/html,*/*;q=0.8",
    })
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req, timeout=20) as resp:
            data = resp.read(5 * 1024 * 1024)
    except (urllib.error.URLError, OSError) as exc:
        raise FetchError("抓取失败，请检查链接与网络，或直接粘贴正文。") from exc
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            html = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        html = data.decode("utf-8", "replace")
    parser = _Parser()
    parser.feed(html)
    parser.close()
    title, body = parser.result()
    if len(body) < 160:
        raise FetchError("未取得可用正文（可能需要登录/验证），请粘贴正文。")
    return {"title": title[:300] or body[:40], "content": body,
            "author": ""}
