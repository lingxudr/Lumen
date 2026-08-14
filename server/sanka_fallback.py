"""Backward-compatible re-export — implementasi di providers/sanka.py """
from __future__ import annotations

try:
    from server.providers.sanka import *  # noqa: F401,F403
    from server.providers import sanka as _impl
except Exception:
    from providers.sanka import *  # type: ignore  # noqa: F401,F403
    from providers import sanka as _impl  # type: ignore

# explicit names used by app
get_terbaru = _impl.get_terbaru
get_populer = _impl.get_populer
search = _impl.search
looks_like_uuid = _impl.looks_like_uuid
get_detail_shinigami = _impl.get_detail_shinigami
get_chapters_shinigami = _impl.get_chapters_shinigami
get_pages_shinigami = _impl.get_pages_shinigami
get_chapter_images_komiku = _impl.get_chapter_images_komiku
