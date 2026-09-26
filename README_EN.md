# BiliBili Video Downloader

Paste a link and download the video. Two versions — CLI and web — with QR-code login
and automatic selection of the highest available quality.
Download a whole favorites folder or a whole UP collection (all of it, or hand-picked
items), and pull in subtitles, danmaku (bullet comments), cover art and video metadata
along the way.

**Only needs `requests`. No ffmpeg, no GUI libraries, and no Flask.**

The QR code is drawn directly in your terminal or on the web page — scan it with your phone.

[中文文档](README.md) | **English**

---

## Two versions, pick what fits

| Version | Entry point | Best for |
|---|---|---|
| CLI | `python bili_dl.py` | Batch downloads, scripting, remote terminals |
| Web | `python web_app.py` | Wanting a UI, scrubbing the video to preview, picking videos out of a favorites folder or a collection |

The web version opens your browser automatically — paste a link in the UI and click.

Both versions share the same download logic (the web app imports `bili_dl.py` directly),
so a fix in one applies to both — they even share the same config file
(`~/.bili_dl.json`). The web version is equally dependency-free: the UI and the HTTP
server are written on the Python standard library, so no Flask and no frontend
framework of any kind.

---

## Read This First

1. **For personal study, research, and backing up content you have the right to save.**
2. Follow BiliBili's Terms of Service and copyright law. Do not redistribute,
   re-upload, or use this for any commercial purpose.
3. This tool **does not download paid series or members-only content, and does not
   bypass any paywall or permission check.**
4. Do not scrape at high frequency. Bulk scraping puts load on their servers and may
   get your account restricted.
5. Once you download someone else's work, the copyright still belongs to the original
   creator.

The tool only calls the public endpoints that BiliBili's own web frontend uses. No
private protocols were reverse-engineered, and no restrictions were broken. What
quality you can get is determined entirely by your account's permissions.

Login credentials are stored only in `.bili_cookies.json` in your local user
directory, and are never uploaded anywhere.

---

## Install

```
python -m pip install requests
```

That's it.

Requires Python 3.8 or newer.

---

## Usage

### Interactive mode

```
python bili_dl.py
```

It asks whether you want to log in by QR code, then you just paste links — as many in a
row as you like. Type `q` to quit, `dir` to change the download directory.

### Download directly

```bash
# Full URL
python bili_dl.py https://www.bilibili.com/video/BV1xx411c7mD

# Just the BV id works too
python bili_dl.py BV1xx411c7mD

# av ids work too
python bili_dl.py av12345

# Short links are resolved automatically
python bili_dl.py https://b23.tv/xxxxxxx
```

A `?p=3` in the URL is picked up automatically — no extra flag needed.

### Common options

| Option | Meaning |
|---|---|
| `-p N` | Download part N |
| `--all` | Download all parts |
| `-o DIR` | Output directory |
| `-q CODE` | Request a specific quality (codes in the table below) |
| `-t TEMPLATE` | Naming template, see the "Naming templates" section |
| `-y` | Auto-confirm every prompt (for scripts) |

Default output directory is `~/Downloads/BiliVideo`.

### Extras

By default only the video itself is downloaded. For subtitles, danmaku and the rest,
turn on the matching switch:

| Option | Meaning |
|---|---|
| `--subtitle` `--sub` | Download subtitles as `.srt` (requires login) |
| `--danmaku` `--dm` | Download danmaku as `.xml`, plus a converted `.ass` |
| `--cover` | Download the cover image |
| `--metadata` `--meta` | Save video metadata as `.info.json` |
| `--all-extras` `--extras` | All four of the above at once |
| `--audio-only` `--audio` | Audio track only, saved as `.m4a` |
| `--mp3` | Also convert the audio to mp3 (requires ffmpeg) |

```bash
# Video + subtitles + danmaku + cover + metadata, all in one go
python bili_dl.py BV1xx411c7mD --all-extras

# Treat it as a podcast — audio only
python bili_dl.py BV1xx411c7mD --audio-only
```

Everything lands next to the video, same name, different extension:

```
SomeUploader/2026-09-21 Some Video.mp4
SomeUploader/2026-09-21 Some Video.srt          subtitles
SomeUploader/2026-09-21 Some Video.xml          raw danmaku
SomeUploader/2026-09-21 Some Video.ass          danmaku subtitles (load directly in a player)
SomeUploader/2026-09-21 Some Video.cover.jpg    cover
SomeUploader/2026-09-21 Some Video.info.json    metadata
```

About subtitles: BiliBili's subtitle endpoint is **flaky**. The same video often needs
several requests before the subtitles show up. The tool retries 5 times by default, and
in practice that gets them within 5 attempts. When it can't, it says so explicitly — it
never silently skips.

