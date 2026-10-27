import json
from datetime import date

from fakes import (
    DREAMS,
    DREAMS_LIVE_VIDEO,
    DREAMS_VIDEO,
    GET_LUCKY,
    GET_LUCKY_COVER_VIDEO,
    GET_LUCKY_VIDEO,
)

from mapper.catalog import Catalog
from mapper.files import SharedPair
from mapper.rules import Mapping, data_files, decide, shard, tally

CHECKED = {"checked": "2026-10-27"}


def catalog() -> Catalog:
    lucky = {"name": "Get Lucky (feat. Pharrell Williams)", "artists": ["Daft Punk"]}
    dreams = {"name": "Dreams", "artists": ["Fleetwood Mac"]}
    return Catalog(
        tracks={
            GET_LUCKY: lucky | {"duration_ms": 248_000, "isrc": "USQX91300108"} | CHECKED,
            DREAMS: dreams | {"duration_ms": 257_000, "isrc": None} | CHECKED,
        },
        videos={
            GET_LUCKY_VIDEO: {
                "title": "Get Lucky (feat. Pharrell Williams)",
                "channel": "Daft Punk - Topic",
                "duration_s": 248,
            }
            | CHECKED,
            GET_LUCKY_COVER_VIDEO: {
                "title": "Get Lucky - Daft Punk (acoustic cover)",
                "channel": "Some Guy",
                "duration_s": 248,
            }
            | CHECKED,
            DREAMS_VIDEO: {
                "title": "Fleetwood Mac - Dreams (Official Music Video)",
                "channel": "Fleetwood Mac",
                "duration_s": 254,
            }
            | CHECKED,
            DREAMS_LIVE_VIDEO: {"gone": True} | CHECKED,
        },
        today=date(2026, 10, 27),
    )


def found(track: str, video: str) -> SharedPair:
    return SharedPair(track, video)


def corrected(track: str, video: str) -> SharedPair:
    return SharedPair(track, video, corrected=True)


def test_pairs_the_matching_rules_accept_need_one_owner():
    votes = tally({"alice": [found(GET_LUCKY, GET_LUCKY_VIDEO)]}, [])

    assert decide(votes, catalog(), set()) == [
        Mapping(GET_LUCKY, GET_LUCKY_VIDEO, "USQX91300108", "matched", 1)
    ]


def test_a_single_correction_the_rules_dont_accept_isnt_enough():
    votes = tally({"alice": [corrected(GET_LUCKY, GET_LUCKY_COVER_VIDEO)]}, [])

    assert decide(votes, catalog(), set()) == []


def test_corrections_count_once_two_owners_agree():
    pair = corrected(GET_LUCKY, GET_LUCKY_COVER_VIDEO)
    votes = tally({"alice": [pair], "bob": [pair, pair]}, [])

    assert decide(votes, catalog(), set()) == [
        Mapping(GET_LUCKY, GET_LUCKY_COVER_VIDEO, "USQX91300108", "corrected", 2)
    ]


def test_corrections_have_to_be_titled_after_the_song_however_many_agree():
    # Get Lucky is not Dreams, whoever says so.
    pair = corrected(GET_LUCKY, DREAMS_VIDEO)
    votes = tally({owner: [pair] for owner in ("alice", "bob", "carol")}, [])

    assert decide(votes, catalog(), set()) == []


def test_reviewed_pairs_only_need_their_track_and_video_to_exist():
    votes = tally({}, [(GET_LUCKY, DREAMS_VIDEO), (DREAMS, DREAMS_LIVE_VIDEO)])

    assert decide(votes, catalog(), set()) == [
        Mapping(GET_LUCKY, DREAMS_VIDEO, "USQX91300108", "reviewed", 0)
    ]


def test_wrong_pairs_and_pairs_not_looked_up_yet_are_left_out():
    unknown_video = "zzzzzzzzzzz"
    votes = tally({"alice": [found(GET_LUCKY, GET_LUCKY_VIDEO), found(DREAMS, unknown_video)]}, [])

    assert decide(votes, catalog(), {(GET_LUCKY, GET_LUCKY_VIDEO)}) == []


def test_data_files_put_the_best_first_and_skip_missing_isrcs():
    mappings = [
        Mapping(GET_LUCKY, GET_LUCKY_COVER_VIDEO, "USQX91300108", "matched", 5),
        Mapping(GET_LUCKY, GET_LUCKY_VIDEO, "USQX91300108", "reviewed", 1),
        Mapping(DREAMS, DREAMS_VIDEO, None, "matched", 2),
    ]

    files = data_files(mappings)

    isrc_file = json.loads(files[f"isrc/{shard('USQX91300108')}.json"])
    assert isrc_file["USQX91300108"] == [
        {"track": GET_LUCKY, "video": GET_LUCKY_VIDEO, "source": "reviewed", "votes": 1},
        {"track": GET_LUCKY, "video": GET_LUCKY_COVER_VIDEO, "source": "matched", "votes": 5},
    ]
    video_file = json.loads(files[f"video/{shard(DREAMS_VIDEO)}.json"])
    assert video_file[DREAMS_VIDEO] == [
        {"track": DREAMS, "isrc": None, "source": "matched", "votes": 2}
    ]
    assert json.loads(files["index.json"]) == {"version": 1, "pairs": 3, "isrcs": 1, "videos": 3}


def test_shards_are_the_start_of_the_sha1():
    # printf USQX91300108 | shasum
    assert shard("USQX91300108") == "12"
