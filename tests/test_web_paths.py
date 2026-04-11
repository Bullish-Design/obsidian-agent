import pytest

from obsidian_agent.web_paths import (
    normalize_vault_path,
    resolve_path_or_url,
    url_to_vault_path,
    vault_path_to_url,
)


def test_normalize_vault_path_accepts_valid_relative_path() -> None:
    assert normalize_vault_path("Projects/Alpha.md") == "Projects/Alpha.md"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "/Projects/Alpha.md",
        "../Projects/Alpha.md",
        "Projects/../Alpha.md",
        "Projects\\Alpha.md",
    ],
)
def test_normalize_vault_path_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_vault_path(value)


def test_url_to_vault_path_absolute_url_requires_matching_host() -> None:
    with pytest.raises(ValueError, match="host does not match"):
        url_to_vault_path(url="https://example.com/Projects/Alpha", site_base_url="http://localhost:8080", flat_urls=False)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("/", "index.md"),
        ("/Projects/Alpha/", "Projects/Alpha.md"),
        ("/Projects/Alpha", "Projects/Alpha.md"),
        ("/Projects/Alpha.md", "Projects/Alpha.md"),
    ],
)
def test_url_to_vault_path_maps_to_markdown_path(url: str, expected: str) -> None:
    assert url_to_vault_path(url=url, site_base_url="http://localhost:8080", flat_urls=False) == expected


def test_vault_path_to_url_round_trip_non_flat() -> None:
    url = vault_path_to_url(path="Projects/Alpha.md", site_base_url="http://localhost:8080", flat_urls=False)

    assert url == "http://localhost:8080/Projects/Alpha/"


def test_vault_path_to_url_round_trip_flat() -> None:
    url = vault_path_to_url(path="Projects/Alpha.md", site_base_url="http://localhost:8080", flat_urls=True)

    assert url == "http://localhost:8080/Projects/Alpha"


def test_resolve_path_or_url_requires_exactly_one() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        resolve_path_or_url(
            path="Projects/Alpha.md",
            url="/Projects/Alpha",
            site_base_url="http://localhost:8080",
            flat_urls=False,
        )

    with pytest.raises(ValueError, match="exactly one"):
        resolve_path_or_url(path=None, url=None, site_base_url="http://localhost:8080", flat_urls=False)


def test_resolve_path_or_url_prefers_path_when_provided() -> None:
    assert (
        resolve_path_or_url(
            path="Projects/Alpha.md",
            url=None,
            site_base_url="http://localhost:8080",
            flat_urls=False,
        )
        == "Projects/Alpha.md"
    )


def test_resolve_path_or_url_uses_url() -> None:
    assert (
        resolve_path_or_url(
            path=None,
            url="/Projects/Alpha/",
            site_base_url="http://localhost:8080",
            flat_urls=False,
        )
        == "Projects/Alpha.md"
    )
