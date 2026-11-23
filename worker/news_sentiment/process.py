"""
§5: the v1 processors, pure and deterministic. One call processes one article version against the aliases admitted
to the caller (and the configuration); its result records the processor versions, the configuration id and the ids of
the aliases it used, with their set hash (§5.5).

Tokens: lower case; a full ticker (`abc.n0000`) and `s&p` are single tokens; company-form suffixes are normalised by a
fixed table (`limited` -> `ltd`, `public limited company` -> `plc`, ...). Matching is exact on normalised tokens with
word boundaries; no fuzzy matching (a wrong entity is worse than a missed one, P6-D5). Overlapping matches: the
longest wins. A string that maps to two or more issuers yields all of them as `ambiguous` candidates and contributes to
NONE of their scores.

`ns.entity.1`     issuers mentioned (count, positions: title, first_paragraph, body; the aliases used), ambiguous
                  mentions, `market_wide` when a fixed market term appears
`ns.relevance.1`  per (article, issuer) in [0, 1]: where mentioned, the issuer's share of the entity mentions, an
                  attached event
`ns.event.1`      a fixed taxonomy matched by rule phrases within a window of an entity (or market) mention; the rule
                  id always, an excerpt only under full_text
`ns.sentiment.1`  a lexicon score in [-1, 1] per issuer from the signed terms in a window around its mentions, with
                  negation and intensifiers, normalised by the number of scored terms; `unscored` below the minimum
                  (never `neutral`); the market-wide score the same way around market terms
Articles not in English are `language_unsupported` and contribute to no score (§5.1).
"""
import re
from dataclasses import dataclass, field

from . import aliases as al
from . import config as cf

_TOKEN = re.compile(r"[a-z0-9]+\.[a-z][0-9]{4}|s&p|[0-9]+(?:\.[0-9]+)+|[a-z0-9]+|[.,;:!?()]")
BOUNDARY = "|"                         # a clause boundary kept in the token stream (NS-2..5 audit, Major 3)
_SUFFIX = {"limited": "ltd", "private": "pvt", "company": "co", "corporation": "corp", "incorporated": "inc"}
_SEQUENCES = ((("public", "ltd", "co"), ("plc",)), (("pvt", "ltd"), ("pvt", "ltd")))
MARKET_TERMS = (("colombo", "stock", "exchange"), ("cse",), ("aspi",), ("all", "share", "price", "index"),
                ("s&p", "sl20"), ("sl20",), ("central", "bank"), ("cbsl",), ("rupee",), ("lkr",))
EVENT_RULES = {
    "earnings": ("profit", "earnings", "net income", "revenue", "results", "quarterly results"),
    "dividend": ("dividend", "dividends", "interim dividend", "final dividend"),
    "corporate_action": ("rights issue", "bonus issue", "share split", "subdivision", "stock split"),
    "management_change": ("appointed", "resigned", "chief executive", "ceo", "chairman", "managing director"),
    "regulatory": ("regulator", "securities and exchange commission", "licence", "license", "compliance"),
    "legal": ("lawsuit", "court", "litigation", "fraud", "penalty"),
    "rating": ("rating", "ratings", "outlook", "credit rating"),
    "merger_acquisition": ("acquisition", "acquire", "acquires", "merger", "takeover", "stake"),
    "capital_raise": ("debenture", "debentures", "bond issue", "ipo", "private placement", "capital raise"),
    "operations": ("plant", "factory", "production", "contract", "launch", "launched"),
    "macro_policy": ("interest rate", "policy rate", "inflation", "budget", "tax", "exchange rate"),
}


def tokens(text):
    raw = _TOKEN.findall((text or "").lower())
    out = [BOUNDARY if t in ".,;:!?()" else _SUFFIX.get(t, t) for t in raw]
    for seq, repl in _SEQUENCES:
        i, res = 0, []
        while i < len(out):
            if tuple(out[i:i + len(seq)]) == seq:
                res.extend(repl)
                i += len(seq)
            else:
                res.append(out[i])
                i += 1
        out = res
    return out


def _phrase(text):
    return tuple(t for t in tokens(text) if t != BOUNDARY)


