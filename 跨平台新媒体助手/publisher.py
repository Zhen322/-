# -*- coding: utf-8 -*-
"""Platform publishing adapters + the six-state publish machine.

Real API code paths are implemented for X (OAuth 1.0a) and the WeChat Official
Account (draft + freepublish). Zhihu and Xiaohongshu have no public unattended
publishing API for this account, so they report "接入受阻" with the exact
conditions required. A blocked platform never produces a fake "published"
result. Manual publishing is an explicit, separately-labelled fallback that
does not count toward auto-publish acceptance.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client

from platforms import (
    WECHAT, ZHIHU, RED, X, READY, UNVERIFIED, BLOCKED,
    STATUS_PENDING, STATUS_EXECUTING, STATUS_PROCESSING,
    STATUS_PUBLISHED, STATUS_FAILED, STATUS_UNKNOWN,
)
from store import now, uid


class PublishError(ValueError):
    def __init__(self, message, kind="failed", recoverable=True,
                 blocked=False, retry_after=None):
        super().__init__(message)
        self.kind = kind
        self.recoverable = recoverable
        self.blocked = blocked
        self.retry_after = retry_after


# ----------------------------------------------------------------------
# Capability / readiness per platform
# ----------------------------------------------------------------------

def _creds(settings, platform):
    return (settings.get("platform_creds") or {}).get(platform) or {}


def capability(settings, platform):
    """Return {status, reason, conditions, has_credentials}."""
    creds = _creds(settings, platform)
    if platform == X:
        required = ["api_key", "api_secret", "access_token",
                    "access_token_secret"]
        if all(creds.get(k) for k in required):
            return {"status": READY, "reason": "已填写 X OAuth 1.0a 凭据，可调用发帖接口。",
                    "conditions": [], "has_credentials": True}
        return {
            "status": BLOCKED,
            "reason": "X 发帖接口需要 OAuth 1.0a 应用凭据（Developer App）。",
            "conditions": [
                "在 X Developer Portal 创建应用，获取 API Key / API Secret。",
                "生成 OAuth 1.0a Access Token / Secret（用户授权）。",
                "在设置中填入四项凭据后重新核验。",
            ],
            "has_credentials": False,
        }
    if platform == WECHAT:
        if creds.get("appid") and creds.get("secret"):
            return {"status": READY, "reason": "已填写公众号 AppID/AppSecret，可走草稿/发布接口。",
                    "conditions": [], "has_credentials": True}
        return {
            "status": BLOCKED,
            "reason": "公众号需要已认证的服务号（或具备相应接口权限的账号）。",
            "conditions": [
                "在公众平台获取 AppID / AppSecret，并将本机出口 IP 加入白名单。",
                "确认账号具备 draft/add、freepublish/submit 接口权限。",
                "明确“发布文章”与“向粉丝群发”是两套权限，分别验收。",
            ],
            "has_credentials": False,
        }
    if platform == ZHIHU:
        return {
            "status": BLOCKED,
            "reason": "知乎官方未向本账号开放可无人接续发布文章/回答的公开接口；ZhihuPublisher 为内部工具，凭证与权限需单独核验。",
            "conditions": [
                "通过知乎官方渠道确认文章/回答接口的申请资格与授权方式。",
                "取得正式凭证后再接入；文章与回答能力分别核验。",
            ],
            "has_credentials": False,
        }
    if platform == RED:
        return {
            "status": BLOCKED,
            "reason": "小红书官方分享 JS SDK 对标题、文案自动填充有限制，不具备无人接续发布能力，需重点核验专用授权发布接口。",
            "conditions": [
                "通过小红书开放平台确认适用于本项目的授权发布接口及企业资质。",
                "完成 OAuth 授权与内容类型、图片规格核验后再接入。",
            ],
            "has_credentials": False,
        }
    return {"status": UNVERIFIED, "reason": "未知平台。", "conditions": [],
            "has_credentials": False}


# ----------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------

def _ssl_ctx():
    return ssl.create_default_context()


def _request(method, url, headers=None, data=None, timeout=30,
             allow_redirects=False):
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={**(headers or {})},
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_ctx())
    )
    if not allow_redirects:
        class NR(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a):
                return None
        opener = urllib.request.build_opener(
            NR(), urllib.request.HTTPSHandler(context=_ssl_ctx())
        )
    return opener.open(req, timeout=timeout)


# ----------------------------------------------------------------------
# X adapter (OAuth 1.0a)
# ----------------------------------------------------------------------

def _pct(s):
    return urllib.parse.quote(str(s), safe="")


def _oauth_header(method, url, creds, extra_params=None):
    params = {
        "oauth_consumer_key": creds["api_key"],
        "oauth_nonce": hashlib.sha1(os.urandom(40)).hexdigest(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": creds["access_token"],
        "oauth_version": "1.0",
    }
    all_params = {**params, **(extra_params or {})}
    base = "&".join(
        _pct(k) + "=" + _pct(v)
        for k, v in sorted(all_params.items())
    )
    base_str = method.upper() + "&" + _pct(url) + "&" + _pct(base)
    signing_key = _pct(creds["api_secret"]) + "&" + _pct(
        creds["access_token_secret"])
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base_str.encode(),
                 hashlib.sha1).digest()
    ).decode()
    params["oauth_signature"] = sig
    header = "OAuth " + ", ".join(
        _pct(k) + '="' + _pct(v) + '"' for k, v in sorted(params.items())
    )
    return {"Authorization": header}


def _download_bytes(image_url):
    with _request("GET", image_url, headers={
        "User-Agent": "Mozilla/5.0"
    }, allow_redirects=True) as r:
        return r.read()


def _x_upload_media(creds, image_url):
    """Upload one image, return media_id string (simple upload for <=5MB)."""
    data_bytes = _download_bytes(image_url)
    boundary = "----med" + uid()
    if len(data_bytes) <= 5 * 1024 * 1024:
        url = "https://upload.twitter.com/1.1/media/upload.json"
        body = _multipart(boundary, [
            ("media_data", base64.b64encode(data_bytes).decode()),
        ])
        head = _oauth_header("POST", url, creds)
        head["Content-Type"] = "multipart/form-data; boundary=" + boundary
        with _request("POST", url, head, body.encode()) as r:
            return json.loads(r.read().decode())["media_id_string"]
    # Chunked upload for larger files (append then finalize).
    init = "https://upload.twitter.com/1.1/media/upload.json?command=INIT&total_bytes=%d&media_type=image/png" % len(data_bytes)
    head = _oauth_header("POST", init.split("?")[0], creds,
                         {"command": "INIT", "total_bytes": str(len(data_bytes)),
                          "media_type": "image/png"})
    with _request("POST", init, head) as r:
        media_id = json.loads(r.read().decode())["media_id_string"]
    seg = 0
    for i in range(0, len(data_bytes), 4 * 1024 * 1024):
        chunk = data_bytes[i:i + 4 * 1024 * 1024]
        body = _multipart(boundary, [
            ("command", "APPEND"), ("media_id", media_id),
            ("segment_index", str(seg)),
            ("media", ("chunk", chunk, "application/octet-stream")),
        ])
        app = "https://upload.twitter.com/1.1/media/upload.json"
        h = _oauth_header("POST", app, creds, {
            "command": "APPEND", "media_id": media_id,
            "segment_index": str(seg)})
        h["Content-Type"] = "multipart/form-data; boundary=" + boundary
        _request("POST", app, h, body.encode())
        seg += 1
    fin = "https://upload.twitter.com/1.1/media/upload.json?command=FINALIZE&media_id=" + media_id
    h = _oauth_header("POST", fin.split("?")[0], creds,
                      {"command": "FINALIZE", "media_id": media_id})
    _request("POST", fin, h)
    return media_id


def _multipart(boundary, fields):
    lines = []
    for name, value in fields:
        if isinstance(value, tuple):
            fname, blob, ctype = value
            lines.append("--" + boundary)
            lines.append('Content-Disposition: form-data; name="%s"; filename="%s"'
                         % (name, fname))
            lines.append("Content-Type: " + ctype)
            lines.append("")
            lines.append(None)
            # binary placeholder handled below
            lines[-1] = ("BINARY", blob)
        else:
            lines.append("--" + boundary)
            lines.append('Content-Disposition: form-data; name="%s"' % name)
            lines.append("")
            lines.append(value)
    out = []
    for ln in lines:
        if isinstance(ln, tuple) and ln and ln[0] == "BINARY":
            out.append(ln[1])
        else:
            out.append(ln.encode("utf-8"))
    out.append(("--" + boundary + "--").encode())
    return b"\r\n".join(out)


def _x_post_tweet(creds, text, media_ids=None, reply_to=None):
    url = "https://api.twitter.com/2/tweets"
    payload = {"text": text}
    if media_ids:
        payload["media"] = {"media_ids": media_ids}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to}
    head = _oauth_header("POST", url, creds)
    head["Content-Type"] = "application/json"
    with _request("POST", url, head,
                  json.dumps(payload).encode("utf-8")) as r:
        return json.loads(r.read().decode())


def publish_x(content, settings):
    """Publish a single post or a thread for one language. Returns result dict."""
    creds = _creds(settings, X)
    if not all(creds.get(k) for k in ("api_key", "api_secret",
                                       "access_token", "access_token_secret")):
        raise PublishError(capability(settings, X)["reason"], blocked=True,
                           recoverable=False)
    posts = content.get("posts") or [{"text": content.get("body", ""),
                                      "images": content.get("image_urls", [])}]
    published, last_id = [], None
    for post in posts:
        media_ids = []
        for im in post.get("images", []):
            media_ids.append(_x_upload_media(creds, im))
        res = _x_post_tweet(creds, post["text"], media_ids or None,
                            last_id if len(published) else None)
        data = res.get("data", {})
        last_id = data.get("id")
        published.append({"id": last_id, "text": post["text"]})
    return {
        "platform": X, "language": content.get("language"),
        "posts": published,
        "link": "https://twitter.com/i/web/status/" + (published[0]["id"] if published else ""),
        "published_at": now(),
    }


# ----------------------------------------------------------------------
# WeChat adapter
# ----------------------------------------------------------------------

def _wechat_token(creds):
    url = ("https://api.weixin.qq.com/cgi-bin/token?grant_type="
           "client_credential&appid=%s&secret=%s"
           % (creds["appid"], creds["secret"]))
    with _request("GET", url) as r:
        body = json.loads(r.read().decode())
    if body.get("access_token"):
        return body["access_token"]
    raise PublishError("获取公众号 access_token 失败（%s）。请核对 AppID/AppSecret 与 IP 白名单。"
                       % body.get("errmsg", "unknown"), recoverable=True)


def _wechat_post_json(base, token, payload, extra=""):
    url = base + "?access_token=" + token + extra
    with _request("POST", url, {"Content-Type": "application/json"},
                  json.dumps(payload, ensure_ascii=False).encode("utf-8")) as r:
        return json.loads(r.read().decode())


def publish_wechat(content, settings):
    creds = _creds(settings, WECHAT)
    if not creds.get("appid") or not creds.get("secret"):
        raise PublishError(capability(settings, WECHAT)["reason"], blocked=True,
                           recoverable=False)
    token = _wechat_token(creds)
    # Upload inline images to obtain mp URLs (media/uploadimg).
    body_html = _markdown_to_simple_html(content.get("body", ""))
    # Real image upload: replace local file paths with returned URLs.
    uploaded = []
    for im in content.get("image_urls", [])[:8]:
        if im.startswith("http"):
            raw = _download_bytes(im)
        else:
            raw = Path(im).read_bytes()
        boundary = "----wx" + uid()
        ext = ".png"
        mp = _multipart(boundary, [
            ("media", ("image%s" % ext, raw, "image/png"))])
        up = ("https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token="
              + token)
        with _request("POST", up,
                      {"Content-Type": "multipart/form-data; boundary=" + boundary},
                      mp) as r:
            res = json.loads(r.read().decode())
        if res.get("url"):
            uploaded.append(res["url"])
    # Build an article; prepend uploaded images into the HTML.
    img_html = "".join('<p><img src="%s"/></p>' % u for u in uploaded)
    article = {
        "title": content.get("title", ""),
        "author": content.get("author", ""),
        "digest": content.get("summary", ""),
        "content": img_html + body_html,
        "content_source_url": "",
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }
    added = _wechat_post_json(
        "https://api.weixin.qq.com/cgi-bin/draft/add", token,
        {"articles": [article]})
    media_id = added.get("media_id")
    if not media_id:
        raise PublishError("公众号草稿创建失败：%s" % added.get("errmsg"))
    sub = _wechat_post_json(
        "https://api.weixin.qq.com/cgi-bin/freepublish/submit", token,
        {"media_id": media_id})
    if sub.get("errcode") not in (0, None):
        raise PublishError("公众号发布提交失败：%s" % sub.get("errmsg"))
    publish_id = sub.get("publish_id")
    return {
        "platform": WECHAT, "language": "zh",
        "draft_media_id": media_id, "publish_id": publish_id,
        "published_at": now(), "processing": True,
    }


def query_wechat(content, settings, publish_id):
    creds = _creds(settings, WECHAT)
    token = _wechat_token(creds)
    res = _wechat_post_json(
        "https://api.weixin.qq.com/cgi-bin/freepublish/get", token,
        {"publish_id": publish_id})
    status = res.get("publish_status")
    # publish_status: 0 success, 1 publishing, 2 failed, 3 success(?)
    article_url = ""
    item = res.get("article_detail", {})
    if item:
        article_url = item.get("article_url", "")
    return {"status": status, "article_url": article_url, "raw": res}


def _markdown_to_simple_html(md):
    """Very small markdown -> wechat-compatible HTML (headings, paragraphs,
    bold, lists). Avoids shipping a full markdown parser."""
    html_lines = []
    for line in md.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            html_lines.append("<h%d>%s</h%d>" % (level, _inline(m.group(2)),
                                                 level))
            continue
        if re.match(r"^[-*]\s+", line):
            html_lines.append("<p>• %s</p>" % _inline(line[2:]))
            continue
        html_lines.append("<p>%s</p>" % _inline(line))
    return "".join(html_lines)


def _inline(text):
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    return text


# ----------------------------------------------------------------------
# Unified dispatch
# ----------------------------------------------------------------------

ADAPTERS = {
    X: publish_x,
    WECHAT: publish_wechat,
}


def run_publish(content, settings):
    """Publish one target's content. Returns platform result or raises
    PublishError. Blocked platforms raise a blocked error (no fake success)."""
    platform = content.get("platform")
    cap = capability(settings, platform)
    if cap["status"] == BLOCKED:
        raise PublishError(cap["reason"], blocked=True, recoverable=False)
    adapter = ADAPTERS.get(platform)
    if not adapter:
        raise PublishError(cap["reason"], blocked=True, recoverable=False)
    return adapter(content, settings)


def classify_http(exc):
    """Map an HTTP failure to a PublishError with the right exception class."""
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code in (401, 403):
            return PublishError("授权已失效，请重新授权后核查原任务。",
                                kind="auth", recoverable=False)
        if code == 429:
            return PublishError("触发平台限流，记录可重试条件后稍后再试。",
                                kind="rate_limit", retry_after=60)
        return PublishError("平台返回 HTTP %s。" % code, kind="failed")
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return PublishError("网络超时，先核查原任务结果，无法确认时保留“结果未知”。",
                            kind="unknown")
    return PublishError("发布失败：%s" % str(exc), kind="failed")
