"""Work out which release of a component is on a card, from the files themselves.

This works on cards built by hand too (no receipt): each candidate release is reduced to a
{path: sha256} fingerprint and compared with what is actually on the card.
"""
from dataclasses import dataclass
from pathlib import Path

from .fetch import sha256_file
from .install import install_file, install_zip

IDENTIFIED = 0.9  # share of a release's files that must match to call it that version
PARTIAL = 0.3


@dataclass(frozen=True)
class Detected:
    state: str  # missing | identified | partial | unknown
    tag: str = None
    score: float = 0.0
    diffs: tuple = ()  # files of the matched release that are missing or different on the card


def fingerprint(component, path, volatile=()):
    """{relative path: sha256} of everything the release would write, minus files hekate rewrites itself."""
    if component.kind == "zip":
        files = install_zip(path, Path("."), redirect=component.redirect, dry_run=True)
    else:
        files = install_file(path, Path("."), component.dest, dry_run=True)
    return {rel: h for rel, h in files.items() if rel not in volatile}


def detect(card, candidates):
    """candidates: [(tag, fingerprint)] newest first. Ties go to the newest release."""
    card = Path(card)
    cache = {}

    def card_hash(rel):
        if rel not in cache:
            p = card / rel
            cache[rel] = sha256_file(p) if p.is_file() else None
        return cache[rel]

    if not any(card_hash(rel) for _, fp in candidates for rel in fp):
        return Detected("missing")
    best_tag, best = None, -1.0
    for tag, fp in candidates:
        if not fp:
            continue
        score = sum(1 for rel, h in fp.items() if card_hash(rel) == h) / len(fp)
        if score > best:  # strict: an older release with the same score does not win
            best_tag, best = tag, score
    diffs = tuple(rel for rel, h in dict(candidates)[best_tag].items() if card_hash(rel) != h) if best_tag else ()
    if best >= IDENTIFIED:
        return Detected("identified", best_tag, best, diffs)
    if best >= PARTIAL:
        return Detected("partial", best_tag, best, diffs)
    return Detected("unknown", None, max(best, 0.0))