@dataclass
class Segment:
    """One scanned text: the title, the first paragraph, or the rest of the body."""
    position: str
    toks: list


def segments(title, text):
    paras = [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return [Segment("title", tokens(title)), Segment("first_paragraph", tokens(paras[0]) if paras else []),
            Segment("body", tokens("\n\n".join(paras[1:])))]


def _find(toks, phrase):
    n = len(phrase)
    return [i for i in range(len(toks) - n + 1) if tuple(toks[i:i + n]) == phrase] if n else []


@dataclass
class Mention:
    segment: int
    start: int
    end: int
    phrase: tuple
    issuers: tuple                   # candidates; ambiguous when more than one
    alias_ids: tuple


def mentions(segs, admitted_aliases):
    """Non-overlapping alias matches, longest first."""
    by_phrase = {}
    for a in admitted_aliases:
        p = _phrase(a.text)
        if p:
            by_phrase.setdefault(p, []).append(a)
    found = []
    for si, seg in enumerate(segs):
        cands = []
        for p, als in by_phrase.items():
            for i in _find(seg.toks, p):
                cands.append(Mention(si, i, i + len(p), p, tuple(sorted({a.issuer_id for a in als})),
                                     tuple(sorted(a.alias_id for a in als))))
        cands.sort(key=lambda m: (-(m.end - m.start), m.start))
        taken = set()
        for m in cands:
            span = set(range(m.start, m.end))
            if span & taken:
                continue
            taken |= span
            found.append(m)
    return sorted(found, key=lambda m: (m.segment, m.start))


def _windows(segs, spans, width, exclude=()):
    """{segment: set of token positions} within `width` of any span; the spans themselves, and every other matched
    alias span (`exclude`: a term inside another issuer's name is not sentiment), left out."""
    out = {}
    for si, s, e in spans:
        n = len(segs[si].toks)
        out.setdefault(si, set()).update(range(max(0, s - width), min(n, e + width)))
    for si, s, e in list(spans) + list(exclude):
        if si in out:
            out[si] -= set(range(s, e))
    return out


def _events(segs, spans, cfg, full_text):
    win = _windows(segs, spans, cfg.event_window_tokens)
    found = {}
    for etype, phrases in EVENT_RULES.items():
        for k, ph in enumerate(phrases):
            p = _phrase(ph)
            for si, pos in win.items():
                for i in _find(segs[si].toks, p):
                    if i in pos:                                # the phrase starts inside the window
                        rule = f"{etype}.{k}"
                        if (etype, rule) not in found:
                            excerpt = " ".join(segs[si].toks[max(0, i - 5):i + len(p) + 5]) if full_text else None
                            found[(etype, rule)] = {"type": etype, "rule": rule, "excerpt": excerpt}
    return sorted(found.values(), key=lambda e: (e["type"], e["rule"]))


def _negated(toks, i, reach, negators, scored):
    """A negator within `reach` tokens before i, the scope ending at a clause boundary or at an earlier scored term
    (Major 3: "no loss, strong growth" does not negate "strong")."""
    for j in range(i - 1, max(-1, i - 1 - reach), -1):
        t = toks[j]
        if t == BOUNDARY or t in scored:
            return False
        if t in negators:
            return True
    return False


def _sentiment(segs, spans, lex, cfg, exclude=()):
    pos_terms, neg_terms = set(lex["positive"]), set(lex["negative"])
    negators, intens = set(lex["negators"]), dict(lex["intensifiers"])
    win = _windows(segs, spans, cfg.sentiment_window_tokens, exclude)
    total, n = 0.0, 0
    for si, positions in sorted(win.items()):
        toks = segs[si].toks
        for i in sorted(positions):
            t = toks[i]
            pol = 1 if t in pos_terms else -1 if t in neg_terms else 0
            if not pol:
                continue
            mult = intens.get(toks[i - 1], 1.0) if i >= 1 else 1.0
            if _negated(toks, i, cfg.negation_reach_tokens, negators, pos_terms | neg_terms):
                pol = -pol
            total += pol * mult
            n += 1
    if n < cfg.min_scored_terms:
        return None, "unscored", n
    score = max(-1.0, min(1.0, total / n))
    label = "positive" if score >= cfg.positive_threshold else "negative" if score <= cfg.negative_threshold \
        else "neutral"
    return round(score, 12), label, n


@dataclass
class ArticleResult:
    processors: tuple
    configuration_id: str
    state: str                       # processed | language_unsupported
    issuers: dict = field(default_factory=dict)     # issuer_id -> {mentions, positions, relevance, events, ...}
    ambiguous: list = field(default_factory=list)   # [{"text", "candidates"}]
    market: dict = field(default_factory=dict)      # {"wide", "events", "sentiment", "label", "scored_terms"}
    alias_ids: tuple = ()               # the aliases its matches used
    alias_set_hash: str = ""            # the hash of its alias CONTEXT: every alias admitted when computed (Major 1)
    context_alias_ids: tuple = ()       # that context


def process(title, text, language, admitted_aliases, cfg=None, lexicon=None, full_text=False):
    """The v1 result of one article version (§5). `admitted_aliases` are those admitted to the caller (§5.2, §5.5).
    The result records the ones it used AND its whole alias context (every alias admitted to it, ends applied): a
    stored result is reused at T only when that context equals the one admitted at T (NS-2..5 audit, Major 1).
    `lexicon`: (lexicon, sha256) or None for the configuration's own; its hash must be the configuration's."""
    cfg = cfg or cf.Config()
    lex, lex_sha = lexicon or cf.load_lexicon()
    if lex_sha != cfg.lexicon_sha256:
        raise ValueError("the lexicon is not the configuration's (its SHA-256 differs)")
    procs = (cf.ENTITY, cf.RELEVANCE, cf.EVENT, cf.SENTIMENT)
    cid = cfg.configuration_id()
    context = tuple(sorted(a.alias_id for a in admitted_aliases))
    if language != "en":
        return ArticleResult(procs, cid, "language_unsupported", alias_set_hash=al.alias_set_hash(context),
                             context_alias_ids=context)
    segs = segments(title, text)
    ms = mentions(segs, admitted_aliases)
    used = sorted({a for m in ms for a in m.alias_ids})
    clear = [m for m in ms if len(m.issuers) == 1]
    total = len(clear)
    res = ArticleResult(procs, cid, "processed", alias_ids=tuple(used), alias_set_hash=al.alias_set_hash(context),
                        context_alias_ids=context)
    all_spans = [(m.segment, m.start, m.end) for m in ms]
    res.ambiguous = [{"text": " ".join(m.phrase), "candidates": list(m.issuers)} for m in ms if len(m.issuers) > 1]
    for issuer in sorted({m.issuers[0] for m in clear}):
        mine = [m for m in clear if m.issuers[0] == issuer]
        spans = [(m.segment, m.start, m.end) for m in mine]
        positions = sorted({segs[m.segment].position for m in mine})
        events = _events(segs, spans, cfg, full_text)
        rel = (cfg.w_title * ("title" in positions) + cfg.w_first_paragraph * ("first_paragraph" in positions)
               + cfg.w_body * ("body" in positions) + cfg.w_share * (len(mine) / total)
               + cfg.w_event * bool(events))
        score, label, n = _sentiment(segs, spans, lex, cfg, all_spans)
        res.issuers[issuer] = {"mentions": len(mine), "positions": positions, "relevance": round(min(1.0, rel), 12),
                               "events": events, "sentiment": score, "label": label, "scored_terms": n,
                               "alias_ids": sorted({a for m in mine for a in m.alias_ids})}
    mkt = [(si, i, i + len(p)) for p in MARKET_TERMS for si, seg in enumerate(segs) for i in _find(seg.toks, p)]
    if mkt:
        score, label, n = _sentiment(segs, mkt, lex, cfg, all_spans)
        res.market = {"wide": True, "events": [e for e in _events(segs, mkt, cfg, full_text)
                                               if e["type"] == "macro_policy"],
                      "sentiment": score, "label": label, "scored_terms": n}
    else:
        res.market = {"wide": False, "events": [], "sentiment": None, "label": "unscored", "scored_terms": 0}
    return res
