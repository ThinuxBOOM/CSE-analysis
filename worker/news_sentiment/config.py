"""
The NS configuration (design §11, `ns_configurations`): processor versions, the lexicon and its hash, the taxonomy,
weights, half-lives and policies, as one canonical JSON document with a SHA-256 id. Every constant here is
`provisional` (§14): measurable only on real news. A change of any of them is a new configuration; a change of rules
or lexicon is also a new processor version.
"""
import hashlib
import json
import os
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
LEXICON_PATH = os.path.join(HERE, "lexicon_v1.json")

ENTITY, RELEVANCE, EVENT, SENTIMENT, DECAY = "ns.entity.1", "ns.relevance.1", "ns.event.1", "ns.sentiment.1", \
    "ns.decay.1"

EVENT_TYPES = ("earnings", "dividend", "corporate_action", "management_change", "regulatory", "legal", "rating",
               "merger_acquisition", "capital_raise", "operations", "macro_policy", "other")


def load_lexicon(path=LEXICON_PATH):
    with open(path, "rb") as fh:
        data = fh.read()
    lex = json.loads(data.decode("utf-8"))
    return lex, hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Config:
    # relevance (§5.3)
    w_title: float = 0.5
    w_first_paragraph: float = 0.25
    w_body: float = 0.1
    w_share: float = 0.3
    w_event: float = 0.2
    # events (§5.4) and sentiment (§5.5)
    event_window_tokens: int = 25
    sentiment_window_tokens: int = 20
    negation_reach_tokens: int = 3
    min_scored_terms: int = 2
    positive_threshold: float = 0.2
    negative_threshold: float = -0.2
    # decay (§7): half-lives in sessions per event type; `other` also for a story with no event
    half_lives: tuple = (("earnings", 5), ("dividend", 5), ("corporate_action", 10), ("management_change", 5),
                         ("regulatory", 10), ("legal", 10), ("rating", 10), ("merger_acquisition", 10),
                         ("capital_raise", 10), ("operations", 3), ("macro_policy", 5), ("other", 2))
    max_age_sessions: int = 21                 # at least 21 (Minor 2)
    # snapshot policies (§8)
    conflicting_share: float = 0.5             # ambiguous mentions above this share of an issuer's: conflicting
    unlabelled_share: float = 0.5              # unsupported / without a result / mixed versions above it: unlabelled
    stale_sessions: int = 10                   # newest story older than this: stale
    lexicon_sha256: str = field(default_factory=lambda: load_lexicon()[1])

    def __post_init__(self):
        if self.max_age_sessions < 21:
            raise ValueError("max_age_sessions is at least 21 (the 21-session counts must be complete)")
        if set(dict(self.half_lives)) != set(EVENT_TYPES) or any(h <= 0 for _e, h in self.half_lives):
            raise ValueError("every event type has a positive half-life")

    def half_life(self, event_type):
        return dict(self.half_lives)[event_type]

    def body(self):
        return {"processors": [ENTITY, RELEVANCE, EVENT, SENTIMENT, DECAY], "event_types": list(EVENT_TYPES),
                **{k: (list(map(list, v)) if k == "half_lives" else v) for k, v in self.__dict__.items()}}

    def configuration_id(self):
        js = json.dumps(self.body(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(js.encode()).hexdigest()
