"""Web search and safely bounded fetch tools."""

import ipaddress
import socket
import threading
import time
from typing import Any
from urllib.request import getproxies_environment, proxy_bypass_environment

from ene.utils.interrupt import CancelWatcher

from .constants import (
    MAX_WEB_FETCH_BYTES,
    MAX_WEB_REDIRECTS,
    IPV6_TRANSITION_NETWORKS,
)
from .formatting import truncate_text_output


WEB_FETCH_TIMEOUT_SECONDS = 30.0


def _proxy_for_url(scheme: str, host: str, port: int | None) -> str | None:
    """Return the shell-configured proxy for a URL, respecting NO_PROXY."""
    proxies = getproxies_environment()
    bypass_host = f"[{host}]" if ":" in host else host
    if port is not None:
        bypass_host = f"{bypass_host}:{port}"
    if proxy_bypass_environment(bypass_host, proxies):
        return None
    return proxies.get(scheme) or proxies.get("all") or proxies.get("socks")


def _validate_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> None:
    if (
        not address.is_global
        or address.is_multicast
        or (
            isinstance(address, ipaddress.IPv6Address)
            and any(address in network for network in IPV6_TRANSITION_NETWORKS)
        )
    ):
        raise ValueError(f"destination resolves to non-public address {address}")


def _validate_proxy_target(host: str) -> None:
    """Reject literal private targets without resolving proxy-routed hostnames."""
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            normalized_host = host.rstrip(".").lower()
            if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
                raise ValueError(
                    "destination resolves to non-public address localhost"
                )
            return
    _validate_public_address(address)


def _resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    """Resolve *host* and reject any result that is not globally routable."""
    addresses: list[str] = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    ):
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        address = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        _validate_public_address(address)
        value = str(address)
        if value not in addresses:
            addresses.append(value)
    if not addresses:
        raise ValueError("destination has no public IP address")
    return tuple(addresses)


