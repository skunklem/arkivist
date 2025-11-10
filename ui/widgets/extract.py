from __future__ import annotations
import re, calendar
from collections import Counter
from typing import Sequence, Iterable, Any
import spacy
import en_core_web_sm
from collections import defaultdict

# ---------- text helpers ----------

def strip_markup(md: str) -> str:
    """
    Makes a reasonable plain-text view for metrics.
    We don't try to keep offset mapping here—use original text for anchored quotes.
    """
    s = md or ""
    # remove code fences/indented code
    s = re.sub(r"```.*?```", "", s, flags=re.DOTALL)
    s = re.sub(r"(^|\n)( {4}|\t).*(\n|$)", r"\1\3", s)
    # strip images/links: [alt](url) -> alt
    s = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    # headings, blockquotes, list markers
    s = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", s)
    s = re.sub(r"(?m)^\s{0,3}>\s?", "", s)
    s = re.sub(r"(?m)^\s*[-*+]\s+", "", s)
    s = re.sub(r"(?m)^\s*\d+\.\s+", "", s)
    # emphasis markers
    s = re.sub(r"[*_]{1,3}", "", s)
    # html tags
    s = re.sub(r"<[^>]+>", "", s)
    # collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s

# ---------- metrics (cheap) ----------

def compute_metrics(text: str) -> dict[str, Any]:
    plain = strip_markup(text)
    # paragraphs (rough)
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    # sentences (very rough)
    sentences = re.split(r"(?<=[.!?])\s+", plain) if plain else []
    words = re.findall(r"\b[\w'-]+\b", plain)
    wc = len(words)
    sc = len(sentences) if sentences and sentences[0] else 0
    avg_s = (sum(len(re.findall(r"\b[\w'-]+\b", s)) for s in sentences) / sc) if sc else 0.0
    types = len(set(w.lower() for w in words))
    ttr = float(types) / wc if wc else 0.0

    # dialogue: words inside “quotes”
    quoted = re.findall(r"\"([^\"]+)\"|'([^']+)'|“([^”]+)”|‘([^’]+)’", text)
    quoted_text = " ".join("".join(t) for t in quoted) if quoted else ""
    d_words = len(re.findall(r"\b[\w'-]+\b", quoted_text))
    d_ratio = (d_words / wc) if wc else 0.0

    # simple reading time & pages
    wpm = 250
    secs = int(round((wc / wpm) * 60))
    est_pages = wc / 300.0  # paperback-ish

    return dict(
        word_count=wc,
        char_count=len(plain),
        paragraph_count=len(paragraphs),
        sentence_count=sc,
        avg_sentence_len=avg_s,
        type_token_ratio=ttr,
        dialogue_words=d_words,
        dialogue_ratio=d_ratio,
        reading_secs=secs,
        est_pages=est_pages
    )

# ---------- candidates (heuristic + optional spaCy) ----------

_KIND_FROM_NER = {
    "PERSON": "character",
    "ORG": "organization",
    "GPE": "place", "LOC": "place", "FAC": "place",
    "WORK_OF_ART": "object", "PRODUCT": "object", "EVENT": "concept"
}

def ner_spans(text: str):
    nlp = spacy.load("en_core_web_sm")
    doc = nlp(text)
    out = []
    for ent in doc.ents:
        out.append((ent.text, ent.label_, ent.start_char, ent.end_char))
    return out

DET_WORDS = {"the","a","an"}
NER_KEEP = {"PERSON","ORG","GPE","LOC","FAC","PRODUCT","WORK_OF_ART","EVENT","NORP","LANGUAGE"}
NER_DROP = {"DATE","TIME","MONEY","PERCENT","ORDINAL","CARDINAL","QUANTITY"}
_SPLIT_CONNECTORS = {"for", "of"}  # we gate 'of' more tightly below

def _strip_possessive(surface: str) -> tuple[str, bool]:
    s = surface.rstrip()
    if s.endswith("'s") or s.endswith("’s"):
        return s[:-2].rstrip(), True
    return surface, False

def _norm_tail(surface: str) -> str:
    """det-less lowercased tail for DB/known-phrase checks."""
    s = surface.strip()
    low = s.lower()
    for det in DET_WORDS:
        if low.startswith(det + " "):
            return low[len(det)+1:]
    return low

