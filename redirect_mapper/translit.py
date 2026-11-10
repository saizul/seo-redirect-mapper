"""Simple Bangla to Latin transliteration used as a slug-matching fallback.

This is intentionally lightweight (no external dependency): it maps each
Bangla letter to a conventional Latin spelling and drops the inherent vowel,
which is good enough to compare an old Bangla URL slug against a Latin slug.
"""
from __future__ import annotations

import unicodedata

_NUKTA = "\u09bc"
_HASANTA = "\u09cd"

_COMPOSED = {
    "\u09a1" + _NUKTA: "r",  # ড়
    "\u09a2" + _NUKTA: "rh",  # ঢ়
    "\u09af" + _NUKTA: "y",  # য়
}

_MAP = {
    # independent vowels
    "অ": "o", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u", "ঋ": "ri",
    "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou",
    # consonants
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng",
    "চ": "ch", "ছ": "chh", "জ": "j", "ঝ": "jh", "ঞ": "n",
    "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n",
    "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
    "প": "p", "ফ": "f", "ব": "b", "ভ": "bh", "ম": "m",
    "য": "j", "র": "r", "ল": "l", "শ": "sh", "ষ": "sh", "স": "s", "হ": "h",
    "ড়": "r", "ঢ়": "rh", "য়": "y", "ৎ": "t", "ং": "ng", "ঃ": "h", "ঁ": "n",
    # vowel signs
    "া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u", "ৃ": "ri",
    "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou",
    # digits
    "০": "0", "১": "1", "২": "2", "৩": "3", "৪": "4",
    "৫": "5", "৬": "6", "৭": "7", "৮": "8", "৯": "9",
}


def has_bangla(text: str) -> bool:
    """Return True if *text* contains characters from the Bengali block."""
    return any("\u0980" <= ch <= "\u09ff" for ch in text)


def transliterate(text: str) -> str:
    """Transliterate Bangla characters in *text* to Latin; other text is kept."""
    if not has_bangla(text):
        return text
    text = unicodedata.normalize("NFC", text)
    for composed, latin in _COMPOSED.items():
        text = text.replace(composed, latin)
    out = []
    for ch in text:
        if ch == _HASANTA or ch == _NUKTA:
            continue
        out.append(_MAP.get(ch, ch))
    return "".join(out)