About danmaku: advanced danmaku that carry coordinates in their `p` attribute (modes 7
and 8) are not converted. They have no fixed track, and forcing them into scrolling
subtitles makes them an unreadable pile. They are complete in the original `.xml` — parse
that yourself if you need them.

### Favorites folders

```bash
# Interactive: list folders -> pick one -> pick videos -> download
python bili_dl.py --fav

# Just list the folders and their IDs
python bili_dl.py --fav list

# See what's inside a folder (listed, not downloaded yet)
python bili_dl.py --fav 1581869426

# Download the whole folder
python bili_dl.py --fav 1581869426 --all

# Pick specific ones
python bili_dl.py --fav 1581869426 --pick 1,3,5-9

# Filter by title keyword
python bili_dl.py --fav 1581869426 --search tutorial --all

# Only things under 500MB
python bili_dl.py --fav 1581869426 --all --max-size 500MB

# Big folder? Grab 200 entries first to see
python bili_dl.py --fav 1581869426 --limit 200 --all
```

| Option | Meaning |
|---|---|
| `--all` `-a` | Download everything that survived the filters |
| `--pick 1,3,5-9` `-p` | Pick by index, ranges supported |
| `--search KEYWORD` `-s` | Keep only titles containing the keyword |
| `--max-size 500MB` | Estimate size from duration and drop anything over the limit |
| `--limit N` | Fetch at most N entries (very useful for big folders) |
| `--list-only` `--dry` `-n` | List only, don't download |

Batch downloads run 3 concurrent jobs by default (`concurrency` changes that), with a
1-second gap between videos (`interval` changes that). One thing about concurrency is
worth spelling out: `requests`' `Session` **is not thread-safe**, so each job builds its
own session. A shared session under high concurrency produces random SSL errors or
responses bleeding into one another.

**Multi-part videos download only part 1 by default.** Add `--all-parts` to download
every part.

### Collections and series

Favorites folders answer "what did I save"; collections answer "an UP grouped a batch of
videos together". Collections are public, so **no login is needed**.

The handiest way in is to just paste a video link — the tool follows that video to the
collection it belongs to:

```bash
# See which collection this video belongs to, and what's inside it
python bili_dl.py --season BV1xx411c7mD

# Download the whole collection
python bili_dl.py --season BV1xx411c7mD --all

# Pick specific ones
python bili_dl.py --season BV1xx411c7mD --pick 1,3,5-9

# Only the 2nd section (a collection can be split into sections, like "early" / "mid" / "late")
python bili_dl.py --season BV1xx411c7mD --section 2

# Filter by title keyword
python bili_dl.py --season BV1xx411c7mD --search tutorial --all
```

You can also start from the UP and list every collection and series they have:

```bash
# Interactive: paste a video link or the UP's mid and pick step by step
python bili_dl.py --season

# List one UP's collections and series
python bili_dl.py --up 517327498
python bili_dl.py --up https://space.bilibili.com/517327498

# What's inside a given collection or series
python bili_dl.py --up 517327498 --season-id 3993361 --all
python bili_dl.py --up 517327498 --series-id 2229877 --pick 1-3
```

`--all` `--pick` `--search` `--list-only` work exactly the same way as they do for
favorites folders.
When you download a video that belongs to a collection, the tool mentions in passing how
many episodes that collection has.

**A collection and a series are two different things** — different endpoints, different
fields — but to the user they are the same thing:

| | Collection (season) | Series (series) |
|---|---|---|
| Can it have sections | Yes, and a section can hold several episodes | No |
| Typical case | A serialized tutorial, a serialized anime | Livestream replays, a grouping around one topic |
| Paging parameters | `page_num` / `page_size` | `pn` / `ps` |

The tool calls whichever endpoint applies on its own, and every entry `--up` lists is
tagged `[collection]` or `[series]`.

One thing to watch when using `--season-id` / `--series-id`: **those two IDs must be used
together with `--up <mid>`**, because the endpoints require the mid and the ID at the same
time. An ID on its own gets you nothing.

### Naming templates

The default name is `{title}.mp4`. To organize things into directories, use `-t`:

```bash
# One directory per uploader, date in the filename
python bili_dl.py BV1xx411c7mD -t "{up}/{date} {title}"

# Include the BV id so you can trace it back
python bili_dl.py BV1xx411c7mD -t "{up}/{title} [{bvid}]"
```

Available fields:

