# youtube-spotify-mapper

An open list of Spotify tracks and the YouTube videos of the same songs, put
together from the matches people share and checked before it's published.

Apps that move songs between Spotify and YouTube, like
[cronchords](https://github.com/snktibor/cronchords), all search for the same
songs, and YouTube allows only 100 searches a day per project. Here every
match only has to be found once. Anyone can use the list, add to it by pull
request, or share the matches their own app finds.

Not affiliated with Spotify or YouTube.

## Using the pairs

The pairs are in [`data/`](data), split into 256 files so that you only need
to fetch the ones you're looking for. To find the videos of a Spotify track,
take its ISRC (`external_ids.isrc` in the Spotify API, in upper case without
dashes). The first two hex digits of the ISRC's SHA-1 name the file:

```
https://raw.githubusercontent.com/snktibor/youtube-spotify-mapper/main/data/isrc/<xx>.json
```

```json
{
"USQX91300108":[{"source": "matched", "track": "69kOkLUCkxIZYexIgSG8rq", "video": "5NV6Rdv1a3I", "votes": 3}]
}
```

The Spotify tracks of a video work the same way, with the video ID in place of
the ISRC:

```
https://raw.githubusercontent.com/snktibor/youtube-spotify-mapper/main/data/video/<xx>.json
```

```json
{
"5NV6Rdv1a3I":[{"isrc": "USQX91300108", "source": "matched", "track": "69kOkLUCkxIZYexIgSG8rq", "votes": 3}]
}
```

A key that isn't in its file has no pairs yet. Several entries per key are
best first:

- `source` says why the pair is in the list, see
  [How pairs are verified](#how-pairs-are-verified). `reviewed` comes first,
  then `corrected`, then `matched`.
- `votes` is how many GitHub accounts shared the pair. Reviewed pairs may have
  none.
- `isrc` can be `null`: not every Spotify track has one.

[`data/index.json`](data/index.json) has the version of the format and how
many pairs there are. The format only changes along with the version.

Videos get deleted and made private, so check that a video still plays before
you rely on it. If you match songs yourself, check the pairs with your own
rules too. They passed this list's rules, but your app may be stricter.

Please fetch only the files you need, or clone the repository if you need all
of it. The pairs are updated every six hours.

## Sharing pairs

**From cronchords:** turn on sharing as described under
[Sharing matches](https://github.com/snktibor/cronchords#sharing-matches) in
its README. Every fork of cronchords that shares is found on its own.

**From another app:** publish your matches as a file in a public GitHub
repository, and add the repository to [`sources.txt`](sources.txt) with a pull
request, as `owner/repo branch path`. The file looks like this:

```json
{
  "version": 1,
  "pairs": [
    {"track": "69kOkLUCkxIZYexIgSG8rq", "video": "5NV6Rdv1a3I", "isrc": "USQX91300108", "source": "search"}
  ]
}
```

- `track` is a Spotify track ID, `video` a YouTube video ID. The ISRC is
  optional, it's looked up anyway.
- `source` is `search` if your app found the pair on its own, or `correction`
  if a person picked it.
- Entries that don't make sense are skipped, and a file with more than 20,000
  pairs or 5 MB is skipped whole.

The files are read every six hours, and when a pair disappears from them, it
loses the vote it had.

## How pairs are verified

The pairs come from people the list has no reason to trust, so the
[Update workflow](.github/workflows/update.yml) checks every pair before it's
published:

1. **Every vote has a GitHub account behind it.** A pair counts once per
   account, however many of its repositories share it. Accounts younger than
   90 days don't count, so votes can't be made up on the spot, and the accounts
   in [`blocklist.txt`](blocklist.txt) don't count at all.
2. **The track and the video have to exist.** Both are looked up on Spotify
   and YouTube, and looked up again every 90 days.
3. **`matched`: the matching rules accept the pair.** These are the rules
   [cronchords](https://github.com/snktibor/cronchords/blob/main/worker/cronchords/matching.py)
   matches songs with: the title, artist and length have to match, and covers,
   live versions, remixes and the like only match each other. One vote is
   enough, since a made-up pair doesn't get through.
4. **`corrected`: people picked the pair.** People correct matches that the
   rules got wrong, and those may not pass the rules. They need at least two
   accounts that agree, and the video still has to be titled after the song.
5. **`reviewed`: the pair is in [`contrib/pairs.json`](contrib/pairs.json).**
   Pull requests are reviewed, so these only need the track and the video to
   exist.
6. **Pairs in [`contrib/wrong.json`](contrib/wrong.json) are never
   published**, whoever shares them.

The list is worked out anew on every update from what's shared at that time.
So a pair someone stops sharing, or that an account on the blocklist shared,
drops out with the next update.

Spotify lets apps like this one look up only one track per request, so an
update looks up at most 2,000 new tracks. When many pairs come in at once,
the rest are verified in the following updates.

## Contributing

Pull requests are welcome for:

- **A pair that is missing or not the best one:** add it to
  [`contrib/pairs.json`](contrib/pairs.json), with an optional `note`:
  `{"track": "<Spotify track ID>", "video": "<YouTube video ID>", "note": "..."}`.
  The track ID is the part after `/track/` in a Spotify link, the video ID the
  part after `v=` or `youtu.be/` in a YouTube link.
- **A wrong pair:** add it to [`contrib/wrong.json`](contrib/wrong.json) the
  same way, and say what's wrong with it.
- **A source:** add it to [`sources.txt`](sources.txt), see
  [Sharing pairs](#sharing-pairs).
- **An account that shares made-up pairs:** add it to
  [`blocklist.txt`](blocklist.txt) and explain why.
- **The code:** the rules for accepting pairs are in
  [`mapper/rules.py`](mapper/rules.py).

`data/` and `state/` are written by the Update workflow, so leave them out of
pull requests. A check fails if a pull request changes them.

## Running the update

The Update workflow runs every six hours. It needs three repository secrets:

- `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`, of an app in the
  [Spotify developer dashboard](https://developer.spotify.com/dashboard) with
  the Web API selected. The tracks are looked up without signing in to any
  account.
- `YOUTUBE_API_KEY`, an API key of a Google Cloud project with the YouTube Data
  API v3 enabled. Looking up 50 videos costs 1 unit of the 10,000 a day.

## Development

The matching rules come from cronchords, so clone it into `.cronchords` first:

```sh
git clone https://github.com/snktibor/cronchords .cronchords
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest
PYTHONPATH=.cronchords/worker python -m mapper check
```

`python -m mapper update` needs the three secrets above as environment
variables, and `GITHUB_TOKEN` to list the forks without running into GitHub's
rate limit.

## License

The code is under the [MIT license](LICENSE). The pairs in `data/` are in the
public domain under [CC0](data/LICENSE): use them for anything, no credit
needed.
