# -*- coding: utf-8 -*-
"""LLM client. Default provider is DeepSeek (OpenAI-compatible Chat Completions).

Only the explicitly configured endpoint ever receives credentials. The client
is stdlib-only, validates the JSON response, retries transient failures with
backoff, and records token usage. Keys are never logged or returned in errors.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client


class LLMError(ValueError):
    """Safe-to-display error; never contains keys or the raw request."""


# DeepSeek is the documented default. The endpoint also accepts any
# OpenAI-compatible chat/completions service by overriding base_url.
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def _ssl_context():
    return ssl.create_default_context()


def _read_json(url, payload, headers, timeout):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    # Never follow a credential-bearing request to a different endpoint.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=_ssl_context()),
    )
    with opener.open(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(settings):
    base = str(settings.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
    model = str(settings.get("model") or DEFAULT_MODEL).strip()
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LLMError("请填写有效的模型服务地址（http/https）。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LLMError("模型地址不能包含账号密码或查询参数。")
    if base.endswith("/chat/completions"):
        return base, model
    if not parsed.path or parsed.path == "/":
        base += "/v1"
    return base + "/chat/completions", model


def _extract(body):
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise LLMError("模型没有返回候选回答，请检查模型是否支持 Chat Completions。")
    message = choices[0].get("message") or {}
    text = message.get("content")
    finish = choices[0].get("finish_reason")
    usage = body.get("usage") or {}
    if finish in ("length", "max_tokens"):
        raise LLMError("模型回答达到输出上限，未返回完整结果。请减少目标字数或拆分任务。")
    if not isinstance(text, str) or not text.strip():
        raise LLMError("模型返回空内容，可能拒绝请求或与接口不兼容。")
    return text.strip(), finish, usage


def parse_json(text):
    """Parse fenced JSON or surrounding prose without eval/silent repair."""
    text = re.sub(r"^\s*<think>.*?</think>\s*", "", text, flags=re.S)
    candidates = [text]
    candidates.extend(re.findall(r"```(?:json)?\s*(.*?)```", text, re.I | re.S))
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate.strip())
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
        for start in [m.start() for m in re.finditer(r"\{", candidate)][:60]:
            try:
                value, _ = decoder.raw_decode(candidate[start:])
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
    raise LLMError("模型没有返回完整有效的 JSON。请重试或更换模型。")


def chat(settings, system, user, max_tokens=2000, temperature=0.35,
         json_mode=True, retries=3, timeout=180):
    """Single-turn chat. Returns (text, usage, model).

    `system`/`user` carry the full task. The model is instructed to treat all
    embedded source data as untrusted data, never as instructions.
    """
    endpoint, model = _endpoint(settings)
    key = str(settings.get("api_key") or "").strip()
    host = urllib.parse.urlsplit(endpoint).hostname
    if not key and host not in ("localhost", "127.0.0.1", "::1"):
        raise LLMError("请先在设置中填写 DeepSeek API Key。")
    headers = {"Authorization": "Bearer " + key} if key else {}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_err = None
    for attempt in range(retries):
        try:
            body = _read_json(endpoint, payload, headers, timeout)
            if body.get("error"):
                err = body["error"]
                code = err.get("code") if isinstance(err, dict) else ""
                msg = err.get("message") if isinstance(err, dict) else str(err)
                # Auth / invalid-request errors are not worth retrying.
                if code in ("invalid_api_key", "authentication_error",
                            "invalid_request_error", "model_not_found"):
                    raise LLMError(f"模型请求失败：{msg}")
                last_err = LLMError(f"模型执行失败：{msg}")
            else:
                text, _finish, usage = _extract(body)
                if json_mode:
                    parse_json(text)
                return text, usage, model
        except urllib.error.HTTPError as exc:
            status = exc
            code = exc.code
            try:
                detail = exc.read(4096).decode("utf-8", "replace")
            except Exception:
                detail = ""
            exc.close()
            if code in (401, 403):
                raise LLMError("模型鉴权失败，请检查 API Key。")
            if code == 404:
                raise LLMError("模型接口或模型不存在，请检查模型名称。")
            if code == 400:
                raise LLMError("模型请求参数错误（可能是输入超长或不支持 JSON 模式）。")
            if code in (429, 503, 500, 502, 504):
                # Rate limit / temporary server issue: backoff and retry.
                wait = min(30, 2 ** attempt * 2)
                last_err = LLMError(f"模型暂时不可用（HTTP {code}），{wait}s 后重试。")
                time.sleep(wait)
                continue
            raise LLMError(f"模型返回 HTTP {code}。")
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException) as exc:
            wait = min(20, 2 ** attempt * 2)
            last_err = LLMError(f"模型连接失败或超时，{wait}s 后重试。")
            time.sleep(wait)
    raise last_err or LLMError("模型多次调用失败，请稍后重试。")


def chat_tools(settings, messages, tools, max_tokens=1200, temperature=0.2,
               retries=3, timeout=180):
    """Tool-calling (function-calling) chat. Returns (message, usage, model).

    The returned message dict may carry ``tool_calls``; it is the same shape
    the API returns in choices[0].message and can be appended straight back to
    ``messages`` for the next agentic step.
    """
    endpoint, model = _endpoint(settings)
    key = str(settings.get("api_key") or "").strip()
    host = urllib.parse.urlsplit(endpoint).hostname
    if not key and host not in ("localhost", "127.0.0.1", "::1"):
        raise LLMError("请先在设置中填写 DeepSeek API Key。")
    headers = {"Authorization": "Bearer " + key} if key else {}
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "tools": tools,
        "tool_choice": "auto",
    }
    last_err = None
    for attempt in range(retries):
        try:
            body = _read_json(endpoint, payload, headers, timeout)
            if body.get("error"):
                err = body["error"]
                code = err.get("code") if isinstance(err, dict) else ""
                msg = err.get("message") if isinstance(err, dict) else str(err)
                if code in ("invalid_api_key", "authentication_error",
                            "invalid_request_error", "model_not_found"):
                    raise LLMError(f"模型请求失败：{msg}")
                last_err = LLMError(f"模型执行失败：{msg}")
            else:
                choices = body.get("choices") or []
                if not choices or not isinstance(choices[0], dict):
                    raise LLMError("模型没有返回候选回答。")
                message = choices[0].get("message") or {}
                finish = choices[0].get("finish_reason")
                usage = body.get("usage") or {}
                if finish in ("length", "max_tokens"):
                    raise LLMError("模型决策达到输出上限，请简化目标或提高步数。")
                return message, usage, model
        except urllib.error.HTTPError as exc:
            code = exc.code
            try:
                exc.read(4096).decode("utf-8", "replace")
            except Exception:
                pass
            exc.close()
            if code in (401, 403):
                raise LLMError("模型鉴权失败，请检查 API Key。")
            if code == 404:
                raise LLMError("模型接口或模型不存在，请检查模型名称。")
            if code == 400:
                raise LLMError("模型请求参数错误（可能不支持函数调用或输入超长）。")
            if code in (429, 503, 500, 502, 504):
                wait = min(30, 2 ** attempt * 2)
                last_err = LLMError(f"模型暂时不可用（HTTP {code}），{wait}s 后重试。")
                time.sleep(wait)
                continue
            raise LLMError(f"模型返回 HTTP {code}。")
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException):
            wait = min(20, 2 ** attempt * 2)
            last_err = LLMError(f"模型连接失败或超时，{wait}s 后重试。")
            time.sleep(wait)
    raise last_err or LLMError("模型多次调用失败，请稍后重试。")


def test(settings):
    text, usage, model = chat(
        settings,
        "你是连接测试助手，只回答“连接成功”。",
        "请只回答：连接成功",
        max_tokens=20, json_mode=False, retries=1, timeout=30,
    )
    return f"模型 {model} 已连接：{text[:80]}"
