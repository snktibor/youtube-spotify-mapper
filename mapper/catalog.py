"""What Spotify and YouTube say about the tracks and videos of the pairs.

Spotify only hands out one track per request to apps like this one, so what
was looked up is kept in state/ and only looked up again after RECHECK_AFTER.
Each run looks up at most so many tracks and videos, and the pairs that are
still waiting are verified in a later run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from cronchords.http import send
from cronchords.spotify import Track, normalize_isrc
from cronchords.youtube import Video, parse_duration

from .files import dump_by_key

log = logging.getLogger(__name__)

RECHECK_AFTER = timedelta(days=90)
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"
# videos.list takes up to 50 ids, for 1 quota unit.
VIDEO_BATCH = 50


class CatalogError(Exception):
    pass


class LimitReached(CatalogError):
    """Spotify's rate limit or YouTube's quota ran out, the rest waits for the next run."""


class Catalog:
    """Tracks and videos by id, as last looked up. {"gone": true} if they don't exist."""

    def __init__(self, tracks: dict[str, Any], videos: dict[str, Any], today: date):
        self.tracks = tracks
        self.videos = videos
        self.today = today

    @classmethod
    def load(cls, folder: Path, today: date) -> Catalog:
        return cls(_read(folder / "tracks.json"), _read(folder / "videos.json"), today)

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "tracks.json").write_text(dump_by_key(self.tracks))
        (folder / "videos.json").write_text(dump_by_key(self.videos))

    def track(self, track_id: str) -> Track | None:
        info = self.tracks.get(track_id)
        if not info or info.get("gone"):
            return None
        return Track(
            track_id, info["isrc"], info["name"], tuple(info["artists"]), info["duration_ms"]
        )

    def video(self, video_id: str) -> Video | None:
        info = self.videos.get(video_id)
        if not info or info.get("gone"):
            return None
        return Video(video_id, info["title"], info["channel"], info["duration_s"])

    def due(self, known: dict[str, Any], ids: Iterable[str]) -> list[str]:
        """The ids to look up, the ones never looked up first, then the longest ago."""
        never = sorted(i for i in set(ids) if i not in known)
        stale = sorted(
            (known[i]["checked"], i)
            for i in set(ids)
            if i in known and self.today - date.fromisoformat(known[i]["checked"]) >= RECHECK_AFTER
        )
        return never + [i for _, i in stale]

    def update_tracks(self, spotify: SpotifyCatalog, track_ids: Iterable[str], limit: int) -> int:
        looked_up = 0
        for track_id in self.due(self.tracks, track_ids)[:limit]:
            try:
                info = spotify.track(track_id)
            except LimitReached as exc:
                log.warning("%s", exc)
                break
            self.tracks[track_id] = (info or {"gone": True}) | {"checked": self.today.isoformat()}
            looked_up += 1
        return looked_up

    def update_videos(self, youtube: YouTubeCatalog, video_ids: Iterable[str], limit: int) -> int:
        due = self.due(self.videos, video_ids)[:limit]
        looked_up = 0
        for start in range(0, len(due), VIDEO_BATCH):
            batch = due[start : start + VIDEO_BATCH]
            try:
                found = youtube.videos(batch)
            except LimitReached as exc:
                log.warning("%s", exc)
                break
            for video_id in batch:
                info = found.get(video_id) or {"gone": True}
                self.videos[video_id] = info | {"checked": self.today.isoformat()}
            looked_up += len(batch)
        return looked_up

    def keep_only(self, track_ids: set[str], video_ids: set[str]) -> None:
        """Forget what no pair refers to anymore."""
        self.tracks = {i: info for i, info in self.tracks.items() if i in track_ids}
        self.videos = {i: info for i, info in self.videos.items() if i in video_ids}


class SpotifyCatalog:
    def __init__(self, http: httpx.Client, client_id: str, client_secret: str):
        self._http = http
        response = send(
            http,
            "POST",
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
        )
        if not response.is_success:
            raise CatalogError(f"Spotify didn't hand out a token ({response.status_code})")
        self._headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    def track(self, track_id: str) -> dict[str, Any] | None:
        """The track, or None if Spotify doesn't have it."""
        response = send(
            self._http, "GET", f"{SPOTIFY_API_URL}/tracks/{track_id}", headers=self._headers
        )
        if response.status_code in (400, 404):
            return None
        if response.status_code == 429:
            raise LimitReached("Spotify is limiting requests, the rest waits for the next run")
        if not response.is_success:
            raise CatalogError(f"Spotify API error {response.status_code} for track {track_id}")
        item = response.json()
        return {
            "name": item.get("name") or "",
            "artists": [a["name"] for a in item.get("artists") or [] if a.get("name")],
            "duration_ms": item.get("duration_ms") or 0,
            "isrc": normalize_isrc((item.get("external_ids") or {}).get("isrc")) or None,
        }


class YouTubeCatalog:
    def __init__(self, http: httpx.Client, api_key: str):
        self._http = http
        self._key = api_key

    def videos(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        """The videos that exist and are public, by id."""
        response = send(
            self._http,
            "GET",
            f"{YOUTUBE_API_URL}/videos",
            params={
                "part": "snippet,contentDetails,status",
                "id": ",".join(video_ids),
                "key": self._key,
                "fields": "items(id,snippet(title,channelTitle),contentDetails/duration,"
                "status/privacyStatus)",
            },
        )
        if response.status_code == 403 and "quota" in response.text.lower():
            raise LimitReached("The YouTube API quota is used up, the rest waits for the next run")
        if not response.is_success:
            raise CatalogError(f"YouTube API error {response.status_code}: {response.text[:200]}")
        found = {}
        for item in response.json().get("items", []):
            if item.get("status", {}).get("privacyStatus") == "private":
                continue
            found[item["id"]] = {
                "title": item["snippet"].get("title") or "",
                "channel": item["snippet"].get("channelTitle") or "",
                "duration_s": parse_duration(item["contentDetails"].get("duration", "")),
            }
        return found


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}