| Field | Meaning |
|---|---|
| `{title}` | Video title |
| `{bvid}` | BV id |
| `{aid}` | av id |
| `{up}` | Uploader name |
| `{date}` | Publish date, like `2026-09-21` |
| `{p}` | Part index, empty for single-part videos |
| `{part}` | Part name, empty for single-part videos |
| `{quality}` | Quality name |
| `{duration}` | Duration, like `05-32` |

A `/` inside a title does not become a directory — the tool splits the template on `/`
first, then fills fields into each segment, and finally sanitizes each segment. That way
the directory levels you wrote in the template survive, while slashes inside the title
get replaced.

### Config file

If you don't want to type flags every time, save them as config:

```bash
python bili_dl.py --show-config              # show current config and available fields
python bili_dl.py --set template="{up}/{title}"
python bili_dl.py --set subtitle=true
python bili_dl.py --set concurrency=5
```

Config lives in `~/.bili_dl.json`. The web version reads the same file, so a change in
one applies to both. Configurable keys: `outdir` `quality` `template` `concurrency`
`subtitle` `danmaku` `cover` `metadata` `audio_only` `all_parts` `proxy` `interval`.

A corrupt or malformed config file does not raise an error — the tool just falls back to
defaults. A broken config should not make the tool completely unusable.

### Quality codes

Without `-q`, the tool **automatically picks the highest quality your account can get**.

| Code | Quality | Code | Quality |
|---|---|---|---|
| 127 | 8K ultra HD | 80 | 1080P HD |
| 126 | Dolby Vision | 74 | 720P60 |
| 125 | HDR true color | 64 | 720P HD |
| 120 | 4K ultra HD | 32 | 480P |
| 116 | 1080P60 | 16 | 360P |
| 112 | 1080P high bitrate | | |

```bash
python bili_dl.py <url> -q 120      # ask for 4K
```

When the tier you asked for isn't available, the API silently drops to the highest one
you can get, and the tool tells you that explicitly.

### Login

```bash
python bili_dl.py --login           # QR code login (recommended)
python bili_dl.py --login-sms       # phone number / SMS login
python bili_dl.py --set-cookie      # paste cookies manually
python bili_dl.py --whoami          # show current login state
python bili_dl.py --logout          # clear locally saved credentials
```

**QR login is recommended.** Open the BiliBili app on your phone and scan the QR code in
your terminal. No username or password to type, and no captcha involved.

#### About phone-number login

BiliBili's login endpoint now requires passing a Geetest human-verification challenge
first. **This tool does not bypass it** — that is an official anti-automation mechanism.

What actually happens: when requesting the SMS verification code, the server returns
`-105 验证码错误` (wrong verification code). That means human verification has to be
completed first. When the tool sees that code it offers you two paths:

- **Use QR login instead** (recommended — takes a second)
- **Paste cookies manually**: log in to BiliBili normally in your browser, press F12 to
  open the developer tools, go to Application / Storage → Cookies → bilibili.com and copy
  the values of `SESSDATA`, `bili_jct` and `DedeUserID`, then run `--set-cookie` and
  paste them in.

In rare cases the server doesn't demand verification, and SMS login goes through.

---

## Web version

```bash
python web_app.py
```

Your browser opens at `http://127.0.0.1:8848` automatically. Paste a link in the UI,
click "解析并下载" (parse and download), and once the progress bar finishes you can click
the link to save the file.

### What the web version can do

- Parse a pasted link and show title, uploader, duration and quality
- The quality dropdown is populated from **the tiers this specific video actually
  offers**, not a hardcoded list
- Live progress bar: percentage, downloaded / total, speed
- Download history list, with a clickable save link once finished
- **Scrubbable preview**: the file server implements Range requests, so the browser can
  play and seek directly
- QR login inside the page, with the QR code drawn on the page itself
- **Favorites folders / collections**: the source can be switched.
  Pick "account favorites" to list the logged-in account's folders;
  pick "UP collections" and paste a video link to find the collection it belongs to,
  or paste the UP's mid / space link to list all of that UP's collections and series.
  Once a video list is loaded you can tick several for a batch download, or click
  "download" on a single row to grab just that one
- **Extras options**: subtitles, danmaku, cover, metadata, audio-only, download all
  parts — all of these, together with the naming template above, apply to both single
  downloads and batch downloads
- Batch progress panel: each video's percentage, done or failed at a glance, and finished
  ones have a clickable "save"

### Options

```bash
python web_app.py --port 9000        # different port, default is 8848
python web_app.py --out D:\videos    # output directory
python web_app.py --no-browser       # don't open a browser
python web_app.py --jobs 4           # concurrency for batches, defaults to config, capped at 4
```

If the port is taken it automatically tries the next ones instead of failing outright.

