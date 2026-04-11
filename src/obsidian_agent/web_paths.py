from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import urlparse


DEFAULT_INDEX_STEM = "index"


def normalize_vault_path(path: str) -> str:
    raw = path.strip()
    if not raw:
        msg = "path must be non-empty"
        raise ValueError(msg)
    if "\\" in raw:
        msg = "path must use '/' separators"
        raise ValueError(msg)

    normalized = PurePosixPath(raw)
    if normalized.is_absolute():
        msg = "path must be vault-relative"
        raise ValueError(msg)
    if ".." in normalized.parts:
        msg = "path must not traverse parent directories"
        raise ValueError(msg)

    return str(normalized)


def _url_path_component(*, url: str, site_base_url: str) -> str:
    parsed_url = urlparse(url)
    parsed_base = urlparse(site_base_url)

    if parsed_url.scheme and parsed_url.netloc:
        if (parsed_url.scheme, parsed_url.netloc) != (parsed_base.scheme, parsed_base.netloc):
            msg = "url host does not match site_base_url"
            raise ValueError(msg)
        return parsed_url.path

    return parsed_url.path or url


def url_to_vault_path(*, url: str, site_base_url: str, flat_urls: bool) -> str:
    path_part = _url_path_component(url=url, site_base_url=site_base_url)

    cleaned = path_part.strip("/")
    if not cleaned:
        cleaned = DEFAULT_INDEX_STEM

    if not cleaned.endswith(".md"):
        if flat_urls:
            cleaned = f"{cleaned}.md"
        else:
            cleaned = f"{cleaned}.md"

    return normalize_vault_path(cleaned)


def vault_path_to_url(*, path: str, site_base_url: str, flat_urls: bool) -> str:
    normalized = normalize_vault_path(path)
    stem = normalized[:-3] if normalized.endswith(".md") else normalized

    base = site_base_url.rstrip("/")
    if stem == DEFAULT_INDEX_STEM:
        return f"{base}/"

    if flat_urls:
        return f"{base}/{stem}"

    return f"{base}/{stem}/"


def resolve_path_or_url(
    *,
    path: str | None,
    url: str | None,
    site_base_url: str,
    flat_urls: bool,
) -> str:
    has_path = path is not None and bool(path.strip())
    has_url = url is not None and bool(url.strip())

    if has_path == has_url:
        msg = "provide exactly one of path or url"
        raise ValueError(msg)

    if has_path:
        return normalize_vault_path(path or "")

    return url_to_vault_path(url=url or "", site_base_url=site_base_url, flat_urls=flat_urls)
