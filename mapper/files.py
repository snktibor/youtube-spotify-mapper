"""The files people change by pull request, and the pairs.json files sources share."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

SPOTIFY_ID = re.compile(r"[0-9A-Za-z]{22}")
VIDEO_ID = re.compile(r"[0-9A-Za-z_-]{11}")
GITHUB_NAME = re.compile(r"[0-9A-Za-z](?:[0-9A-Za-z._-]*[0-9A-Za-z_-])?")

# Where cronchords puts the matches it shares.
DEFAULT_BRANCH = "cronchords-export"
DEFAULT_PATH = "pairs.json"
SHARED_FORMAT = 1

# A Spotify track id and a YouTube video id of the same song.
Pair = tuple[str, str]


class FileError(Exception):
    pass


@dataclass(frozen=True)
class Source:
    """A GitHub repository that shares a pairs.json."""

    owner: str
    repo: str
    branch: str = DEFAULT_BRANCH
    path: str = DEFAULT_PATH

    @property
    def name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://raw.githubusercontent.com/{self.name}/{self.branch}/{self.path}"


@dataclass(frozen=True)
class SharedPair:
    track: str
    video: str
    # A person picked it, rather than a search.
    corrected: bool = False


def read_sources(path: Path) -> list[Source]:
    """sources.txt: "owner/repo [branch [path]]" per line, # starts a comment."""
    sources = []
    for number, words in _lines(path):
        if len(words) > 3 or words[0].count("/") != 1:
            raise FileError(f"{path}:{number}: expected owner/repo [branch [path]]")
        owner, repo = words[0].split("/")
        if not GITHUB_NAME.fullmatch(owner) or not GITHUB_NAME.fullmatch(repo):
            raise FileError(f"{path}:{number}: {words[0]} is not a GitHub repository name")
        sources.append(Source(owner, repo, *words[1:]))
    return sources


def read_blocklist(path: Path) -> set[str]:
    """blocklist.txt: a GitHub user or organization per line, # starts a comment."""
    blocked = set()
    for number, words in _lines(path):
        if len(words) != 1 or not GITHUB_NAME.fullmatch(words[0]):
            raise FileError(f"{path}:{number}: expected a single GitHub user name")
        blocked.add(words[0].lower())
    return blocked


def read_contrib(path: Path) -> set[Pair]:
    """contrib/*.json: {"pairs": [{"track": ..., "video": ..., "note": ...}]}."""
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise FileError(f"{path}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("pairs"), list):
        raise FileError(f'{path}: expected {{"pairs": [...]}}')
    pairs: set[Pair] = set()
    for number, entry in enumerate(document["pairs"], 1):
        pair = _pair(entry)
        if pair is None or not set(entry) <= {"track", "video", "note"}:
            raise FileError(
                f"{path}: pair {number} needs a Spotify track id as track and a YouTube "
                "video id as video, and may have a note, nothing else"
            )
        if pair in pairs:
            raise FileError(f"{path}: pair {number} is there twice")
        pairs.add(pair)
    return pairs


def parse_shared(content: bytes) -> list[SharedPair]:
    """A source's pairs.json. Entries that don't make sense are left out."""
    try:
        document = json.loads(content)
    except ValueError as exc:
        raise FileError(f"not JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("pairs"), list):
        raise FileError('expected {"version": 1, "pairs": [...]}')
    if document.get("version") != SHARED_FORMAT:
        raise FileError(f"unknown version {document.get('version')!r}")
    shared = []
    for entry in document["pairs"]:
        pair = _pair(entry)
        if pair:
            shared.append(SharedPair(*pair, corrected=entry.get("source") == "correction"))
    return shared


def dump_by_key(entries: dict[str, object]) -> str:
    """JSON with one key per line, so that changes show up line by line in the history."""
    lines = [f"{json.dumps(k)}:{json.dumps(v, sort_keys=True)}" for k, v in sorted(entries.items())]
    return "{\n" + ",\n".join(lines) + "\n}\n" if lines else "{}\n"


def _pair(entry: object) -> Pair | None:
    if not isinstance(entry, dict):
        return None
    track, video = entry.get("track"), entry.get("video")
    if (
        isinstance(track, str)
        and isinstance(video, str)
        and SPOTIFY_ID.fullmatch(track)
        and VIDEO_ID.fullmatch(video)
    ):
        return track, video
    return None


def _lines(path: Path) -> list[tuple[int, list[str]]]:
    try:
        text = path.read_text()
    except OSError as exc:
        raise FileError(f"{path}: {exc}") from exc
    lines = []
    for number, line in enumerate(text.splitlines(), 1):
        words = line.split("#", 1)[0].split()
        if words:
            lines.append((number, words))
    return lines
