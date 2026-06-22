"""Tests for the curated LGTM-GIF picker (`handlers/pr_review_lgtm.py`)."""

from __future__ import annotations

from hyejin_bot.handlers.pr_review_lgtm import (
    _LGTM_GIFS,  # pyright: ignore[reportPrivateUsage]
    _seed_index,  # pyright: ignore[reportPrivateUsage]
    pick_lgtm_gif,
)


def test_pick_returns_markdown_image_with_giphy_url() -> None:
    out = pick_lgtm_gif("deadbeef")
    assert out.startswith("![LGTM: ")
    assert "](https://media.giphy.com/media/" in out
    # Caption follows on a separate italicized line.
    lines = out.split("\n")
    assert lines[0].endswith("/giphy.gif)")
    assert lines[-1].startswith("_") and lines[-1].endswith("_")


def test_pick_is_deterministic_per_seed() -> None:
    """A force re-review of the same commit must render the same GIF."""
    assert pick_lgtm_gif("deadbeef") == pick_lgtm_gif("deadbeef")


def test_pick_varies_across_seeds() -> None:
    # Two seeds whose 8-hex prefixes land on different list indices.
    n = len(_LGTM_GIFS)
    a = f"{0:08x}"
    b = f"{1:08x}"
    assert _seed_index(a, n) != _seed_index(b, n)
    assert pick_lgtm_gif(a) != pick_lgtm_gif(b)


def test_seed_index_within_bounds_for_hex() -> None:
    n = len(_LGTM_GIFS)
    for sha in ("0", "ffffffff", "abc123de", "00000007"):
        assert 0 <= _seed_index(sha, n) < n


def test_seed_index_tolerates_non_hex() -> None:
    n = len(_LGTM_GIFS)
    assert 0 <= _seed_index("not-hex-zzz", n) < n


def test_seed_index_empty_list() -> None:
    assert _seed_index("deadbeef", 0) == 0


def test_all_gifs_produce_valid_urls() -> None:
    for slug, gif_id, caption in _LGTM_GIFS:
        assert slug and gif_id and caption
        assert " " not in gif_id


def test_caption_is_korean_one_line() -> None:
    """Each caption is a short Korean reaction — single line, ends with period."""
    for _slug, _gif_id, caption in _LGTM_GIFS:
        assert "\n" not in caption
        assert caption.endswith(".")