The web version and the CLI share `~/.bili_dl.json`: the naming template, quality and
extras switches are pre-filled in the UI, and the download directory matches too.

### Why the web version runs a local server

You might ask: if there's a web UI, why not just ship a single HTML file you
double-click?

Because a pure frontend approach can't work here. There are three obstacles that can't be
worked around:

1. **CORS**
   BiliBili's playback URL endpoint doesn't return CORS headers, so `fetch` from a browser
   page gets blocked. That's a browser security policy — frontend code can't get around
   it (and shouldn't).

2. **Nowhere to keep credentials**
   The cookies from QR login have to be stored somewhere. A pure frontend could only stuff
   them into `localStorage`, and the moment you share the page or move to another computer
   the credentials travel with it. That's not safe.

3. **Large downloads are unreliable**
   When the browser downloads a few hundred MB, a mid-way drop can't be resumed, and there
   is no stable local path to hand you.

So a small local server does the work: requests are made by the server (no CORS
restriction), credentials live in the server's user directory (the same file the CLI
uses), and downloads are performed by the server with resume support. The browser only
displays and triggers.

### Security

- **Listens on `127.0.0.1` only**, so it isn't reachable from outside. Unless you
  explicitly add `--host 0.0.0.0`, in which case other devices on your network can reach
  it — startup prints a warning when you do
- **Cross-site requests are rejected**: `Origin` is checked, and any non-local origin gets
  a 403
- **No arbitrary file read**: the file endpoint takes only a job ID, and the path is
  looked up in the server's own job table — whatever path the client sends is useless
- **The API cannot change the download directory**: extras in the request go through a
  whitelist that only recognizes the template and a few switches; every other key is
  ignored, and the template length has a cap as well
- **Everything rendered in the page is escaped**: video titles come from the API and are
  untrusted input, so they are escaped before being spliced into HTML — a title containing
  `<` won't wreck the layout

### Web version limitations

- **Multi-part videos download only part 1 by default** (if the link carries `?p=`, that
  wins). To get them all, tick "download all parts" in the UI
- A favorites batch accepts at most 500 videos at a time. For more, split it into batches
  or use the CLI

---

## Why no ffmpeg is needed

BiliBili serves two kinds of playback URLs:

| Kind | Content | Needs ffmpeg |
|---|---|---|
| Pre-merged MP4 (API parameter `fnval=1`) | Audio and video in one file | **No** |
| DASH separated tracks | Video track and audio track apart | Yes |

The tool **requests the pre-merged MP4 by default**, so what you download is a complete,
directly playable file. Verified working across multiple videos.

When a video only has DASH available, the tool downloads the video track and the audio
track separately and looks for ffmpeg to merge them. If ffmpeg isn't found it still
doesn't fail — it keeps both files and tells you how to load them separately in a player.

---

## Features

- **Resume support**   re-run after an interruption and it continues from where it stopped
- **Multi-segment merging**   videos split into several segments are downloaded and joined automatically
- **Progress display**   percentage, downloaded amount, total size and speed in real time
- **Filename sanitizing**   strips characters Windows doesn't allow, so saving never fails
- **Automatic quality**   after login, picks the highest tier your account is allowed
- **Skip existing**   a file that's already downloaded is skipped
- **Favorites downloads**   download a whole folder, or filter by index, keyword or size and pick
- **Collections and series**   paste a video link to find the collection it belongs to and download the whole set; you can also list all of a UP's collections
- **Extras**   subtitles, danmaku (with ASS conversion), cover art, video metadata
- **Audio only**   grab the audio track directly instead of wasting bandwidth on video
- **Naming templates**   organize into directories automatically by uploader, date, quality and more
- **Config file**   store your usual settings once, shared by the CLI and the web version

---

## Tests

```bash
python test_bili.py              # CLI main suite, 216 checks, offline
python test_bili.py --online     # also verifies the live API
python test_fav.py               # favorites, collections, templates, extras, config, 262 checks, offline
python test_shutdown.py          # stopping the service doesn't wait for in-flight requests
python test_stability.py         # stability fuzzing, hunts specifically for crashes
python test_e2e_stability.py     # end-to-end, includes a real resume test
python test_qr.py                # QR encoder, cross-verified with an independent decoder
python test_web.py               # web version API tests, 74 checks offline
python test_web.py --online      # web version live end-to-end, 107 checks
python test_web_browser.py       # drives the real UI in a real browser, 53 checks
```

### CLI

