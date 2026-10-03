"""Keyword-based fashion category for a product title.

Deliberately conservative: a title that matches no rule becomes "other". It is never forced into a
category. Rules are checked in priority order, so "dress shoes" is shoes (not dresses) and
"top handle bag" is a bag (not a top).
"""
import re

ORDER = ["outerwear", "tops", "dresses", "bottoms", "shoes", "bags", "accessories", "other"]

_RULES: list[tuple[str, str]] = [
    ("shoes", r"sneakers?|trainers?|shoes?|boots?|booties|heels?|pumps?|loafers?|sandals?|slippers?|flats|oxfords?|"
              r"stilettos?|wedges?|clogs?|moccasins?|espadrilles?|footwear|slides"),
    ("bags", r"handbags?|bags?|purses?|backpacks?|totes?|clutch(?:es)?|satchels?|crossbody|wallets?|duffels?|"
             r"shoulder bag|fanny pack|messenger"),
    ("accessories", r"watch(?:es)?|sunglasses|glasses|necklaces?|bracelets?|earrings?|rings?|belts?|scarf|scarves|"
                    r"hats?|caps?|beanies?|gloves?|ties?|bowtie|jewel(?:ry|lery)|brooch|anklets?|headbands?"),
    ("dresses", r"dress(?:es)?|gowns?|jumpsuits?|rompers?|playsuits?|sundress"),
    ("outerwear", r"jackets?|coats?|blazers?|parkas?|cardigans?|vests?|windbreakers?|puffers?|trench|bombers?|"
                  r"overcoats?|raincoats?|anoraks?|poncho|capes?|shackets?"),
    ("bottoms", r"jeans|pants|trousers|shorts|skirts?|leggings?|joggers?|chinos|culottes|sweatpants|cargos?|"
                r"capris?|bottoms"),
    ("tops", r"shirts?|t-?shirts?|tees?|blouses?|tops?|tanks?|camisoles?|sweaters?|hoodies?|sweatshirts?|"
             r"pullovers?|polos?|jerseys?|tunics?|knitwear|bodysuits?|crop"),
]
_COMPILED = [(cat, re.compile(rf"\b(?:{pat})\b", re.I)) for cat, pat in _RULES]


# Costume/cosplay listings are multi-piece sets, not one garment: do not guess a single category.
_SET = re.compile(r"\b(?:cosplay|costumes?|halloween|full set|roleplay)\b", re.I)
# Accessories mentioned after these words ("Jacket with Belt") must not decide the category.
_TAIL = re.compile(r"\s(?:with|w/|includes?|including)\s|[,|+]", re.I)


def categorize(title: str) -> str:
    title = title or ""
    if _SET.search(title):
        return "other"
    head = _TAIL.split(title, maxsplit=1)[0]
    for cat, rx in _COMPILED:
        if rx.search(head):
            return cat
    return "other"
