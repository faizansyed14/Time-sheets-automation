"""
Word-CAPTCHA: a randomly generated word rendered to a distorted PNG.

generate() -> (captcha_id, png_bytes); the answer is stashed in the cache under
the id with a short TTL. verify() pops it and compares case-insensitively.
"refresh" on the client is just a second generate() call (new id + image).
"""
from __future__ import annotations

import io
import random
import secrets

from app.core.cache import cache
from app.core.config import settings
from app.services.extraction.file_processor import _load_font

# Avoid visually ambiguous characters (0/O, 1/l/I).
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _random_word(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _key(captcha_id: str) -> str:
    return f"captcha:{captcha_id}"


def _render(word: str) -> bytes:
    from PIL import Image, ImageDraw

    w, h = 220, 80
    img = Image.new("RGB", (w, h), (245, 247, 250))
    d = ImageDraw.Draw(img)
    # noise lines
    for _ in range(6):
        d.line(
            [(random.randint(0, w), random.randint(0, h)) for _ in range(2)],
            fill=(random.randint(150, 210), random.randint(150, 210), random.randint(150, 210)),
            width=2,
        )
    # characters, each jittered/rotated
    x = 18
    for ch in word:
        font = _load_font(random.randint(34, 44))
        glyph = Image.new("RGBA", (44, 60), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glyph)
        gd.text((4, 2), ch, font=font,
                fill=(random.randint(20, 90), random.randint(20, 90), random.randint(90, 160)))
        glyph = glyph.rotate(random.randint(-28, 28), expand=1, resample=Image.BICUBIC)
        img.paste(glyph, (x, random.randint(6, 20)), glyph)
        x += 32
    # speckle
    for _ in range(450):
        img.putpixel((random.randint(0, w - 1), random.randint(0, h - 1)),
                     (random.randint(120, 220),) * 3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def generate() -> tuple[str, bytes]:
    word = _random_word(settings.captcha_length)
    captcha_id = secrets.token_urlsafe(16)
    await cache.set(_key(captcha_id), word.upper(), ttl=settings.captcha_ttl_seconds)
    return captcha_id, _render(word)


async def verify(captcha_id: str, answer: str) -> bool:
    if not captcha_id or not answer:
        return False
    stored = await cache.get(_key(captcha_id))
    await cache.delete(_key(captcha_id))  # single-use
    if not stored:
        return False
    return secrets.compare_digest(str(stored).upper(), answer.strip().upper())
