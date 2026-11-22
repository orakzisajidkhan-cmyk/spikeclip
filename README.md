# SpikeClip

**Free, local, open-source live-stream clipper.** Watch a Twitch channel's
live chat, catch the moments chat explodes, and cut them into vertical
1080x1920 Shorts with burned captions and a hook — no accounts, no tokens,
no paid APIs, nothing uploaded to a cloud.

Built from a pipeline that produced 20+ published Shorts: chat spikes
find the moment, streamlink records the stream, ffmpeg cuts it, and a
local faster-whisper model writes the captions.

> **Only clip streamers who allow it.** If a streamer says no clips,
> don't run this on their channel.

## How it works

| Step | Tool | What it does |
|---|---|---|
| Chat | anonymous WebSocket (`wss://irc-ws.chat.twitch.tv:443`) | Reads chat with no account — over port 443, since plain IRC ports 6667/6697 are blocked on some networks |
| Spike detection | `spikeclip.spikes` | Counts messages per 10s window |
| Recording | streamlink 8.6.3 | Records the stream the moment it goes live |
| Cutting | ffmpeg 8.x | Cuts the clip around each spike |
| Captions | faster-whisper 1.2.1 | Local transcription — `.srt` + burned-in captions |

A window is a **spike** when it has **≥10 messages AND ≥2× the median of
the previous 5 minutes**. Spikes closer than 60s are de-duplicated
(strongest kept). Each clip is 30s by default: 15s before the spike +
15s after.

## Requirements

- Python 3.10+
- ffmpeg 8.x on PATH (cutting, captions)
- streamlink (installed by `install.sh`, inside the venv)
- TwitchDownloaderCLI — only for `spikeclip vod` on Twitch VOD URLs
  (https://github.com/lay295/TwitchDownloader). Not needed for live
  watching or local files.

## Install

```bash
git clone https://github.com/orakzisajidkhan-cmyk/spikeclip.git
cd spikeclip
./install.sh        # creates .venv, installs the package + deps
source .venv/bin/activate
spikeclip --help
```

## Quickstart

```bash
# Is the channel live? (no recording)
spikeclip watch somechannel --check-only

# Watch live: waits until live, records, cuts + captions every spike.
# Ctrl-C to stop (pending clips finish cutting first).
spikeclip watch somechannel --out-dir runs/somechannel

# Same, but build the full Short layout (facecam top + gameplay bottom)
# using the channel's crops from spikeclip.yaml:
spikeclip watch somechannel --short --out-dir runs/somechannel

# Quieter stream — lower the bar:
spikeclip watch somechannel --min-count 5 --min-ratio 1.5

# Long-form: 15-minute landscape clips instead of Shorts:
spikeclip watch somechannel --longform --quality 720p60

# Clip a moment from a VOD (needs TwitchDownloaderCLI):
spikeclip vod https://www.twitch.tv/videos/1234567890 --start 11319 --end 11379 --short

# Same, from a local recording:
spikeclip vod ./recording.mp4 --start 600 --end 640 --short --hook "THE COMEBACK"

# Find spikes in a saved chat log (TwitchDownloader chat.json):
spikeclip spikes chat.json --video vod.mp4 --out-dir clips
spikeclip spikes chat.json --list-only
```

While `watch` runs you get:

```
runs/somechannel/
├── recordings/somechannel-20261122-120000.ts  # full stream recording
└── clips/
    ├── clip_001_spike-65s.mp4                # 1080x1920, 30s
    ├── clip_001_spike-65s.srt                # Whisper captions
    ├── clip_001_spike-65s_captioned.mp4      # same clip, captions burned in
    └── spikes.json                           # spike list + clip log
```

## The Short layout (`--short`)

1080x1920, 30fps, built for phone screens:

- **Facecam on top** (1080x780): the streamer's camera box, cropped
  from the broadcast frame. The crop is per-channel configuration —
  measure it from a frame of that channel's broadcast, don't guess it.
  A bottom-left camera is a different box than a top-left one.
- **Gameplay below** (1080x800): a centre crop of the 16:9 frame
  (default `590:437:330:141`), which excludes the minimap, chat panel
  and HUD on a typical broadcast.
- **Big bold white captions** with a thick black outline, synced to
  the words; **yellow hook text** for the first 3 seconds (long hooks
  are auto-wrapped so they never overflow the frame).
- Burned captions and the `.srt` are censored (F\*\*\* / sh\*t style);
  the original audio is untouched.
- `--black-bar` draws a black bar over the bottom of the gameplay
  window, hiding in-game subtitle text there.

Configure crops per channel in `spikeclip.yaml` (copy
`spikeclip.yaml.example`):

```yaml
channels:
  example_channel:
    facecam_crop: "195:141:9:314"    # w:h:x:y in the 1280x720 frame
    gameplay_crop: "590:437:330:141"
    hook: "EXAMPLE HOOK"
```

## Config file

`spikeclip.yaml` in the working directory sets the defaults
(thresholds, clip length, whisper model, output dir, per-channel
crops). Every command-line flag overrides it. See
`spikeclip.yaml.example` for the full documented list.

## Commands

| Command | What it does |
|---|---|
| `spikeclip watch <channel>` | Live: record + spike clips (vertical, captioned) |
| `spikeclip watch <channel> --short` | Live: full Short layout from the channel's config |
| `spikeclip watch <channel> --longform` | Live: 15-min landscape clips, `.srt` only |
| `spikeclip vod <url\|file> --start S --end E` | Clip a VOD/local range (add `--short` for the Short layout) |
| `spikeclip spikes <chat.json> [--video VOD]` | Find spikes in a saved chat log, optionally cut clips |

Common flags: `--min-count` (default 10), `--min-ratio` (default 2.0),
`--window` (default 10s), `--baseline-win` (default 30), `--min-gap`
(default 60s), `--pre-roll` (default 15s), `--clip-length` (default
30s), `--whisper-model` (default `tiny`; `base` is slower/better),
`--no-captions`, `--no-burn`, `--out-dir`, `--config`.

## Tests

```bash
python3 -m unittest discover -s tests -v
# Spike detector against the synthetic fixture chat log:
spikeclip spikes tests/fixtures/chat.json --list-only
# Expect: 1 spike at 65s (90 msgs, 16.4x baseline)
```

## Caveats

- **Live only — it cannot rewind an ended stream.** If the stream is
  over, use `spikeclip vod` or `spikeclip spikes` on the VOD instead.
- **Long streams are big.** A 6-hour recording at 1080p is tens of GB
  — check disk space first (`df -h`). Use `--quality 720p` if space is
  tight.
- **Whisper is CPU-slow-ish**: the `tiny` model transcribes a 30s clip
  in ~15s on a typical CPU; `base` is roughly 2–3x slower. Captions
  run after each clip is cut, so they lag the spike by about a minute.
- **Chat join lags the recording by a few seconds** (WebSocket connect
  + JOIN), so the first windows may undercount slightly.
- **Slow-chat streams spike rarely**: on a quiet stream (~3 msgs/10s)
  the ≥10-msg minimum seldom fires — pick a busy channel or lower
  `--min-count` (e.g. 5).
- If Twitch ever requires auth for anonymous chat, the chat step breaks
  (recording still works).
- **Stop gracefully**: end a run with Ctrl-C so the clip worker can
  drain pending clips; a hard kill can cut a clip short and lose queued
  spikes. `--timeout N` stops a run after N seconds (handy for tests).

## License

MIT — see [LICENSE](LICENSE).
