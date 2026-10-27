"""GitHub, Spotify and YouTube with a few songs, behind an httpx.MockTransport."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

# Real ids, so that they look the way the files expect them.
GET_LUCKY = "69kOkLUCkxIZYexIgSG8rq"
GET_LUCKY_VIDEO = "5NV6Rdv1a3I"
GET_LUCKY_COVER_VIDEO = "Hxq6Q1JmJ1s"
DREAMS = "0ofHAoxe9vBkTCp2UQIavz"
DREAMS_VIDEO = "mrZRURcb1cM"
DREAMS_LIVE_VIDEO = "Y3ywicffOj4"
GONE_VIDEO = "aaaaaaaaaaa"


def track(name: str, artist: str, isrc: str | None, duration_ms: int = 248_000) -> dict[str, Any]:
    return {
        "name": name,
        "artists": [{"name": artist}],
        "duration_ms": duration_ms,
        "external_ids": {"isrc": isrc} if isrc else {},
    }


def video(title: str, channel: str, duration: str = "PT4M8S") -> dict[str, Any]:
    return {
        "snippet": {"title": title, "channelTitle": channel},
        "contentDetails": {"duration": duration},
        "status": {"privacyStatus": "public"},
    }


def shared(*pairs: tuple[str, str, str]) -> bytes:
    """A pairs.json the way cronchords writes it, from (track, video, source) triples."""
    entries = [{"track": t, "video": v, "source": s} for t, v, s in pairs]
    return json.dumps({"version": 1, "pairs": entries}).encode()


@dataclass
class FakeCloud:
    # Repository -> its forks, as (owner, name) pairs
    forks: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    # Login -> when the account was created
    users: dict[str, str] = field(default_factory=dict)
    # Raw URL -> content
    files: dict[str, bytes] = field(default_factory=dict)
    tracks: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            GET_LUCKY: track("Get Lucky (feat. Pharrell Williams)", "Daft Punk", "USQX91300108"),
            DREAMS: track("Dreams", "Fleetwood Mac", "USWB10400047", 257_000),
        }
    )
    videos: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            GET_LUCKY_VIDEO: video("Get Lucky (feat. Pharrell Williams)", "Daft Punk - Topic"),
            GET_LUCKY_COVER_VIDEO: video("Get Lucky - Daft Punk (acoustic cover)", "Some Guy"),
            DREAMS_VIDEO: video("Fleetwood Mac - Dreams (Official Music Video)", "Fleetwood Mac"),
            DREAMS_LIVE_VIDEO: video("Dreams - Fleetwood Mac (Live 1977)", "Fleetwood Mac"),
        }
    )
    spotify_rate_limited: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def share(self, owner: str, content: bytes, joined: str = "2015-01-01T00:00:00Z") -> None:
        """Make owner/cronchords a fork that shares content."""
        self.forks.setdefault("snktibor/cronchords", []).append((owner, "cronchords"))
        self.users[owner] = joined
        self.files[
            f"https://raw.githubusercontent.com/{owner}/cronchords/cronchords-export/pairs.json"
        ] = content

    def calls(self, host: str, path_prefix: str = "") -> list[httpx.Request]:
        return [
            r for r in self.requests if r.url.host == host and r.url.path.startswith(path_prefix)
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "api.github.com":
            if path.endswith("/forks"):
                repository = path.removeprefix("/repos/").removesuffix("/forks")
                page = int(request.url.params["page"])
                forks = self.forks.get(repository, []) if page == 1 else []
                return httpx.Response(
                    200,
                    json=[
                        {
                            "full_name": f"{owner}/{name}",
                            "name": name,
                            "owner": {"login": owner},
                            "forks_count": len(self.forks.get(f"{owner}/{name}", [])),
                        }
                        for owner, name in forks
                    ],
                )
            if path.startswith("/users/"):
                return httpx.Response(
                    200, json={"created_at": self.users[path.removeprefix("/users/")]}
                )
        if host == "raw.githubusercontent.com":
            content = self.files.get(str(request.url))
            return httpx.Response(200, content=content) if content else httpx.Response(404)
        if host == "accounts.spotify.com":
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "spotify-access"})
        if host == "api.spotify.com":
            assert request.headers["Authorization"] == "Bearer spotify-access"
            if self.spotify_rate_limited:
                return httpx.Response(429, headers={"Retry-After": "3600"})
            track_id = path.removeprefix("/v1/tracks/")
            if track_id not in self.tracks:
                return httpx.Response(404, json={"error": {"status": 404}})
            return httpx.Response(200, json={"id": track_id, **self.tracks[track_id]})
        if host == "www.googleapis.com":
            assert request.url.params["key"] == "youtube-key"
            ids = request.url.params["id"].split(",")
            assert len(ids) <= 50
            items = [{"id": i, **self.videos[i]} for i in ids if i in self.videos]
            return httpx.Response(200, json={"items": items})
        raise AssertionError(f"unexpected request {request.method} {request.url}")
