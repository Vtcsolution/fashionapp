"""Shared types and the offline fallback parser for outfit prompts."""
import re
from dataclasses import dataclass

from .categorize import categorize

MAX_ITEMS = 6  # outfit items per prompt
CATEGORIES = ["outerwear", "tops", "dresses", "bottoms", "shoes", "bags", "accessories", "other"]

COLORS = {"black", "white", "red", "blue", "green", "yellow", "pink", "purple", "brown", "grey", "gray", "beige",
          "navy", "orange", "gold", "silver", "cream", "tan", "khaki", "burgundy", "olive"}
_SPLIT = r"\s*(?:,|;|\+|&|\bwith\b|\band\b)\s*"  # \b keeps 'handbag', 'sandals', 'within' intact


@dataclass
class PromptItem:
    query: str  # what to search for, e.g. "brown leather handbag"
    category: str  # one of CATEGORIES


def split_prompt(text: str) -> list[str]:
    """'red dress with black heels and handbag' -> ['red dress', 'black heels', 'handbag'].

    Each outfit item is searched on its own so every category gets real results. 'and' between two
    colors ('black and white sneakers') is NOT a split point.
    """
    tokens = re.split(_SPLIT, text.strip(), flags=re.I)
    seps = re.findall(_SPLIT, text.strip(), flags=re.I)
    parts: list[str] = []
    for i, tok in enumerate(tokens):
        tok = tok.strip()
        if not tok:
            continue
        prev = parts[-1] if parts else ""
        joined_and = i > 0 and seps[i - 1].strip().lower() == "and"
        if joined_and and prev and prev.split()[-1].lower() in COLORS and tok.split()[0].lower() in COLORS:
            parts[-1] = f"{prev} and {tok}"  # keep 'black and white ...' together
        else:
            parts.append(tok)
    return (parts or [text.strip()])[:MAX_ITEMS]


def fallback_items(text: str) -> list[PromptItem]:
    return [PromptItem(p, categorize(p)) for p in split_prompt(text)]