def maybe_split_owner_relation(text: str, start_off: int, end_off: int) -> list[tuple[str,int,int,str]]:
    """
    Return [(surface, s, e, reason), ...] if we should split a long entity like:
      'Black Gate for Solara’s' -> ['Black Gate', 'Solara']
    Rules:
      - split on lowercase 'for' always, if both sides look like names/titles.
      - split on lowercase 'of' only if right side is possessive (’s/'s).
      - right side must start with a capitalized token.
    """
    sub = text[start_off:end_off]
    # Find lowercase connector tokens
    for m in re.finditer(r"\b(for|of)\b", sub):
        conn = m.group(1)
        # ensure actually lowercase in text
        if sub[m.start():m.end()].islower():
            left = sub[:m.start()].strip()
            right = sub[m.end():].strip()
            if not left or not right:
                continue
            # right must start with capital
            if not right[:1].isupper():
                continue

            # 'of' only when possessive on right
            right_stripped, had_poss = _strip_possessive(right)
            if conn == "of" and not had_poss:
                continue

            # simple “looks like a title/name” check for left/right
            def looks_like_title(s: str) -> bool:
                parts = [p for p in s.split() if p]
                caps = sum(1 for p in parts if p[:1].isupper())
                return caps >= 1

            if looks_like_title(left) and looks_like_title(right_stripped):
                # compute absolute spans
                s1, e1 = start_off, start_off + m.start()
                # skip trailing space before connector
                while e1 > s1 and text[e1-1].isspace():
                    e1 -= 1
                # right span (strip possessive)
                s2 = start_off + m.end()
                while s2 < end_off and text[s2].isspace():
                    s2 += 1
                e2 = end_off
                if right.endswith("'s") or right.endswith("’s"):
                    e2 -= 2
                return [(left, s1, e1, f"split-{conn}"), (right_stripped, s2, e2, f"split-{conn}")]
    return []

def spacy_doc(text):
    # Cache across calls: module-level singletons are fine
    if not hasattr(spacy_doc, "_nlp"):
        print("Loading spaCy model...")
        spacy_doc._nlp = en_core_web_sm.load()
    else:
        print("Using cached spaCy model.")
    return spacy_doc._nlp(text)

def spacy_candidates(text: str) -> list[dict]:
    print("Parsing tex:", text)
    doc = spacy_doc(text)
    if not doc: 
        return []
    out = []
    for ent in doc.ents:
        if ent.label_ in NER_DROP:
            continue
        if ent.label_ in NER_KEEP:
            print("Spacy entity:", ent.text, ent.label_)
            kind = {
                "PERSON":"character","ORG":"organization",
                "GPE":"place","LOC":"place","FAC":"place",
                "PRODUCT":"object","WORK_OF_ART":"object","EVENT":"concept",
                "NORP":"culture","LANGUAGE":"language"
            }.get(ent.label_, None)
            out.append(dict(
                surface=ent.text, start_off=ent.start_char, end_off=ent.end_char,
                kind_guess=kind, context=None, confidence=0.65
            ))
    return out

def _is_sentence_initial(doc, start_char: int) -> bool:
    for s in doc.sents:
        if s.start_char <= start_char < s.end_char:
            # sentence-initial if this span begins before any non-space token in the sentence
            first_tok = next((t for t in s if not t.is_space), None)
            return first_tok is not None and first_tok.idx == start_char
    return False

def _title_case_bonus(surface: str) -> float:
    # generous: allow connectors like of/the/and to be lowercase between caps
    parts = surface.strip().split()
    if not parts:
        return 0.0
    has_cap = sum(1 for w in parts if w[:1].isupper())
    if has_cap >= 2:
        return 0.15
    if has_cap == 1 and len(parts) == 1:
        return 0.10  # single proper-looking name
    return 0.0

def _score_spacy_entity(surface: str, start_char: int, doc, label: str) -> float:
    # base by source
    score = 0.70  # spaCy entity baseline

    # label strength
    if label in NER_KEEP:
        score += 0.05

    # title-case structure
    score += _title_case_bonus(surface)

    # mid-sentence boost (helps lowercase sentence-initial determiners)
    if not _is_sentence_initial(doc, start_char):
        score += 0.05

    # cap & floor
    return max(0.35, min(0.95, score))

