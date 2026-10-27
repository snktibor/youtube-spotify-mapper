"""python -m mapper check | update

check validates the files people change by pull request. update collects the
shared pairs, verifies them and writes data/ and state/, see the README.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from .catalog import Catalog, CatalogError, SpotifyCatalog, YouTubeCatalog
from .files import (
    FileError,
    SharedPair,
    dump_by_key,
    parse_shared,
    read_blocklist,
    read_contrib,
    read_sources,
)
from .github import GitHub, GitHubError
from .rules import Mapping, data_files, decide, tally

log = logging.getLogger("mapper")

ROOT = Path(__file__).resolve().parent.parent
# Its forks share what cronchords matched.
UPSTREAM = "snktibor/cronchords"

# New accounts don't count, so that votes can't be made up on the spot.
MIN_ACCOUNT_AGE = timedelta(days=90)
MAX_PAIRS_PER_SOURCE = 20_000
# Per run. Spotify takes a request per track, YouTube one per 50 videos.
TRACK_LOOKUPS = 2_000
VIDEO_LOOKUPS = 50_000


@dataclass
class Report:
    sharing: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    not_sharing: int = 0
    shared_pairs: int = 0
    waiting: int = 0
    mappings: list[Mapping] = field(default_factory=list)

    def lines(self) -> list[str]:
        sources = {m.source for m in self.mappings}
        counts = ", ".join(
            f"{sum(m.source == s for m in self.mappings)} {s}" for s in sorted(sources)
        )
        lines = [
            f"Sources sharing pairs: {len(self.sharing)} ({', '.join(self.sharing) or 'none'})",
            f"Forks and sources without shared pairs: {self.not_sharing}",
            *(f"Skipped {line}" for line in self.skipped),
            f"Pairs shared: {self.shared_pairs}",
            f"Pairs published: {len(self.mappings)}" + (f" ({counts})" if counts else ""),
            f"Pairs waiting for their track or video to be looked up: {self.waiting}",
        ]
        return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mapper")
    parser.add_argument("command", choices=["check", "update"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "check":
        return check(ROOT)

    missing = [
        name
        for name in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "YOUTUBE_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        log.error("Missing environment variables: %s", ", ".join(missing))
        return 1
    try:
        with httpx.Client(timeout=30) as http:
            report = update(
                ROOT,
                GitHub(http, os.environ.get("GITHUB_TOKEN")),
                SpotifyCatalog(
                    http, os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]
                ),
                YouTubeCatalog(http, os.environ["YOUTUBE_API_KEY"]),
                datetime.now(UTC).date(),
            )
    except (FileError, GitHubError, CatalogError) as exc:
        log.error("%s", exc)
        return 1
    for line in report.lines():
        log.info("%s", line)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as file:
            file.write("".join(f"- {line}\n" for line in report.lines()))
    return 0


def check(root: Path) -> int:
    """Whether the files people change by pull request are all right."""
    try:
        read_sources(root / "sources.txt")
        read_blocklist(root / "blocklist.txt")
        both = read_contrib(root / "contrib/pairs.json") & read_contrib(root / "contrib/wrong.json")
    except FileError as exc:
        log.error("%s", exc)
        return 1
    if both:
        log.error("These pairs are in contrib/pairs.json and contrib/wrong.json: %s", sorted(both))
        return 1
    log.info("All files are fine")
    return 0


def update(
    root: Path, github: GitHub, spotify: SpotifyCatalog, youtube: YouTubeCatalog, today: date
) -> Report:
    report = Report()
    shares = collect(root, github, today, report)
    report.shared_pairs = len({(p.track, p.video) for pairs in shares.values() for p in pairs})
    wrong = read_contrib(root / "contrib/wrong.json")
    votes = tally(shares, read_contrib(root / "contrib/pairs.json"))
    votes = {pair: vote for pair, vote in votes.items() if pair not in wrong}

    track_ids = {track_id for track_id, _ in votes}
    video_ids = {video_id for _, video_id in votes}
    catalog = Catalog.load(root / "state", today)
    catalog.update_videos(youtube, video_ids, VIDEO_LOOKUPS)
    catalog.update_tracks(spotify, track_ids, TRACK_LOOKUPS)
    catalog.keep_only(track_ids, video_ids)
    catalog.save(root / "state")

    report.mappings = decide(votes, catalog, wrong)
    report.waiting = sum(
        track_id not in catalog.tracks or video_id not in catalog.videos
        for track_id, video_id in votes
    )
    write_data(root / "data", data_files(report.mappings))
    return report


def collect(root: Path, github: GitHub, today: date, report: Report) -> dict[str, list[SharedPair]]:
    """What each owner shared, by lowercased GitHub user name."""
    blocked = read_blocklist(root / "blocklist.txt")
    owners_path = root / "state/owners.json"
    joined: dict[str, str] = json.loads(owners_path.read_text()) if owners_path.exists() else {}
    shares: dict[str, list[SharedPair]] = {}
    for source in [*github.forks(UPSTREAM), *read_sources(root / "sources.txt")]:
        owner = source.owner.lower()
        if owner in blocked:
            continue
        try:
            content = github.shared(source)
        except GitHubError as exc:
            report.skipped.append(f"{source.name}: {exc}")
            continue
        if content is None:
            report.not_sharing += 1
            continue
        if owner not in joined:
            joined[owner] = github.created_at(source.owner).date().isoformat()
        if today - date.fromisoformat(joined[owner]) < MIN_ACCOUNT_AGE:
            report.skipped.append(f"{source.name}: the account is younger than 90 days")
            continue
        try:
            pairs = parse_shared(content)
        except FileError as exc:
            report.skipped.append(f"{source.name}: {exc}")
            continue
        if len(pairs) > MAX_PAIRS_PER_SOURCE:
            report.skipped.append(f"{source.name}: more than {MAX_PAIRS_PER_SOURCE} pairs")
            continue
        shares.setdefault(owner, []).extend(pairs)
        report.sharing.append(source.name)
    owners_path.parent.mkdir(parents=True, exist_ok=True)
    owners_path.write_text(dump_by_key(joined))
    return shares


def write_data(folder: Path, files: dict[str, str]) -> None:
    """Replace data/'s generated files, leaving the license alone."""
    for old in [*folder.glob("isrc/*.json"), *folder.glob("video/*.json")]:
        if str(old.relative_to(folder)) not in files:
            old.unlink()
    for path, content in files.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


if __name__ == "__main__":
    sys.exit(main())
