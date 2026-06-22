"""Curated cat LGTM GIFs embedded on APPROVE reviews — hyejin-bot signature.

When the `pr_review` handler posts a GitHub **APPROVE** event, it drops a
celebratory cat GIF into the Summary — hyejin-bot's "LGTM" signal, deliberately
distinct from upstream daeyeon-bot's pop-culture set. The cat theme aligns
with the persona sign-off `— hyejin-bot 🐱✨`.

The GIF list is a *static curated set* of Giphy cat reactions. We deliberately
do NOT fetch any gallery at runtime: the 24/7 daemon takes no new network
dependency, no HTML to parse on every approval, no breakage when external
markup changes. Trade-off: refreshing the roster is a code change.

Each entry is `(slug, giphy_media_id, ko_caption)`:
- `slug` is the kebab-case nickname (the markdown alt-text).
- `giphy_media_id` is the stable Giphy media id; the embed URL is
  `https://media.giphy.com/media/<id>/giphy.gif`.
- `ko_caption` is the short Korean reaction the operator wants attached —
  daeyeon-bot uses no caption, hyejin-bot adds one so the LGTM also speaks
  in the operator's voice. (See `pick_lgtm_gif` for assembly.)

The pick is deterministic per head SHA so a force re-review of the same
commit shows the same GIF + caption (no churn).
"""

from __future__ import annotations

# (slug, giphy media id, ko_caption) — hyejin-bot cat LGTM curated set, 2026-06-22.
# To refresh: harvest cat reaction GIFs from giphy.com, capture the media id from
# the URL (`https://giphy.com/gifs/<slug>-<id>`), preview at
# `https://media.giphy.com/media/<id>/giphy.gif`, then drop in below.
_LGTM_GIFS: tuple[tuple[str, str, str], ...] = (
    ("cat-typing-fast", "vFKqnCdLPNOKc", "타이핑이 빠른 고양이만큼 깔끔한 PR."),
    ("cat-thumbs-up", "111ebonMs90YLu", "고양이도 인정."),
    ("cat-clap", "rl0FOxdz7CcxOgPGqk", "박수."),
    ("cat-jam", "5T06ftQWtCMy0XFaaI", "통과."),
    ("cat-yes", "tHIRLHtNwxpjIFqPdV", "예."),
    ("cat-vibe", "13CoXDiaCcCoyk", "vibe check 통과."),
    ("cat-shocked-good", "3o7TKsQbWLBPnuMtCw", "이 정도면 머지각."),
    ("cat-okay-paw", "QQv8jJpNvVOyklRjr2", "okay paw — 좋습니다."),
    ("cat-fast-keyboard", "MDJ9IbxxvDUQM", "키보드 위 고양이도 동의."),
    ("cat-nod", "ToMjGpx9F5ktZw8qPUQ", "끄덕."),
    ("cat-ship-it", "l46Cy1rHbQ7qg9Lzi", "ship it."),
    ("cat-good-job", "26gN0XKMTQXkO7t4Y", "good job — 머지하셔도 됩니다."),
)


def _seed_index(seed: str, n: int) -> int:
    """Map `seed` (a head SHA) to an index in `[0, n)`, deterministically.

    Uses the leading hex of the SHA so the same commit always yields the same
    GIF. Falls back to a char-sum when `seed` isn't hex (defensive — head_sha
    is always hex in practice).
    """
    if n <= 0:
        return 0
    prefix = seed[:8] or "0"
    try:
        return int(prefix, 16) % n
    except ValueError:
        return sum(map(ord, seed)) % n


def pick_lgtm_gif(seed: str) -> str:
    """Return a markdown image line + Korean caption for an LGTM, chosen by `seed`.

    Format: two lines — the markdown image, then the caption beneath it. The
    caption gives the LGTM voice; the GIF carries the energy.
    """
    slug, gif_id, caption = _LGTM_GIFS[_seed_index(seed, len(_LGTM_GIFS))]
    url = f"https://media.giphy.com/media/{gif_id}/giphy.gif"
    return f"![LGTM: {slug}]({url})\n\n_{caption}_"


__all__ = ["pick_lgtm_gif"]
