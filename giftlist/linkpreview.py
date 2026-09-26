"""Fill in an item's name, photo and (sometimes) price from a shop link.

Best effort: any failure returns an empty preview and the user types it in.
The server fetches URLs users paste, so it refuses anything that resolves to
a private or local address (no poking at the VPS's own services).
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

MAX_HTML = 1_500_000
MAX_IMAGE = 5_000_000
TIMEOUT = 6
UA = "Mozilla/5.0 (compatible; FamilyGiftList/1.0; +link preview)"


@dataclass(slots=True, frozen=True)
class Preview:
    title: str | None = None
    image: str | None = None      # saved file name
    price_minor: int | None = None


class Blocked(Exception):
    pass


def is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    addrs = {ipaddress.ip_address(info[4][0]) for info in infos}
    return bool(addrs) and all(a.is_global for a in addrs)


def _fetch(url: str, max_bytes: int, session: requests.Session) -> tuple[str, bytes]:
    for _ in range(4):  # original + up to 3 redirects, each re-checked
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or not is_public_host(parts.hostname):
            raise Blocked(url)
        resp = session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False, headers={"User-Agent": UA})
        if resp.is_redirect:
            url = urljoin(url, resp.headers.get("Location", ""))
            resp.close()
            continue
        resp.raise_for_status()
        body = b""
        for chunk in resp.iter_content(64_000):
            body += chunk
            if len(body) > max_bytes:
                resp.close()
                raise Blocked("too big")
        return resp.headers.get("Content-Type", ""), body
    raise Blocked("too many redirects")


class _Meta(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title = ""
        self.in_title = False
        self.in_ld = False
        self.ld: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "title":
            self.in_title = True
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.in_ld = True
            self.ld.append("")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "script":
            self.in_ld = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif self.in_ld:
            self.ld[-1] += data


def _price_minor(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d{1,2})?", str(text))
    if not m:
        return None
    try:
        value = Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    return int(value * 100) if value > 0 else None


def _ld_price(blobs: list[str]) -> int | None:
    def walk(node):
        if isinstance(node, dict):
            offers = node.get("offers")
            if offers is not None:
                for o in offers if isinstance(offers, list) else [offers]:
                    if isinstance(o, dict):
                        p = _price_minor(o.get("price") or o.get("lowPrice"))
                        if p:
                            return p
            for v in node.values():
                found = walk(v)
                if found:
                    return found
        elif isinstance(node, list):
            for v in node:
                found = walk(v)
                if found:
                    return found
        return None

    for blob in blobs:
        try:
            found = walk(json.loads(blob))
        except (ValueError, RecursionError):
            continue
        if found:
            return found
    return None


def parse_html(html: str, base_url: str) -> tuple[str | None, str | None, int | None]:
    """(title, absolute image url, price in minor units) from a product page."""
    p = _Meta()
    try:
        p.feed(html)
    except Exception:  # malformed pages happen; use whatever we got
        pass
    m = p.meta
    title = m.get("og:title") or m.get("twitter:title") or p.title.strip() or None
    image = m.get("og:image") or m.get("og:image:url") or m.get("twitter:image")
    price = (_price_minor(m.get("product:price:amount")) or _price_minor(m.get("og:price:amount"))
             or _price_minor(m.get("price")) or _ld_price(p.ld))
    if title:
        title = re.sub(r"\s+", " ", title)[:160]
    return title, (urljoin(base_url, image) if image else None), price


def save_image(data: bytes, images_dir: str) -> str:
    """Re-encode as a small JPEG: shrinks it and strips anything odd inside."""
    with Image.open(io.BytesIO(data)) as im:
        im.thumbnail((600, 600))
        rgb = im.convert("RGB")
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=82)
    blob = out.getvalue()
    name = hashlib.sha256(blob).hexdigest()[:24] + ".jpg"
    os.makedirs(images_dir, exist_ok=True)
    path = os.path.join(images_dir, name)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(blob)
    return name


def preview(url: str, images_dir: str, session: requests.Session | None = None) -> Preview:
    session = session or requests.Session()
    try:
        ctype, body = _fetch(url, MAX_HTML, session)
        if "html" not in ctype.lower():
            return Preview()
        title, image_url, price = parse_html(body.decode("utf-8", errors="replace"), url)
    except (Blocked, requests.RequestException, ValueError):
        return Preview()
    image = None
    if image_url:
        try:
            ictype, idata = _fetch(image_url, MAX_IMAGE, session)
            if ictype.lower().startswith("image/"):
                image = save_image(idata, images_dir)
        except (Blocked, requests.RequestException, OSError, ValueError, Image.DecompressionBombError):
            image = None
    return Preview(title, image, price)


IMAGE_NAME = re.compile(r"^[0-9a-f]{24}\.jpg$")


def valid_image_name(name: str | None) -> str | None:
    return name if name and IMAGE_NAME.match(name) else None
