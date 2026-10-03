"""Drop retailer results that do not match what the user asked for.

Two rules, applied per searched outfit item (e.g. "white sneakers"):
1. The item's main noun (its last meaningful word) must appear in the product title.
2. Titles for costumes, bag organizers, replacement parts etc. are excluded, unless the user asked for them.
"""
import re

_WORD = re.compile(r"[a-z0-9]+")

# Words that describe the wearer / filler, never the product itself.
_STOP = {"for", "women", "womens", "woman", "men", "mens", "man", "girl", "girls", "boy", "boys", "lady", "ladies",
         "kids", "the", "a", "an", "of", "in", "and", "or", "new", "size", "pakistani", "indian", "desi"}
_COLORS = {"black", "white", "red", "blue", "green", "yellow", "pink", "purple", "brown", "grey", "gray", "beige",
           "navy", "orange", "gold", "silver", "cream", "tan", "khaki", "burgundy", "olive", "maroon", "teal"}

# Listings that are never the garment/accessory itself.
_NEGATIVE = {"costume", "costumes", "cosplay", "halloween", "organizer", "organiser", "insert", "liner", "shaper",
             "replacement", "sticker", "stickers", "keychain", "figure", "figurine", "poster", "doll", "toy",
             "mannequin", "hanger", "hangers", "cleaner", "protector", "perfume", "cologne", "fragrance",
             "parfum", "spray", "deodorant"}


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _stem(w: str) -> str:
    if w.endswith(("sses", "ches", "shes", "xes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def main_noun(part: str) -> str | None:
    """'white sneakers' -> 'sneaker'; 'pakistani embroidered kurti' -> 'kurti'."""
    words = [w for w in _words(part) if w not in _STOP and w not in _COLORS]
    return _stem(words[-1]) if words else None


def is_relevant(part: str, title: str) -> bool:
    asked = set(_words(part))
    title_words = _words(title)
    if any(w in _NEGATIVE and w not in asked for w in title_words):
        return False
    noun = main_noun(part)
    if noun is None:
        return True
    return any(_stem(w) == noun or _stem(w).endswith(noun) for w in title_words)  # 'tshirt'/'handbag' still match