def spacy_candidates_strict(text: str, known_phrases: set[str]) -> list[dict]:
    """
    spaCy-only candidates with:
      - lowercase-det strip
      - DB skip via det-less tail
      - '&' bonus
      - owner split: 'for' always; 'of' only if right is possessive (Y’s/'s)
    """
    doc = spacy_doc(text)
    if not doc:
        return []
    raw = []

    # collect raw + det-less variants
    for ent in doc.ents:
        if ent.label_ in NER_DROP or ent.label_ not in NER_KEEP:
            continue
        start, end = ent.start_char, ent.end_char
        surface = ent.text
        first_tok = doc[ent.start]
        has_lower_det = (first_tok.text.lower() in DET_WORDS and first_tok.text.islower())

        # Try to split owner relation BEFORE anything else
        splits = maybe_split_owner_relation(text, start, end)
        if splits:
            for surf, s, e, _why in splits:
                tail = _norm_tail(surf)
                if tail in known_phrases:
                    continue
                raw.append(dict(surface=surf, start_off=s, end_off=e, label=ent.label_, doc=doc))
            # swallow the long ent; we emitted its parts
            continue

        # otherwise push det-less variant (if lowercase det) + original
        if has_lower_det:
            s2 = first_tok.idx + len(first_tok.text)
            while s2 < end and text[s2].isspace():
                s2 += 1
            if s2 < end:
                surf2 = text[s2:end]
                tail = _norm_tail(surf2)
                if tail not in known_phrases:
                    raw.append(dict(surface=surf2, start_off=s2, end_off=end, label=ent.label_, doc=doc))
        # original (skip if known)
        tail0 = _norm_tail(surface)
        if tail0 not in known_phrases:
            raw.append(dict(surface=surface, start_off=start, end_off=end, label=ent.label_, doc=doc))

    # group by det-less tail to canonicalize within this doc
    groups = defaultdict(list)
    for r in raw:
        groups[_norm_tail(r["surface"])].append(r)

    out = []
    for tail, items in groups.items():
        if not tail:
            continue

        # prefer det-less canonical if:
        #  - any lowercase leading 'the/a/an' variants exist in this group, OR
        #  - any item already appears det-less (exact tail)
        def _is_lower_det(s: str) -> bool:
            head = s.strip().split(" ", 1)[0]
            return head.lower() in DET_WORDS and head.islower()

        has_lower_det = any(_is_lower_det(i["surface"]) for i in items)
        detless_items = [i for i in items if i["surface"].strip().lower() == tail]
        has_detless   = bool(detless_items)

        if has_lower_det or has_detless:
            # pick earliest det-less span if present; otherwise earliest overall
            rep_src = detless_items if detless_items else items
            rep = min(rep_src, key=lambda x: x["start_off"])
        else:
            # keep the capitalized 'The …' form when that's the only form seen
            rep = min(items, key=lambda x: x["start_off"])

        surface = text[rep["start_off"]:rep["end_off"]]
        label = rep["label"]; doc = rep["doc"]
        kind = _KIND_FROM_NER.get(label)
        conf = _score_spacy_entity(surface, rep["start_off"], doc, label)

        if "&" in surface and re.search(r"\w\s*&\s*\w", surface):
            conf = min(0.95, conf + 0.08)
            if not kind:
                kind = "organization"

        out.append(dict(surface=surface,
                        start_off=rep["start_off"], end_off=rep["end_off"],
                        kind_guess=kind, context=None, confidence=conf))

    # dedup exact surface+span
    seen, uniq = set(), []
    for c in out:
        k = (c["surface"].strip(), c["start_off"], c["end_off"])
        if k in seen: 
            continue
        seen.add(k); uniq.append(c)
    return uniq

def noun_chunk_candidates(text: str) -> list[dict]:
    doc = spacy_doc(text)
    if not doc: 
        return []
    GENERIC_HEADS = {"thing","something","someone","time","day","way","man","woman","people","place"}
    out = []
    for nc in doc.noun_chunks:
        head = nc.root.lemma_.lower()
        if head in GENERIC_HEADS: 
            continue
        # skip if it’s just a pronoun
        if nc.root.pos_ == "PRON":
            continue
        out.append(dict(
            surface=nc.text, start_off=nc.start_char, end_off=nc.end_char,
            kind_guess=None, context=None, confidence=0.5
        ))
    return out
