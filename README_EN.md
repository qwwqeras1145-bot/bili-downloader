# BiliBili Video Downloader

A command-line tool for downloading BiliBili (哔哩哔哩) videos. Paste a link and it downloads.

Supports QR-code login and phone/SMS login.

**Only needs `requests`. No ffmpeg. No GUI libraries.**

The QR code is drawn directly in your terminal — scan it with your phone to log in.

[中文文档](README.md) | **English**

---

## Read This First

1. **For personal study, research, and backing up content you have the right to save.**
2. Follow BiliBili's Terms of Service and copyright law. Do not redistribute,
   re-upload, or use this for any commercial purpose.
3. This tool **does not download paid content or members-only content, and does not
   bypass any paywall or permission check.**
4. Do not hammer the API. Bulk scraping puts load on their servers and may get your
   account restricted.
5. Copyright of downloaded content belongs to the original creator.

The tool only calls the public web endpoints that BiliBili's own website uses.
No private protocols were reverse-engineered and no restrictions were broken.
What quality you can get is determined entirely by your account's permissions.

Login credentials are stored only in `.bili_cookies.json` in your local user
directory, and are never uploaded anywhere.

---

## Install

```bash
python -m pip install requests
```

That's it. Requires Python 3.8+.

---

## Usage

### Interactive mode

```bash
python bili_dl.py
```

It will offer to log you in via QR code, then you just paste links one after another.
Type `q` to quit, `dir` to change the download directory.

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

### Options

| Option | Meaning |
|---|---|
| `-p N` | Download part N |
| `--all` | Download all parts |
| `-o DIR` | Output directory |
| `-q N` | Request a specific quality (see table below) |

Default output directory is `~/Downloads/BiliVideo`.

### Quality codes

If you don't pass `-q`, the tool **automatically picks the highest quality your
account is allowed to download**.

| Code | Quality | Code | Quality |
|---|---|---|---|
| 127 | 8K | 80 | 1080p |
| 126 | Dolby Vision | 74 | 720p60 |
| 125 | HDR | 64 | 720p |
| 120 | 4K | 32 | 480p |
| 116 | 1080p60 | 16 | 360p |
| 112 | 1080p high bitrate | | |

```bash
python bili_dl.py <url> -q 120      # ask for 4K
```

If the quality you ask for isn't available to your account, the API silently
downgrades — the tool will tell you exactly what happened.

### Login

```bash
python bili_dl.py --login           # QR code login (recommended)
python bili_dl.py --login-sms       # phone / SMS login
python bili_dl.py --set-cookie      # paste cookies manually
python bili_dl.py --whoami          # show current login state
python bili_dl.py --logout          # clear saved credentials
```

**QR login is recommended.** Open the BiliBili mobile app, scan the QR code in your
terminal, done. No password to type, no captcha to solve.

#### About phone / SMS login

BiliBili's login endpoint now requires passing a Geetest human-verification challenge.
**This tool does not bypass it** — that's an official anti-automation measure.

In practice: requesting an SMS code returns `-105 验证码错误` (wrong verification code),
which actually means the human-verification step is missing. When the tool sees that
code it gives you two options:

- **Use QR login instead** (recommended — takes two seconds)
- **Paste cookies manually**: log in normally in your browser, press F12, go to
  Application / Storage → Cookies → bilibili.com, copy the values of `SESSDATA`,
  `bili_jct` and `DedeUserID`, then run `--set-cookie` and paste them in.

In rare cases the server doesn't demand verification, and SMS login works as-is.

---

## Why no ffmpeg is needed

BiliBili serves two kinds of playback URLs:

| Kind | Content | Needs ffmpeg |
|---|---|---|
| Pre-merged MP4 (`fnval=1`) | Audio and video in one file | **No** |
| DASH separated tracks | Video track and audio track apart | Yes |

The tool **requests the pre-merged MP4 by default**, so what you download is a
complete, directly playable file. Verified working across multiple videos.

When a video only has DASH available, the tool downloads the video and audio tracks
separately and looks for ffmpeg to merge them. If ffmpeg isn't found it doesn't fail —
it keeps both files and tells you how to load them in a player.

---

## Features