| Group | Coverage |
|---|---|
| 1 | Link parsing — 20 forms including short links, part parameters, garbage input and oversized input |
| 2 | Filename sanitizing, including Windows reserved names and illegal characters |
| 3 | Formatting function boundaries |
| 4 | QR encoding — finder patterns, timing patterns, version info, terminal rendering |
| 5 | Block and error-correction parameter consistency, checked against per-version total capacity |
| 6 | API response parsing — merged streams, DASH, error responses, network failures |
| 7 | CLI interface, including exit codes and error messages |
| 8 | Live API verification |
| 9 | Quality selection — premium-membership scenarios, silent downgrade, DASH track picking |
| 10 | Argument precedence and defaults |

### Favorites, collections and extras

| Group | Coverage |
|---|---|
| 1 | Naming templates: field substitution, slash handling, illegal characters, empty arguments |
| 2 | Config file read/write, corrupt-file fallback, unknown keys ignored |
| 3 | The part *count* is never mistaken for the part *index* (this one is a regression guard) |
| 4 | Selection expressions and size notation, including full-width commas, reversed ranges, all units |
| 5 | Dead-video filtering, both forms of dead entry |
| 6 | Interactive vs non-interactive: `safe_input` never blocks in the background |
| 7 | Quiet mode: `info`/`step`/`ok` silenced, `warn`/`err` still printed |
| 8 | Web parameter whitelist and MIME inference |
| 9 | Batch state machine and artifact lookup |
| 10 | Page elements, placeholders, required JS functions, title escaping |
| 11 | Subtitle-to-SRT and danmaku-to-ASS, including dirty data and advanced-danmaku skipping |
| 12 | Concurrency isolation: per-job sessions, restoring the interactivity switch |
| 13 | Collection entry parsing: mid, space link, video link, and the hint when it can't tell |
| 14 | Collection section slicing: the three sections' boundaries don't start at a fixed value, out of range must raise |
| 15 | Trimming fields out of the web response, so play counts and the like aren't handed to the frontend |
| 16 | Collection routing and page elements, clearing the list on source switch, collection name escaping |
| 17 | Variable shadowing check (output functions like `info` must not be covered by a parameter) |

### Web version

| Group | Coverage |
|---|---|
| 1 | Page rendering, including placeholder substitution and "no external resources" |
| 2 | Job API — nonexistent jobs, oversized IDs, percent-encoded parameters |
| 3 | File serving and Range: full, middle, suffix, open-ended, out-of-range 416, invalid Range |
| 4 | Security hardening: path traversal, cross-site requests, oversized bodies, invalid links |
| 5 | Concurrency: 8 parallel downloads plus multi-threaded status polling |
| 6 | Job management: ID generation, progress math, divide-by-zero, count cap |
| 7 | Helper functions and byte counting |
| 8 | CLI entry point, including automatic port fallback when the port is taken |
| 9 | Live end-to-end: the QR endpoint, a real download, fetching it back with Range and checking it |
| 10 | Collections and series (online): list a UP's collections, fetch collection videos, fetch series videos, find a collection from a video |

The web version's file-serving tests work by **injecting fake jobs**, so they run fully
offline and are repeatable, unaffected by how BiliBili's API happens to behave.

### Browser end-to-end

`test_web_browser.py` launches a real Edge (headless) and drives the page over CDP:
fills in a link, clicks the button, waits for the download, reads the on-screen text, then
loads a favorites folder, selects all, batch-downloads; then switches to the collection
source, pastes a video link to find its collection, downloads one of them on its own,
pastes the UP mid to list the collections, picks a series to load, and finally switches
back to favorites to confirm the list was cleared.

It verifies **the things API tests can't reach** — whether the click handler is actually
bound, whether the progress bar really moves, whether the number on the button is right
after select-all, whether the save link appears when it's done, whether switching the
source clears what the previous step left behind.

```
python -m pip install websockets     # only needed to run this test
python test_web_browser.py
```

During the test the page installs a `window.__errs` collector: any JS error or unhandled
Promise rejection turns the last group red.

### Stability fuzzing

The main suite uses a fair amount of synthetic data, which can hide problems that only
appear in a real environment. This group deliberately avoids mocks:

- Malformed API responses (a quality list with strings and `None` mixed in, a missing
  `owner` field, a missing title)
- `ConnectionError` / `ChunkedEncodingError` / `Timeout` thrown mid-download
- stdin hitting EOF immediately in a non-interactive environment
- Whether read-only commands have side effects such as creating directories
- Functions receiving arguments of the wrong type

The end-to-end group also **really performs a download and a resume**: it downloads 400
bytes and drops the connection, the second run sends `Range: bytes=400-` and fetches only
the remainder, and finally the finished file is verified byte-for-byte against the
expected content.

