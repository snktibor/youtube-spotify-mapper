import json

import pytest
from fakes import DREAMS, DREAMS_VIDEO, GET_LUCKY, GET_LUCKY_VIDEO, shared

from mapper.files import (
    FileError,
    SharedPair,
    Source,
    dump_by_key,
    parse_shared,
    read_blocklist,
    read_contrib,
    read_sources,
)


def test_sources_default_to_where_cronchords_shares(tmp_path):
    path = tmp_path / "sources.txt"
    path.write_text("# comment\n\nsomeone/app\nother/repo main data/pairs.json  # theirs\n")

    sources = read_sources(path)

    assert sources == [
        Source("someone", "app", "cronchords-export", "pairs.json"),
        Source("other", "repo", "main", "data/pairs.json"),
    ]
    assert sources[1].url == "https://raw.githubusercontent.com/other/repo/main/data/pairs.json"


@pytest.mark.parametrize("line", ["someone", "a/b/c", "a/b main path extra", "-x/repo"])
def test_sources_that_dont_make_sense_are_reported_with_their_line(tmp_path, line):
    path = tmp_path / "sources.txt"
    path.write_text(f"# comment\n{line}\n")

    with pytest.raises(FileError, match="sources.txt:2:"):
        read_sources(path)


def test_the_blocklist_ignores_case(tmp_path):
    path = tmp_path / "blocklist.txt"
    path.write_text("Troll  # made up pairs\n")

    assert read_blocklist(path) == {"troll"}


def test_contrib_pairs_may_have_a_note(tmp_path):
    path = tmp_path / "pairs.json"
    entries = [
        {"track": GET_LUCKY, "video": GET_LUCKY_VIDEO, "note": "the topic upload"},
        {"track": DREAMS, "video": DREAMS_VIDEO},
    ]
    path.write_text(json.dumps({"pairs": entries}))

    assert read_contrib(path) == {(GET_LUCKY, GET_LUCKY_VIDEO), (DREAMS, DREAMS_VIDEO)}


@pytest.mark.parametrize(
    "entry",
    [
        {"track": "spotify:track:" + GET_LUCKY, "video": GET_LUCKY_VIDEO},
        {"track": GET_LUCKY, "video": "https://youtu.be/" + GET_LUCKY_VIDEO},
        {"track": GET_LUCKY, "video": GET_LUCKY_VIDEO, "votes": 100},
        {"video": GET_LUCKY_VIDEO},
    ],
)
def test_contrib_pairs_have_to_be_plain_ids(tmp_path, entry):
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps({"pairs": [entry]}))

    with pytest.raises(FileError, match="pair 1"):
        read_contrib(path)


def test_contrib_pairs_cant_be_there_twice(tmp_path):
    path = tmp_path / "pairs.json"
    entry = {"track": GET_LUCKY, "video": GET_LUCKY_VIDEO}
    path.write_text(json.dumps({"pairs": [entry, entry]}))

    with pytest.raises(FileError, match="pair 2 is there twice"):
        read_contrib(path)


def test_shared_pairs_that_dont_make_sense_are_left_out():
    content = json.dumps(
        {
            "version": 1,
            "pairs": [
                {"track": GET_LUCKY, "video": GET_LUCKY_VIDEO, "source": "search"},
                {"track": DREAMS, "video": DREAMS_VIDEO, "source": "correction"},
                {"track": "not an id", "video": DREAMS_VIDEO},
                "nonsense",
            ],
        }
    ).encode()

    assert parse_shared(content) == [
        SharedPair(GET_LUCKY, GET_LUCKY_VIDEO),
        SharedPair(DREAMS, DREAMS_VIDEO, corrected=True),
    ]


@pytest.mark.parametrize(
    "content", [b"<html>", b"[]", json.dumps({"version": 2, "pairs": []}).encode()]
)
def test_shared_files_in_another_format_are_refused(content):
    with pytest.raises(FileError):
        parse_shared(content)


def test_cronchords_files_are_understood():
    assert parse_shared(shared((GET_LUCKY, GET_LUCKY_VIDEO, "search"))) == [
        SharedPair(GET_LUCKY, GET_LUCKY_VIDEO)
    ]


def test_dumps_have_a_key_per_line():
    assert dump_by_key({"b": [1, 2], "a": {"y": 1, "x": 2}}) == (
        '{\n"a":{"x": 2, "y": 1},\n"b":[1, 2]\n}\n'
    )
    assert dump_by_key({}) == "{}\n"