class WebToolsMixin:
    def _web_search(self, query: str) -> dict[str, Any]:
        """Web search using DuckDuckGo."""
        try:
            from ddgs import DDGS
        except ImportError:
            return {"error": "web_search requires ddgs: pip install ddgs", "success": False}

        try:
            results = DDGS().text(query, max_results=5)
            if not results:
                return {"content": "No results found.", "success": True}

            formatted_results = []
            for res in results:
                title = res.get("title", "No title")
                href = res.get("href", "No URL")
                body = res.get("body", "No description")
                formatted_results.append(f"Title: {title}\nURL: {href}\nSnippet: {body}\n")

            return {"content": "\n".join(formatted_results), "success": True}
        except Exception as e:
            return {"error": f"Search failed: {e}", "success": False}

    def _web_fetch(self, url: str) -> dict[str, Any]:
        """Fetch public HTTP(S) content with an interruptible overall deadline."""
        stopped = threading.Event()
        done = threading.Event()
        outcome: dict[str, Any] = {}

        def fetch() -> None:
            try:
                outcome["result"] = self._web_fetch_blocking(url, stopped)
            except BaseException as exc:
                outcome["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(target=fetch, name="ene-web-fetch", daemon=True)
        worker.start()
        deadline = time.monotonic() + WEB_FETCH_TIMEOUT_SECONDS

        try:
            with CancelWatcher(self.cancellation) as watcher:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        stopped.set()
                        return {
                            "error": (
                                "Failed to fetch URL: timed out after "
                                f"{WEB_FETCH_TIMEOUT_SECONDS:g} seconds"
                            ),
                            "success": False,
                        }
                    if done.wait(min(0.05, remaining)):
                        break
                    if watcher.is_cancelled:
                        stopped.set()
                        return {
                            "error": "Web fetch interrupted by user.",
                            "success": False,
                            "interrupted": True,
                        }
        except KeyboardInterrupt:
            stopped.set()
            return {
                "error": "Web fetch interrupted by user.",
                "success": False,
                "interrupted": True,
            }

        if "error" in outcome:
            raise outcome["error"]
        return outcome["result"]

    def _web_fetch_blocking(
        self, url: str, stopped: threading.Event
    ) -> dict[str, Any]:
        """Perform the bounded fetch; the caller enforces its overall deadline."""
        try:
            import httpcore
            import httpx
            from bs4 import BeautifulSoup
        except ImportError:
            return {"error": "web_fetch requires httpx and beautifulsoup4: pip install httpx beautifulsoup4", "success": False}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        current = httpx.URL(url)
        final_url = current
        body = b""
        try:
            for redirect_count in range(MAX_WEB_REDIRECTS + 1):
                if stopped.is_set():
                    raise TimeoutError("fetch stopped")
                if current.scheme not in ("http", "https") or not current.host:
                    raise ValueError("only absolute HTTP(S) URLs are allowed")
                if current.userinfo:
                    raise ValueError("URL credentials are not allowed")

                port = current.port or (443 if current.scheme == "https" else 80)
                proxy = _proxy_for_url(current.scheme, current.host, current.port)
                if proxy:
                    # Proxy-aware DNS may intentionally return synthetic addresses
                    # (for example Clash fake-IP results). Let the proxy resolve
                    # hostnames, but still reject literal private destinations.
                    _validate_proxy_target(current.host)
                    addresses: tuple[str, ...] = ()
                else:
                    addresses = _resolve_public_addresses(current.host, port)

                class PinnedBackend(httpcore.SyncBackend):
                    def connect_tcp(self, host, port, **kwargs):
                        last_error = None
                        for address in addresses:
                            try:
                                return super().connect_tcp(address, port, **kwargs)
                            except Exception as exc:
                                last_error = exc
                        assert last_error is not None
                        raise last_error

                ssl_context = httpx.create_ssl_context(trust_env=False)
                if proxy:
                    transport = httpx.HTTPTransport(
                        verify=ssl_context,
                        proxy=proxy,
                        trust_env=False,
                    )
                else:
                    pool = httpcore.ConnectionPool(
                        ssl_context=ssl_context,
                        network_backend=PinnedBackend(),
                    )
                    transport = httpx.HTTPTransport()
                    transport._pool = pool
                try:
                    with httpx.Client(transport=transport, trust_env=False) as client:
                        with client.stream(
                            "GET", current, headers=headers, timeout=30.0
                        ) as response:
                            if response.status_code in (301, 302, 303, 307, 308):
                                location = response.headers.get("location")
                                if not location:
                                    raise ValueError("redirect response has no Location header")
                                if redirect_count == MAX_WEB_REDIRECTS:
                                    raise ValueError("too many redirects")
                                current = current.join(location)
                                continue
                            response.raise_for_status()
                            chunks = []
                            size = 0
                            for chunk in response.iter_bytes():
                                if stopped.is_set():
                                    raise TimeoutError("fetch stopped")
                                size += len(chunk)
                                if size > MAX_WEB_FETCH_BYTES:
                                    raise ValueError(
                                        f"response exceeds {MAX_WEB_FETCH_BYTES} bytes"
                                    )
                                chunks.append(chunk)
                            body = b"".join(chunks)
                            final_url = current
                            encoding = response.encoding or "utf-8"
                            break
                finally:
                    transport.close()
            else:  # pragma: no cover - loop always breaks or raises
                raise ValueError("too many redirects")
        except Exception as e:
            return {"error": f"Failed to fetch URL: {e}", "success": False}

        text_body = body.decode(encoding, errors="replace")
        soup = BeautifulSoup(text_body, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()

        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = " ".join(chunk for chunk in chunks if chunk)

        title = soup.find("title")
        if title:
            text = f"# {title.get_text().strip()}\n\n{text}"

        content, truncated = truncate_text_output(
            text,
            "Fetch a more specific source or URL.",
        )
        result = {
            "content": content,
            "url": str(final_url),
            "truncated": truncated,
            "success": True,
        }
        if truncated:
            result["truncation_reason"] = "character cap"
        return result
