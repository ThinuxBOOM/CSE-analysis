"""
§6: deduplication and syndication, by a true key with no similarity threshold (revision 2, M6).

The story key of an article is the SHA-256 of its normalised title tokens (sorted, after a fixed stop list) and its
issuer set. Articles with the same key within the snapshot's window are one story. **Story ids are derived per
snapshot** from the admitted articles only (the key and the earliest admitted member's id), so an article recorded
after T can never change the grouping or the ids at T (NL-12): admission happens before this function is called.
The earliest admitted article sets the story's instant (its age, §7); the story's score is the mean of its admitted
articles' scores; the number of distinct sources is kept.
"""
import hashlib
import json
from dataclasses import dataclass, field

from .process import tokens

STOP = frozenset({"the", "a", "an", "of", "in", "on", "for", "to", "and", "is", "are", "at", "by", "with", "as",
                  "its", "from", "s"})


def story_key(title, issuer_ids):
    toks = sorted(t for t in tokens(title) if t not in STOP)
    return hashlib.sha256(json.dumps([toks, sorted(set(issuer_ids))]).encode()).hexdigest()


@dataclass(frozen=True)
class Member:
    """An admitted article version as grouping needs it."""
    article_id: str                     # stable: source and reference (not the version)
    source_id: str
    title: str
    issuer_ids: tuple
    instant: object                     # its availability instant for age (§7)


@dataclass
class Story:
    story_id: str
    key: str
    members: list = field(default_factory=list)

    @property
    def instant(self):
        return min(m.instant for m in self.members)

    @property
    def sources(self):
        return sorted({m.source_id for m in self.members})


def stories(members):
    """Group admitted members into stories; ids from the key and the earliest admitted member (ties: article id)."""
    groups = {}
    for m in members:
        groups.setdefault(story_key(m.title, m.issuer_ids), []).append(m)
    out = []
    for key, ms in sorted(groups.items()):
        ms.sort(key=lambda m: (m.instant, m.article_id))
        out.append(Story(f"{key[:16]}:{ms[0].article_id}", key, ms))
    return sorted(out, key=lambda s: (s.instant, s.story_id))