- **Resume support** — re-run after an interruption and it continues where it left off
- **Multi-part merging** — videos split into segments are downloaded and joined automatically
- **Progress display** — percentage, downloaded amount, total size and speed in real time
- **Filename sanitizing** — strips characters Windows doesn't allow, so saving never fails
- **Automatic quality** — picks the highest your account allows after login
- **Skip existing** — files already downloaded are skipped

---

## Tests

```bash
python test_bili.py              # offline, no network
python test_bili.py --online     # also verifies the live API
```

| Group | Coverage |
|---|---|
| 1 | Link parsing — 20 forms including short links, `?p=` params, garbage and oversized input |
| 2 | Filename sanitizing, including Windows reserved names and illegal characters |
| 3 | Formatting function boundaries |
| 4 | QR encoding — finder patterns, timing patterns, version info, terminal rendering |
| 5 | Block and error-correction parameter consistency, verified against per-version capacities |
| 6 | API response parsing — merged streams, DASH, error codes, network failures |
| 7 | CLI interface, including exit codes and error messages |
| 8 | Live API verification |
| 9 | Quality selection — VIP scenarios, silent downgrade, DASH track picking |
| 10 | Argument precedence and defaults |

### How the QR encoder is verified

The QR encoder is implemented from scratch in pure Python, with no third-party
dependency. It is **not** verified by comparing matrices against another library —
different implementations may pick different masks and both are valid, so that
comparison proves nothing.

Instead, the generated code is rendered to pixels and handed to **OpenCV's
independent decoder** to read back and compare.

```bash
python -m pip install opencv-python-headless numpy
python test_qr.py
```

Only a code that an independent decoder can actually read is considered correct.

---

## Known limitations

- **Phone/SMS login will most likely not work**, because the official endpoint demands
  human verification. See the explanation above. QR login is the supported path.
- **Paid series and members-only content are not downloaded.** This is intentional,
  not a bug.
- **Not every video can be downloaded.** Creator-disabled downloads, private videos
  and region restrictions will all fail.
- The tool uses BiliBili's public web endpoints. If those change, the tool will need
  updating.

---

## FAQ

**I logged in, but I'm still only getting 1080p. Why?**

The quality ceiling depends on both your account's permissions and the video itself.

- If the video was only ever uploaded in 1080p, nobody can get more than that
- If the video has 4K but it requires a premium membership and you don't have one,
  1080p is as high as it goes

The tool reports both what you actually got and what your account is allowed.
When you see "this account can get up to XXXX for this video", that XXXX is your ceiling.

**My phone says login succeeded but the terminal is still waiting?**

You still need to tap "confirm login" in the mobile app. The QR code stays valid
for a while, so there's no rush.

**It says "failed to render QR code" — now what?**

It doesn't matter. The link is printed right below the QR code. Send it to your
phone's browser and open it — same result.

**The download got interrupted halfway. What now?**

Just re-run the same command. Progress is tracked in a `.part` file and it resumes
from where it stopped. During download the file is named `xxx.mp4.part` and only
renamed to `xxx.mp4` once complete — so if you see `.part`, it isn't finished.

**Can it download paid series or members-only content?**

No, and that's intentional. The tool does not bypass any paywall or permission check.

**Why does it sometimes say ffmpeg is needed?**

For a few videos BiliBili doesn't offer a pre-merged MP4, only separate DASH tracks.
The tool downloads the video and audio tracks separately and looks for ffmpeg to merge
them. If ffmpeg isn't installed it doesn't error out — both files are kept and you can
load them in a player.

**Why is there `[P1]` in some filenames?**

Multi-part videos get a per-part name, otherwise the parts would overwrite each other.

**Will my account get banned?**

The tool does no cracking, and its request pattern matches the web player.
But **do not scrape at high frequency** — that can trip risk control regardless of
what tool you use. Normal personal viewing volume is fine.

---

## Changelog

### v1.0

First release.

- Link parsing: BV ids, av ids, full URLs, b23.tv short links, part parameters
- QR login with the QR code drawn in the terminal, backed by a pure-Python encoder
- Phone/SMS login and manual cookie login
- Automatic selection of the highest quality your account is allowed
- Resume support, multi-segment merging, skip-if-exists
- Uses pre-merged MP4 by default, which is why ffmpeg is not needed
- 216 tests, including cross-verification of the QR encoder with an independent decoder

---

## License

MIT License — see [LICENSE](LICENSE).

This project is for learning and personal use. Users are responsible for their own
usage and must comply with local laws and BiliBili's Terms of Service.