`test_shutdown.py` specifically verifies that stopping the service doesn't wait for
in-flight requests: it fabricates a request that hangs for 30 seconds, then shuts the
service down and requires it to exit within 3 seconds (measured: 0.23 seconds).

### How the QR encoder is verified

The QR part is implemented from scratch in pure Python, with no third-party library. It
is not verified by comparing matrices against another library — instead the generated code
is rendered to a pixel image and handed to **OpenCV's independent decoder** to read back
and compare the content.

```bash
python -m pip install opencv-python-headless numpy
python test_qr.py
```

Why not compare matrices: different implementations may choose different masks, both are
valid, so such a comparison proves nothing about correctness. Only a code an independent
decoder can actually read counts as correct.

---

## Known limitations

- **Phone/SMS login will most likely not work**, because the official endpoint demands
  human verification. See the explanation above.
- **Paid series and members-only content are not downloaded.** This is intentional, not
  a defect.
- **Not every video can be downloaded.** Creator-disabled downloads, videos set to
  private, region restrictions and similar cases will all fail.
- **The subtitle endpoint is flaky.** The same video needs several requests before
  subtitles appear; the tool has retries built in.
- **A batch accepts at most 500 videos at a time** (web version batches). For more,
  split it into batches or use the CLI.
- **Multi-part videos download only part 1 by default**; getting them all requires
  explicitly turning the switch on. The favorites API gives you the *total* number of
  parts, the collection API doesn't (always counted as 1), and neither API gives you
  *which* part an entry is, so there's no way to pick part by part in a batch.
- **Collection sections are only recognized when you come in from a video link.** The
  `ugc_season` endpoint carries the section structure, `seasons_archives_list` doesn't
  (measured: its `meta` only has
  `category/cover/description/mid/name/ptime/season_id/title/total`).
  When you specify with `--season-id`, the tool makes one extra request to get the
  sections, and if the first episode has been moved out of the collection so they don't
  line up, it tells you explicitly to use `--pick` instead.
- **Channels (`频道`) and playlists (`播放列表`) are not supported.** Those are a
  different structure on the UP's homepage, with different endpoints.
- The endpoints are BiliBili's public web APIs. If they change in the future, the tool
  will need updating accordingly.

---

## FAQ

**I logged in, so why am I still getting 1080P?**

The quality ceiling is decided by your account's permissions and by the video itself. You
need both.

- If the video was only ever uploaded at 1080P, nobody can get more than that
- If the video has 4K but it requires a premium membership and you don't have one, then
  1080P is as high as it goes

The tool tells you both what you actually got and what this account can use. When you see
"this account can get up to XXX for this video", that XXX is the ceiling for your account.

**My phone said login succeeded, but the terminal is still waiting for a scan?**

You still need to tap "confirm login" in the mobile app. Any time before the QR code
expires works.

**It says "failed to draw QR code" — what do I do?**

It doesn't affect usability. The link is printed right below the QR code — send it to your
phone's browser and open it, and the result is the same.

**The download broke halfway. What now?**

Just re-run the same command. The tool tracks progress in a `.part` file and resumes from
where it stopped. During a download the filename is `xxx.mp4.part`, and it's only renamed
to `xxx.mp4` once everything has arrived — so if you see `.part`, it isn't finished.

**Can it download paid series or members-only content?**

No, and that's intentional. The tool does not bypass any paywall or permission check.

**Why do some videos say ffmpeg is needed?**

For a few videos BiliBili doesn't provide a pre-merged MP4, only separated DASH tracks.
In that case the tool downloads the video track and the audio track separately and looks
for ffmpeg to merge them. If ffmpeg isn't installed it doesn't error out — both files are
kept, and you can load them separately in a player.

**Why is there something like `[P1]` in the filename?**

Multi-part videos get a separate name per part, otherwise the parts would overwrite each
other.

**The folder clearly has videos in it — why doesn't the count match?**

Entries that have died are filtered out of a favorites folder, so you see fewer than the
website shows. Both forms of dead entry are recognized: ones where the API doesn't return
a bvid at all, and ones where the bvid is still there but the title has been replaced with
something like "已失效视频" (video no longer available). Without filtering the latter out
early, a download would waste a request and then report failure — and across a batch of
several hundred that adds up to a pile of fake failures.

**What's the difference between a "collection" and a "series"?**

Both are an UP grouping a batch of videos together, but the underlying endpoints differ:

- **Collections** can be split into sections (like "early" / "mid" / "late"); a
  serialized tutorial is usually a collection
- **Series** have no sections; livestream replays and groupings around one topic are
  usually series

The tool calls whichever endpoint applies on its own, and when `--up` lists them every
entry is tagged `[collection]` or `[series]`, so you don't have to tell them apart
yourself.

