"""极简 HTTP 客户端：统一 UA、超时、重试与代理。

只依赖标准库，避免在树莓派上引入额外依赖。
"""

from __future__ import annotations

import gzip
import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Dict, Optional

from ..version import USER_AGENT


class HttpError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "close",
}


class HttpClient:
    def __init__(
        self,
        timeout: float = 8.0,
        retries: int = 1,
        proxy: str = "",
        user_agent: str = "",
    ) -> None:
        self.timeout = max(2.0, float(timeout or 8))
        self.retries = max(0, int(retries or 0))
        self.proxy = (proxy or "").strip()
        self.user_agent = user_agent or USER_AGENT
        handlers = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        self._opener = urllib.request.build_opener(*handlers)

    # ------------------------------------------------------------------
    def _request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> bytes:
        merged = dict(DEFAULT_HEADERS)
        merged["User-Agent"] = self.user_agent
        if headers:
            merged.update({k: v for k, v in headers.items() if v})
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=merged)
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read(max_bytes + 1)
                    if len(raw) > max_bytes:
                        raise HttpError("响应体过大", resp.status, url)
                    encoding = (resp.headers.get("Content-Encoding") or "").lower()
                    if encoding == "gzip":
                        raw = gzip.decompress(raw)
                    elif encoding == "deflate":
                        raw = zlib.decompress(raw)
                    return raw
            except urllib.error.HTTPError as exc:
                last_error = exc
                # 4xx 不重试（429 除外）
                if exc.code == 429 or exc.code >= 500:
                    time.sleep(0.4 + attempt * 0.4)
                    continue
                raise HttpError(f"HTTP {exc.code}", exc.code, url) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.3 + attempt * 0.3)
                    continue
                raise HttpError(f"网络错误：{exc}", None, url) from exc
        raise HttpError(f"请求失败：{last_error}", None, url)

    # ------------------------------------------------------------------
    def get_bytes(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        max_bytes: int = 12 * 1024 * 1024,
    ) -> bytes:
        return self._request(url, headers=headers, max_bytes=max_bytes)

    def post(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: bytes = b"",
        max_bytes: int = 2 * 1024 * 1024,
    ) -> bytes:
        """发送一个简单的 POST（用于 Jellyfin 等 Web API）。"""
        return self._request(url, headers=headers, data=data, max_bytes=max_bytes)

    def get_json(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(params)
        raw = self._request(url, headers=headers)
        text = decode_bytes(raw)
        text = text.strip()
        if text.startswith("callback("):
            text = text[text.find("(") + 1 : text.rfind(")")]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            snippet = text[:120].replace("\n", " ")
            raise HttpError(f"返回内容不是 JSON：{snippet}", None, url) from exc

    def get_text(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        return decode_bytes(self._request(url, headers=headers))


def decode_bytes(raw: bytes, fallback: str = "utf-8") -> str:
    for enc in (fallback, "utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


def url_quote(text: str) -> str:
    return urllib.parse.quote(text or "", safe="")


def jitter_sleep(base: float = 0.2) -> None:
    time.sleep(base + random.random() * base)
