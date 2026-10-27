"""Which shared pairs make it into data/, see "How pairs are verified" in the README."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field

from cronchords import matching

from .catalog import Catalog
from .files import Pair, SharedPair, dump_by_key

# Pairs that the matching rules don't accept need this many people to agree.
MIN_CORRECTION_VOTES = 2

# Best first.
REVIEWED = "reviewed"
CORRECTED = "corrected"
MATCHED = "matched"
RANKS = {REVIEWED: 0, CORRECTED: 1, MATCHED: 2}

DATA_FORMAT = 1


@dataclass
class Votes:
    """Who shared a pair: GitHub users or organizations, lowercased."""

    found: set[str] = field(default_factory=set)
    corrected: set[str] = field(default_factory=set)
    # It is in contrib/pairs.json.
    reviewed: bool = False

    @property
    def owners(self) -> set[str]:
        return self.found | self.corrected


@dataclass(frozen=True)
class Mapping:
    track: str
    video: str
    isrc: str | None
    source: str
    votes: int


def tally(shares: dict[str, Iterable[SharedPair]], reviewed: Iterable[Pair]) -> dict[Pair, Votes]:
    """The votes for each pair, from what every owner shared."""
    votes: dict[Pair, Votes] = {}
    for owner, shared in shares.items():
        for pair in shared:
            entry = votes.setdefault((pair.track, pair.video), Votes())
            (entry.corrected if pair.corrected else entry.found).add(owner)
    for pair in reviewed:
        votes.setdefault(pair, Votes()).reviewed = True
    return votes


def decide(votes: dict[Pair, Votes], catalog: Catalog, wrong: set[Pair]) -> list[Mapping]:
    """The pairs that pass. Those whose track or video wasn't looked up yet wait."""
    mappings = []
    for (track_id, video_id), vote in sorted(votes.items()):
        if (track_id, video_id) in wrong:
            continue
        track, video = catalog.track(track_id), catalog.video(video_id)
        if track is None or video is None:
            continue
        if vote.reviewed:
            source = REVIEWED
        elif len(vote.corrected) >= MIN_CORRECTION_VOTES and matching.same_title(track, video):
            source = CORRECTED
        elif vote.owners and matching.score(track, video) >= matching.MIN_SCORE:
            source = MATCHED
        else:
            continue
        mappings.append(Mapping(track_id, video_id, track.isrc, source, len(vote.owners)))
    return mappings


def shard(key: str) -> str:
    """The file a key's entries are in: the first two hex digits of its SHA-1."""
    return hashlib.sha1(key.encode()).hexdigest()[:2]


def data_files(mappings: list[Mapping]) -> dict[str, str]:
    """data/'s files by path: the videos of every ISRC, and the tracks of every video."""
    by_isrc: dict[str, list[Mapping]] = {}
    by_video: dict[str, list[Mapping]] = {}
    for mapping in mappings:
        if mapping.isrc:
            by_isrc.setdefault(mapping.isrc, []).append(mapping)
        by_video.setdefault(mapping.video, []).append(mapping)

    shards: dict[str, dict[str, list[dict[str, object]]]] = {}
    for folder, index, other in (("isrc", by_isrc, "video"), ("video", by_video, "track")):
        for key, entries in index.items():
            entries.sort(key=lambda m: (RANKS[m.source], -m.votes, getattr(m, other)))
            rows = []
            for m in entries:
                row: dict[str, object] = {"track": m.track, "source": m.source, "votes": m.votes}
                row |= {"video": m.video} if folder == "isrc" else {"isrc": m.isrc}
                rows.append(row)
            shards.setdefault(f"{folder}/{shard(key)}.json", {})[key] = rows

    files = {path: dump_by_key(entries) for path, entries in shards.items()}
    files["index.json"] = (
        json.dumps(
            {
                "version": DATA_FORMAT,
                "pairs": len(mappings),
                "isrcs": len(by_isrc),
                "videos": len(by_video),
            },
            indent=2,
        )
        + "\n"
    )
    return files