**Why does `--season-id` have to be paired with `--up <mid>`?**

The endpoints require the mid and the ID at the same time; give only an ID and nothing
comes back. The `--season` path (pasting a video link) doesn't need the mid by hand —
the tool reads it out of the video info.

**I ticked subtitles but got no `.srt`?**

BiliBili's subtitle endpoint is unstable; the same video needs several requests before
subtitles appear. The tool already retries 5 times, and if all 5 fail that means the
subtitles genuinely aren't retrievable at this moment — try again later and they may be
there. When there are no subtitles, no empty `.srt` file is created, so you don't end up
with an empty file that looks legitimate.

**Why are some danmaku missing from the `.ass`?**

Advanced danmaku that carry coordinates (modes 7 and 8) have no fixed track, and
converting them into scrolling subtitles crushes them into an unreadable clump, so they
are skipped. The complete danmaku are in the `.xml` next to it, not a single line short.

**Why does Ctrl+C halfway through a download take a while to quit?**

It doesn't wait any more. Stopping the service doesn't wait for in-flight requests — it
exits immediately, and whatever wasn't finished stays in the `.part` file for the next run
to continue.

**Will my account get banned?**

The tool does no cracking at all, and its request pattern matches the web frontend. But
**do not scrape at high frequency** — that can trip risk control no matter what tool you
use. Normal personal-viewing volume is fine.

---

## Changelog

### v1.4

Collection and series downloads, filled in on both the CLI and the web version at once.

New

- **UP collection and series downloads** (`bili_dl.py --season` / `--up`). The handiest
  way in is pasting a video link: the tool follows that video to the collection it belongs
  to and gets the whole set in one request. You can also list all of a UP's collections and
  series, then specify one by ID and download it in a batch or pick items out of it
- **Collection sections**: `--section 2` downloads only the 2nd section. A collection can
  be split into several sections, like "early / mid / late"
- **A new "UP collections" source in the web version**: it shares one list and checkbox
  mechanism with favorites, and the source can be switched. Pasting a video link gets you
  there in one step; pasting a mid lists every collection and you pick
- When downloading a video that belongs to a collection, the tool mentions in passing how
  many episodes it has and how to grab the whole set
- Added `bili_ugc.py`; `test_fav.py` gained 5 groups (13-17),
  `test_web.py --online` gained a live collections group, and `test_web_browser.py` gained
  a collections UI group

Two places that are easy to get wrong — both are written into the comments

- **The collection API and the series API use different field names.** The paging
  parameter is `page_num`/`page_size` for one and `pn`/`ps` for the other; the ID is called
  `season_id` for one and `series_id` for the other. Each gets its own fetch function, but
  both normalize into the same structure before being handed upwards
- **Only `ugc_season` has the section information.** `seasons_archives_list`'s `meta` has
  no sections (measured: only category/cover/description/mid/name/ptime/season_id/
  title/total). So `--section` on the "coming in from `--season-id`" path has to make one
  extra request, looking it up with any one episode from the collection

Also fixed along the way

- **`show_sections(info, ...)` covered the module-level `info()` output function**, and
  the line that called it raised `TypeError: 'dict' object is not callable`.
  The parameter is renamed to `cinfo`. This is a repeat of the same-name problem from
  v1.2, so this time a static check was added (test group 17) to block this whole class of
  shadowing
- The web version didn't clear the previous step's list when switching sources, which
  produced mismatched states like "the source says favorites but the list holds collection
  videos"

### v1.3

Favorites folders and extras, filled in on both versions at once.

New

- **Account favorites folder downloads** (`bili_dl.py --fav`, plus a new favorites column
  in the web version): download a whole folder in a batch, or pick individual videos.
  Filtering by index, title keyword and size limit is supported
- **Extras**: subtitles (`.srt`), danmaku (`.xml` plus a converted `.ass`), cover art,
  metadata JSON. `--all-extras` turns them all on at once
- **Audio only**: `--audio-only` grabs just the audio track and saves `.m4a`, `--mp3` also
  converts it to mp3
- **Naming templates**: `-t "{up}/{date} {title}"`, 9 usable fields in total
- **Config file**: `~/.bili_dl.json`, managed with `--show-config` / `--set key=value`,
  shared by the CLI and the web version
- **Web favorites UI**: a favorites dropdown, a video list with checkboxes,
  select-all / clear / invert, a per-row "download" button, and a batch progress panel
- Added `test_fav.py` (199 checks) and `test_shutdown.py`

Bugs fixed