def ner_filter_and_enrich(candidates, text):
    nlp = spacy.load("en_core_web_sm")
    by_span = {(c["start_off"], c["end_off"]): c for c in candidates if c["start_off"] is not None}
    doc = nlp(text)
    out = []
    for ent in doc.ents:
        if ent.label_ in NER_DROP:
            # remove overlapping candidates of dropped types
            for k, c in list(by_span.items()):
                if not (ent.end_char <= k[0] or ent.start_char >= k[1]):
                    by_span.pop(k, None)
            continue
        if ent.label_ in NER_KEEP:
            # enrich overlapping candidate or add new
            found = None
            for k, c in by_span.items():
                if not (ent.end_char <= k[0] or ent.start_char >= k[1]):
                    found = c; break
            if found:
                found["kind_guess"] = found.get("kind_guess") or {
                    "PERSON":"character","ORG":"organization",
                    "GPE":"place","LOC":"place","FAC":"place",
                    "PRODUCT":"object","WORK_OF_ART":"object","EVENT":"concept"
                }.get(ent.label_, None)
            else:
                out.append(dict(surface=ent.text, start_off=ent.start_char, end_off=ent.end_char,
                                kind_guess={"PERSON":"character","ORG":"organization",
                                            "GPE":"place","LOC":"place","FAC":"place",
                                            "PRODUCT":"object","WORK_OF_ART":"object","EVENT":"concept"}.get(ent.label_),
                                context=None, confidence=0.55))
    return list(by_span.values()) + out

STOP_CAPS = {*(m for m in calendar.month_name if m),
             *(d for d in calendar.day_name if d),
             "I","AM","PM","No","Yes","Chapter","Act","Part","Prologue","Epilogue"}

SENT_BOUNDARY = re.compile(r"[.!?…]\s*$")

def _sentence_initial(text: str, start: int) -> bool:
    # look back up to 3 chars non-space
    j = start - 1
    while j >= 0 and text[j].isspace():
        j -= 1
    if j < 0:
        return True
    # true if previous char ends a sentence
    return SENT_BOUNDARY.search(text[:j+1]) is not None

def find_known_spans(text: str, known_phrases: set[str]) -> list[tuple[int,int]]:
    if not text or not known_phrases: return []
    hay = re.sub(r"\s+", " ", text.lower()).strip()
    # sort longest-first so earlier spans dominate
    phrases = sorted((p for p in known_phrases if p), key=len, reverse=True)
    used = []
    for p in phrases:
        patt = r"(?<!\w)"+re.escape(p)+r"(?!\w)"
        for m in re.finditer(patt, hay):
            s,e = m.span()
            if any(not (e<=ps or s>=pe) for ps,pe in used):
                continue
            used.append((s,e))
    return used

def _inside_any(span: tuple[int,int], spans: list[tuple[int,int]]) -> bool:
    s,e = span
    return any(s>=ps and e<=pe for ps,pe in spans)

def heuristic_candidates_spacy(doc, known_spans) -> list[dict]:
    out = []
    def add(surface, s, e, bonus=0.0):
        if _inside_any((s,e), known_spans): return
        out.append(dict(surface=surface, start_off=s, end_off=e,
                        kind_guess=None, context=None, confidence=0.5+bonus))
    for sent in doc.sents:
        # contiguous non-punct tokens only
        toks = [t for t in sent if not t.is_punct and not t.is_space]
        n = len(toks)
        for i in range(n):
            # Title-case token gates unigrams, allows Mr./Dr. (t.text keeps '.')
            if not toks[i].text[:1].isupper() and not toks[i].ent_type_:  # let NER-tagged through
                continue
            # unigram
            add(toks[i].text, toks[i].idx, toks[i].idx+len(toks[i].text), bonus=0.0)
            # bigram/trigram within sentence
            if i+1<n:
                s = toks[i].idx; e = toks[i+1].idx+len(toks[i+1].text)
                add(doc.text[s:e], s, e, bonus=0.1)
            if i+2<n:
                s = toks[i].idx; e = toks[i+2].idx+len(toks[i+2].text)
                add(doc.text[s:e], s, e, bonus=0.15)
    return out

def drop_overlapped_shorter(cands: list[dict]) -> list[dict]:
    """Keep the longest when two candidates overlap in the same region"""
    # sort by (length desc, earlier first)
    ordered = sorted(cands, key=lambda c: ((c["end_off"]-c["start_off"]), -c["start_off"]), reverse=True)
    kept = []
    used = []
    def overlaps(s,e): return any(not (e<=ps or s>=pe) for ps,pe in used)
    for c in ordered:
        s,e = c["start_off"], c["end_off"]
        if overlaps(s,e): 
            continue
        kept.append(c); used.append((s,e))
    # stable order by start offset
    return sorted(kept, key=lambda c: c["start_off"])

