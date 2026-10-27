import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fakes import (
    DREAMS,
    DREAMS_VIDEO,
    GET_LUCKY,
    GET_LUCKY_COVER_VIDEO,
    GET_LUCKY_VIDEO,
    GONE_VIDEO,
    FakeCloud,
    shared,
)

from mapper.__main__ import Report, check, update
from mapper.catalog import SpotifyCatalog, YouTubeCatalog
from mapper.github import GitHub
from mapper.rules import shard

TODAY = date(2026, 10, 27)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "contrib").mkdir()
    (tmp_path / "contrib/pairs.json").write_text('{"pairs": []}')
    (tmp_path / "contrib/wrong.json").write_text('{"pairs": []}')
    (tmp_path / "sources.txt").write_text("")
    (tmp_path / "blocklist.txt").write_text("")
    return tmp_path


@pytest.fixture
def cloud() -> FakeCloud:
    cloud = FakeCloud()
    cloud.share(
        "alice",
        shared((GET_LUCKY, GET_LUCKY_VIDEO, "search"), (DREAMS, DREAMS_VIDEO, "search")),
    )
    return cloud


def run(root: Path, cloud: FakeCloud, today: date = TODAY) -> Report:
    with httpx.Client(transport=cloud.transport) as http:
        return update(
            root,
            GitHub(http, "github-token"),
            SpotifyCatalog(http, "client", "secret"),
            YouTubeCatalog(http, "youtube-key"),
            today,
        )


def isrc_entries(root: Path, isrc: str) -> list[dict[str, object]]:
    return json.loads((root / f"data/isrc/{shard(isrc)}.json").read_text())[isrc]


def test_shared_pairs_are_verified_and_published(root, cloud):
    report = run(root, cloud)

    assert report.sharing == ["alice/cronchords"]
    assert isrc_entries(root, "USQX91300108") == [
        {"track": GET_LUCKY, "video": GET_LUCKY_VIDEO, "source": "matched", "votes": 1}
    ]
    assert json.loads((root / "data/index.json").read_text())["pairs"] == 2
    assert set(json.loads((root / "state/tracks.json").read_text())) == {GET_LUCKY, DREAMS}


def test_what_was_looked_up_isnt_looked_up_again_for_a_while(root, cloud):
    run(root, cloud)
    cloud.requests.clear()

    run(root, cloud, TODAY + timedelta(days=30))
    assert cloud.calls("api.spotify.com") == cloud.calls("www.googleapis.com") == []

    run(root, cloud, TODAY + timedelta(days=90))
    assert len(cloud.calls("api.spotify.com")) == 2


def test_forks_of_forks_share_too_and_owners_vote_once(root, cloud):
    pair = (GET_LUCKY, GET_LUCKY_COVER_VIDEO, "correction")
    cloud.share("bob", shared(pair))
    cloud.forks["bob/cronchords"] = [("carol", "cronchords")]
    cloud.users["carol"] = "2020-01-01T00:00:00Z"
    cloud.files[
        "https://raw.githubusercontent.com/carol/cronchords/cronchords-export/pairs.json"
    ] = shared(pair)
    # A second repository of bob's doesn't make a third vote.
    (root / "sources.txt").write_text("bob/songs main pairs.json\n")
    cloud.files["https://raw.githubusercontent.com/bob/songs/main/pairs.json"] = shared(pair)

    report = run(root, cloud)

    assert report.sharing == ["alice/cronchords", "bob/cronchords", "carol/cronchords", "bob/songs"]
    assert isrc_entries(root, "USQX91300108") == [
        {"track": GET_LUCKY, "video": GET_LUCKY_COVER_VIDEO, "source": "corrected", "votes": 2},
        {"track": GET_LUCKY, "video": GET_LUCKY_VIDEO, "source": "matched", "votes": 1},
    ]


def test_blocked_and_new_accounts_dont_count(root, cloud):
    pair = (GET_LUCKY, GET_LUCKY_COVER_VIDEO, "correction")
    cloud.share("troll", shared(pair))
    cloud.share("sockpuppet", shared(pair), joined="2026-10-01T00:00:00Z")
    (root / "blocklist.txt").write_text("Troll\n")

    report = run(root, cloud)

    assert report.skipped == ["sockpuppet/cronchords: the account is younger than 90 days"]
    assert [e["video"] for e in isrc_entries(root, "USQX91300108")] == [GET_LUCKY_VIDEO]


def test_a_blocked_owners_pairs_drop_out_with_the_next_update(root, cloud):
    run(root, cloud)
    (root / "blocklist.txt").write_text("alice\n")

    run(root, cloud)

    assert not (root / f"data/isrc/{shard('USQX91300108')}.json").exists()
    assert json.loads((root / "data/index.json").read_text())["pairs"] == 0
    assert json.loads((root / "state/tracks.json").read_text()) == {}


def test_reviewed_and_wrong_pairs_come_from_contrib(root, cloud):
    contrib = {"pairs": [{"track": GET_LUCKY, "video": GET_LUCKY_COVER_VIDEO}]}
    (root / "contrib/pairs.json").write_text(json.dumps(contrib))
    wrong = {"pairs": [{"track": GET_LUCKY, "video": GET_LUCKY_VIDEO}]}
    (root / "contrib/wrong.json").write_text(json.dumps(wrong))

    run(root, cloud)

    assert isrc_entries(root, "USQX91300108") == [
        {"track": GET_LUCKY, "video": GET_LUCKY_COVER_VIDEO, "source": "reviewed", "votes": 0}
    ]


def test_deleted_videos_and_unreadable_files_are_left_out(root, cloud):
    cloud.share("bob", shared((GET_LUCKY, GONE_VIDEO, "search")))
    cloud.share("carol", b"<html>not json</html>")

    report = run(root, cloud)

    assert report.skipped == [
        "carol/cronchords: not JSON: Expecting value: line 1 column 1 (char 0)"
    ]
    assert [e["video"] for e in isrc_entries(root, "USQX91300108")] == [GET_LUCKY_VIDEO]
    assert json.loads((root / "state/videos.json").read_text())[GONE_VIDEO]["gone"]


def test_pairs_wait_while_spotify_limits_requests(root, cloud):
    cloud.spotify_rate_limited = True

    report = run(root, cloud)

    assert report.waiting == 2
    assert report.mappings == []

    cloud.spotify_rate_limited = False
    assert len(run(root, cloud).mappings) == 2


def test_the_repository_files_are_fine():
    assert check(Path(__file__).resolve().parent.parent) == 0