- **Multi-part videos downloaded the wrong part.** The field called `page` in the
  favorites API is the *total number of parts*, not the *index of this part*. It was being
  used as an index, so every multi-part video ended up downloading only its last part.
  Measured: `BV15J41187T2` returns 2 for that field, and the video-info API reports exactly
  2 entries in `pages`. It's now renamed to `parts` and used only to display "N parts
  total"
- **Background download threads could hang forever.** For multi-part videos the tool asks
  which part to download; when the web version is started from a terminal,
  `sys.stdin.isatty()` is True, so the background thread really called `input()` and waited
  forever for an answer nobody could give — which showed up in the web UI as a progress bar
  stuck at 0%, with no error. Now the entry point explicitly declares `set_interactive()`
  instead of guessing from `isatty()`
- **The web version never imported `re`**, so every favorites download raised `NameError`
- **`--max-size 500MB` was silently ignored.** Only single-letter suffixes were recognized,
  so `500MB` was judged unparseable and returned 0 — and 0 means "no limit set", so the
  filter did nothing at all. It now understands `B/KB/MB/GB/TB` in their various forms, and
  says so explicitly when it can't parse something
- **Ctrl+C took as long as the largest download to stop the web version.**
  `ThreadingHTTPServer` defaults to `block_on_close=True`, which joins every request thread
  on shutdown. That behavior is now turned off; measured with a stuck request, shutdown
  time went from 30 seconds to 0.23 seconds
- **Finished batch files couldn't be opened.** The file endpoint only consulted the
  single-video job table, and no batch entry was in it. It now also looks in the batch
- **Extras were always served as `video/mp4`**, so audio and subtitles got opened by the
  wrong program. Now inferred from the extension
- **A `<` in a video title wrecked the layout.** Titles come from the API and are
  untrusted input; they are now uniformly escaped before being spliced into `innerHTML`
- **A browser cancelling a request spammed a stack trace into the terminal.**
  `ConnectionResetError` / `BrokenPipeError` are normal when a browser refreshes a page
  repeatedly; they are no longer treated as program errors
- **The favicon 404 log line.** It now returns 204 and isn't logged
- **The console got flooded during batch downloads.** In quiet mode `info`/`step`/`ok` are
  no longer printed, and the part list is no longer printed entry by entry;
  `warn`/`err` are still printed as usual

### v1.2

Added the web version.

- **New `web_app.py` web version**: a small server that runs locally; once started, paste a
  link in the browser and download
- The UI is implemented with zero dependencies — no Flask, no frontend framework of any
  kind, still only `requests`
- QR login inside the page, with the code drawn on the page itself
- The quality dropdown is filled from the tiers this specific video actually offers
- The file server supports Range requests, so you can scrub and preview in the browser
- Added `test_web.py` (74 offline checks + live end-to-end) and
  `test_web_browser.py` (drives the real UI in a real browser, 19 checks)

Two bugs fixed:

- **An out-of-range Range request hung the client forever.** The 416 response omitted
  `Content-Length`, so on an HTTP/1.1 keep-alive connection the client waited forever for a
  response body that never came. Measured: from "hangs" to returning in 0.01 seconds
- **Oversized request bodies corrupted the keep-alive connection.** The bytes past the
  limit were never drained, and the leftover was parsed as the beginning of the next
  request. Now the body is always read to the end before the request is rejected (413)

### v1.1

Stability fixes, aimed at what actually happens when a download goes wrong.

- **An interrupted download no longer crashes the program.** Network hiccups, reset
  connections and read timeouts used to propagate the exception all the way to the top and
  exit the whole program. Now they're caught, the `.part` file is kept, and the next run
  continues from where it stopped
- **Fixed a connection leak.** Responses with a non-200/206 status were never closed, so a
  few retries piled up connections and large downloads could stall
- **Malformed API responses no longer crash.** A quality list with strings or `None` mixed
  in, a missing `owner` field, a missing title — all of these used to raise and abort the
  download
- **`sanitize` and `parse_link` accept non-string input** instead of raising a type error
- Added stability fuzzing and end-to-end resume verification

### v1.0

First release.

- Link parsing supports BV ids, av ids, full URLs, b23.tv short links and part parameters
- QR login with the QR code drawn in the terminal, backed by a pure-Python QR encoder
- Phone/SMS login and manual cookie login
- Automatic selection of the highest quality available to the account
- Resume support, multi-segment merging, skip-if-exists
- Uses pre-merged MP4 by default, which is why ffmpeg is not needed
- 216 tests, including cross-verification of the QR encoder with an independent decoder

---

## License

MIT License — see the [LICENSE](LICENSE) file.

This project is for learning and exchange only. Users bear full responsibility for their
own usage, and must comply with local laws and regulations as well as BiliBili's Terms of
Service.
