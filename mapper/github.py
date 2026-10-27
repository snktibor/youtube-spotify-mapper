"""Finds the repositories that share pairs, and reads what they share."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from cronchords.http import send

from .files import Source

API_URL = "https://api.github.com"
# Larger files aren't read: no one has that many songs.
MAX_SHARED_BYTES = 5_000_000


class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self, http: httpx.Client, token: str | None):
        self._http = http
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def forks(self, repository: str) -> list[Source]:
        """Every fork of the repository, forks of forks too."""
        sources: list[Source] = []
        waiting = [repository]
        seen = {repository.lower()}
        while waiting:
            parent = waiting.pop()
            page = 1
            while True:
                forks = self._get(f"/repos/{parent}/forks", {"per_page": "100", "page": str(page)})
                for fork in forks:
                    name = fork["full_name"]
                    if name.lower() in seen:
                        continue
                    seen.add(name.lower())
                    sources.append(Source(fork["owner"]["login"], fork["name"]))
                    if fork.get("forks_count"):
                        waiting.append(name)
                if len(forks) < 100:
                    break
                page += 1
        return sources

    def created_at(self, login: str) -> datetime:
        """When the user or organization joined GitHub."""
        user = self._get(f"/users/{login}")
        return datetime.fromisoformat(user["created_at"])

    def shared(self, source: Source) -> bytes | None:
        """The source's pairs.json, or None if it doesn't have one."""
        with self._http.stream("GET", source.url) as response:
            if response.status_code == 404:
                return None
            if not response.is_success:
                raise GitHubError(f"{source.url} returned {response.status_code}")
            content = b""
            for chunk in response.iter_bytes():
                content += chunk
                if len(content) > MAX_SHARED_BYTES:
                    raise GitHubError(f"{source.url} is larger than {MAX_SHARED_BYTES} bytes")
            return content

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        response = send(self._http, "GET", f"{API_URL}{path}", headers=self._headers, params=params)
        if not response.is_success:
            raise GitHubError(f"GitHub API error {response.status_code} for {path}")
        return response.json()