def build_candidates(text: str, known_phrases: set[str], super_lenient=False) -> list[dict]:
    doc = spacy_doc(text)
    known_spans = find_known_spans(text, known_phrases)

    cands = []
    # 1) spaCy ents (primary signal)
    cands += spacy_candidates(text)
    print("Spacy:", len(cands), cands)
    # 2) Heuristic (sentence-safe)
    if doc is not None:
        cands += heuristic_candidates_spacy(doc, known_spans)
    print("After heuristic:", len(cands), cands)
    # 3) Optional noun chunks
    if super_lenient and doc is not None:
        nc = [c for c in noun_chunk_candidates(text) if not _inside_any((c["start_off"], c["end_off"]), known_spans)]
        cands += nc
        print("After noun chunks:", len(cands))

    print("Candidates before known filter:", len(cands))
    # Drop anything whose surface is already a known phrase
    known_lower = set(known_phrases)
    cands = [c for c in cands if c["surface"].strip().lower() not in known_lower]
    print("Known:", len(known_phrases), "-> candidates after known filter:", len(cands))

    # Dedup
    seen, uniq = set(), []
    for c in cands:
        key = (c["surface"].strip().lower(), c["start_off"], c["end_off"])
        if key in seen: continue
        seen.add(key); uniq.append(c)

    # Keep longest non-overlapping spans
    return drop_overlapped_shorter(uniq)

def heuristic_new_entity_candidates(text: str, known_phrases: set[str]) -> list[dict]:
    if not text:
        return []
    tokens = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\b[\w'-]+\b", text)]
    # Build ngrams 1..3 around capitalized tokens
    cands = {}
    def add(surface, s, e, bonus=0.0):
        key = surface.lower().strip()
        if key in known_phrases:
            return
        if len(surface.split()) == 1 and surface.upper() in STOP_CAPS:
            return
        # confidence seed: length & bonus
        base = 0.45 + 0.15 * (len(surface.split()) - 1) + bonus
        c = cands.get(key)
        if not c:
            cands[key] = dict(surface=surface, start_off=s, end_off=e,
                              kind_guess=None, context=None, confidence=base)
        else:
            c["confidence"] = min(0.95, c["confidence"] + 0.05)

    # frequency map for unigrams
    caps_unigrams = Counter()

    for i, (tok, s, e) in enumerate(tokens):
        if not tok[0].isupper():
            continue
        # ignore sentence-initial
        if _sentence_initial(text, s):
            continue

        # unigram pass (defer adding until we see cues/frequency)
        caps_unigrams[tok] += 1

        # try bigram/trigram forward if they look title-cased
        # ensure following tokens start with capital or are of/and/of-like connectors
        def title_like(w): return bool(w and (w[0].isupper() or w.lower() in {"of","the","and","de","del","von"}))
        # bigram
        if i+1 < len(tokens):
            t2, s2, e2 = tokens[i+1]
            if title_like(t2):
                add(f"{tok} {t2}", s, e2, bonus=0.1)
        # trigram
        if i+2 < len(tokens):
            t2, s2, e2 = tokens[i+1]
            t3, s3, e3 = tokens[i+2]
            if title_like(t2) and title_like(t3):
                add(f"{tok} {t2} {t3}", s, e3, bonus=0.15)

        # unigram with cue: possessive
        if i+1 < len(tokens) and tokens[i+1][0] == "'s":
            add(tok, s, e, bonus=0.1)

    # finalize unigrams with frequency >= 2
    for (tok, count) in caps_unigrams.items():
        if count >= 2 and tok.upper() not in STOP_CAPS and tok.lower() not in known_phrases:
            # find first occurrence span
            for (w, s, e) in tokens:
                if w == tok:
                    add(tok, s, e, bonus=0.05 if count >= 3 else 0.0)
                    break

    return list(cands.values())

def enrich_kind_with_ner(candidates: list[dict], ner: list[tuple[str,str,int,int]]):
    # map by surface lower for a cheap join
    by_surface = {c["surface"].lower(): c for c in candidates}
    for surface, label, s, e in ner:
        key = surface.strip().lower()
        if key in by_surface:
            c = by_surface[key]
            c["kind_guess"] = c.get("kind_guess") or _KIND_FROM_NER.get(label)
            c["start_off"] = c.get("start_off") or s
            c["end_off"] = c.get("end_off") or e
            c["context"] = c.get("context") or _context_snippet(surface, s, e)
    return candidates

def _context_snippet(surface: str, s: int, e: int, radius: int = 60) -> str:
    # filler; the caller can pass real text to do a proper slice if needed
    return f"...{surface}..."
