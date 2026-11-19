"""公共 HTTP 客户端：SSRF 防护、限额、重试与安全日志（C2-01）。

约束：
* 只允许 http/https；默认拒绝私网、回环、链路本地、保留地址；
* 重定向手工处理，每一跳重新做地址校验，禁止 https→http 降级；
* 强制超时、响应大小上限、每域名最小请求间隔；
* 429/5xx 有限重试并尊重 Retry-After，401/403 立即停止；
* 日志与错误信息不含认证头、查询中的敏感参数或响应正文。
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit


class FetchError(Exception):
    """带机器可读错误码的采集异常。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    content: bytes
    content_type: str
    elapsed_ms: int
    sha256: str
    redirects: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        charset = "utf-8"
        for part in self.content_type.split(";")[1:]:
            if "charset=" in part.lower():
                charset = part.split("=", 1)[1].strip() or "utf-8"
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")

    def json(self) -> Any:
        import json

        return json.loads(self.text)


BLOCKED_NETS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_blocked(ip: ipaddress._BaseAddress) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    return any(ip in net for net in BLOCKED_NETS)


def validate_url(url: str, *, allow_private: bool = False) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise FetchError("url_scheme", "只允许 http/https 地址")
    if not parts.hostname:
        raise FetchError("url_host", "地址缺少主机名")
    if parts.username or parts.password:
        raise FetchError("url_credentials", "地址中不允许内嵌账号密码")
    if not allow_private:
        try:
            infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise FetchError("dns_failed", f"域名解析失败: {exc}") from exc
        addresses = {info[4][0] for info in infos}
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise FetchError("dns_bad_address", "域名解析到非法地址") from exc
            if _is_blocked(ip):
                raise FetchError("blocked_address", "目标解析到私网/回环/保留地址，已拒绝")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


class HttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        max_bytes: int = 4 * 1024 * 1024,
        retry_count: int = 2,
        host_min_interval_seconds: float = 1.0,
        user_agent: str = "MindOS-Radar/0.1",
        allow_private: bool = False,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.retry_count = retry_count
        self.host_min_interval_seconds = host_min_interval_seconds
        self.user_agent = user_agent
        self.allow_private = allow_private
        self._last_hit: dict[str, float] = {}
        self._ssl = ssl.create_default_context()

    # -- public ---------------------------------------------------------
    def get(self, url: str, *, headers: dict[str, str] | None = None) -> HttpResponse:
        return self.request("GET", url, headers=headers)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        max_redirects: int = 5,
    ) -> HttpResponse:
        current = validate_url(url, allow_private=self.allow_private)
        redirects: list[str] = []
        attempts = self.retry_count + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._request_once(method, current, headers, body, redirects, max_redirects)
                return response
            except FetchError as exc:
                if exc.code in {"http_401", "http_403", "http_404", "blocked_address", "url_scheme", "url_host"}:
                    raise
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = FetchError("network", f"{type(exc).__name__}")
            if attempt + 1 < attempts:
                time.sleep(min(1 + attempt, 5))
        raise FetchError(getattr(last_error, "code", "network"), str(last_error))

    # -- internal -------------------------------------------------------
    def _throttle(self, host: str) -> None:
        now = time.monotonic()
        last = self._last_hit.get(host, 0.0)
        wait = self.host_min_interval_seconds - (now - last)
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _request_once(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        body: bytes | None,
        redirects: list[str],
        max_redirects: int,
    ) -> HttpResponse:
        current = url
        for hop in range(max_redirects + 1):
            host = urlsplit(current).hostname or ""
            self._throttle(host)
            merged = {"User-Agent": self.user_agent, "Accept": "*/*"}
            merged.update(headers or {})
            request = urllib.request.Request(current, data=body, headers=merged, method=method)
            started = time.monotonic()
            try:
                response = urllib.request.urlopen(request, timeout=self.timeout_seconds, context=self._ssl)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504):
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    delay = 0
                    if retry_after:
                        try:
                            delay = min(int(retry_after), 30)
                        except ValueError:
                            try:
                                delay = min(int((parsedate_to_datetime(retry_after).timestamp() - time.time())), 30)
                            except Exception:  # noqa: BLE001
                                delay = 0
                    if delay > 0:
                        time.sleep(delay)
                    raise FetchError(f"http_{exc.code}", f"HTTP {exc.code}")
                raise FetchError(f"http_{exc.code}", f"HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, ssl.SSLError):
                    raise FetchError("tls", "TLS 校验失败") from exc
                raise

            with response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                if 300 <= status < 400:
                    location = response.headers.get("Location")
                    if not location:
                        raise FetchError("redirect_no_location", "重定向缺少 Location")
                    if hop >= max_redirects:
                        raise FetchError("redirect_limit", "重定向次数超限")
                    target = urljoin(current, location)
                    if urlsplit(current).scheme == "https" and urlsplit(target).scheme == "http":
                        raise FetchError("redirect_downgrade", "拒绝 https→http 降级重定向")
                    redirects.append(target)
                    current = validate_url(target, allow_private=self.allow_private)
                    if status in (302, 303):
                        method, body = "GET", None
                    continue
                if status in (401, 403):
                    raise FetchError(f"http_{status}", f"HTTP {status}（已停止，不尝试绕过）")
                if status >= 400:
                    raise FetchError(f"http_{status}", f"HTTP {status}")

                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise FetchError("response_too_large", f"响应超过 {self.max_bytes} 字节上限")
                    chunks.append(chunk)
                content = b"".join(chunks)
                return HttpResponse(
                    url=current,
                    status=status,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    content=content,
                    content_type=content_type,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    sha256=hashlib.sha256(content).hexdigest(),
                    redirects=redirects,
                )
        raise FetchError("redirect_limit", "重定向次数超限")


def safe_url(url: str) -> str:
    """去掉查询串，避免把敏感参数写进日志。"""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def safe_error(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    return text[:300]


def headers_from_source(source: dict[str, Any], secret_getter: Any = None) -> dict[str, str]:
    """按来源配置生成请求头；只读取 secret_ref 名称，绝不落库。"""
    access = source.get("access") or {}
    auth = access.get("auth")
    headers: dict[str, str] = {}
    secret_ref = access.get("secret_ref")
    if auth in {"api_key", "token"} and secret_ref and secret_getter:
        value = secret_getter(secret_ref)
        if value:
            header_name = access.get("header_name") or ("Authorization" if auth == "token" else "X-API-Key")
            prefix = access.get("header_prefix") or ("Bearer " if auth == "token" else "")
            headers[header_name] = f"{prefix}{value}"
    return headers
