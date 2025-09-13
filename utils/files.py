# file reading, docx→md, path helpers
import re
from pathlib import Path

def parse_chapter_filename(fname: str, split_mode: str|None) -> tuple[int|None, str]:
    """
    Returns (order_hint, clean_title)
    - order_hint: int if a leading number was found (used for bulk ordering), else None
    - clean_title: title after optional split and cleanup
    split_mode options:
      None        -> no split, just cleanup
      " - "       -> split once on " - "
      ". "        -> split once on ". "
      "<custom>"  -> any string; if not found, no split
    """
    stem = Path(fname).stem

    # 1) optional split on first separator
    title_part = stem
    if split_mode:
        parts = stem.split(split_mode, 1)
        if len(parts) == 2:
            title_part = parts[1] or parts[0]  # prefer the right side
        else:
            title_part = stem  # no match -> fallback

    # 2) grab leading number ON THE ORIGINAL STEM (ordering hint)
    m = re.match(r"^\s*(\d+)", stem)
    order_hint = int(m.group(1)) if m else None

    if split_mode is not None:
        # 3) cleanup: strip common leading numbering/decoration from title_part
        # e.g., "01 - My Title" -> "My Title", "2. Title" -> "Title"
        clean = re.sub(r"^\s*[\d]+[\s\.\-_:]*", "", title_part).strip()

        # ensure something
        clean = clean or stem.strip()
    else:
        clean = title_part.strip() or stem.strip()
    return order_hint, clean

def singularize(word: str) -> str:
    # Simple, readable mapping + fallback
    # Some common words that don't follow simple rules
    mapping = {
        "People": "Person",
        "Children": "Child",
    }
    if word in mapping:
        return mapping[word]
    elif word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    elif word.endswith("es") and len(word) > 2:
        return word[:-1]
    elif word.endswith("s") and len(word) > 1:
        return word[:-1]
    elif word.endswith("a") and len(word) > 1:
        return word[:-1] + "um"
    elif word.endswith("i") and len(word) > 1:
        return word[:-1] + "us"
    return word
