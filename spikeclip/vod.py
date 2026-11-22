"""VOD mode: clip a time range from a Twitch VOD (or a local file).

For a Twitch VOD URL (or bare VOD id), the range is downloaded with
TwitchDownloaderCLI (``TwitchDownloaderCLI videodownload -u <id> -o
out.mp4 -b <start> -e <end>``) — install it separately, see README.
For a local video file the range is cut straight from the file.

The downloaded/cut range is then either kept as-is (landscape) or
built into a vertical Short with :func:`spikeclip.shorts.build_short`
(``--short``), with faster-whisper captions.
"""

import os
import re
import shutil
import subprocess

from . import shorts, video

VOD_URL_RE = re.compile(r"(?:twitch\.tv/videos/|twitch\.tv/\w+/v/)(\d+)")
VOD_ID_RE = re.compile(r"^\d{6,}$")


def parse_vod_id(source):
    """Return the numeric VOD id from a URL or bare id, or None."""
    m = VOD_URL_RE.search(source)
    if m:
        return m.group(1)
    if VOD_ID_RE.match(source.strip()):
        return source.strip()
    return None


def find_twitchdownloader(explicit=None):
    """Locate the TwitchDownloaderCLI binary (explicit path, else PATH)."""
    if explicit and os.path.isfile(explicit):
        return explicit
    found = shutil.which("TwitchDownloaderCLI")
    return found


def download_vod_range(vod_id, start, end, out_path, quality="720p60",
                       td_path=None):
    """Download VOD ``start``..``end`` (seconds) with TwitchDownloaderCLI.

    Returns (ok, stderr). ``--trim-mode Exact`` keeps the range exact;
    ``--collision Overwrite`` never prompts.
    """
    td = find_twitchdownloader(td_path)
    if not td:
        raise RuntimeError(
            "TwitchDownloaderCLI not found — install it from "
            "https://github.com/lay295/TwitchDownloader and put it on "
            "PATH (or pass --twitchdownloader-path).")
    cmd = [td, "videodownload", "-u", vod_id, "-o", out_path,
           "-b", f"{float(start):.0f}s", "-e", f"{float(end):.0f}s",
           "-q", quality, "--trim-mode", "Exact",
           "--collision", "Overwrite"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def run_vod(args):
    """`spikeclip vod <source> --start S --end E`."""
    start, end = float(args.start), float(args.end)
    if end <= start:
        print("ERROR: --end must be after --start.", flush=True)
        return 2
    os.makedirs(args.out_dir, exist_ok=True)
    vod_id = parse_vod_id(args.source)
    if vod_id:
        raw_path = os.path.join(args.out_dir, f"vod-{vod_id}-{int(start)}-"
                                f"{int(end)}.mp4")
        print(f"[vod] downloading VOD {vod_id} {start:.0f}s..{end:.0f}s "
              f"-> {raw_path}", flush=True)
        ok, err = download_vod_range(
            vod_id, start, end, raw_path, quality=args.quality,
            td_path=getattr(args, "twitchdownloader_path", None))
        if not ok:
            print(f"[vod] download FAILED: {err}", flush=True)
            return 1
        src, src_start = raw_path, 0.0
    elif os.path.isfile(args.source):
        src, src_start = args.source, start
    else:
        print(f"ERROR: not a Twitch VOD URL/id or a local file: "
              f"{args.source}", flush=True)
        return 2

    dur = end - start
    stem = os.path.splitext(os.path.basename(src))[0]
    if getattr(args, "short", False):
        out_mp4 = os.path.join(args.out_dir, f"{stem}-short.mp4")
        print(f"[short] building vertical Short ({dur:.0f}s) -> {out_mp4}",
              flush=True)
        res = shorts.build_short(
            src, src_start, dur, out_mp4,
            facecam_crop=getattr(args, "facecam_crop", None),
            gameplay_crop=(getattr(args, "gameplay_crop", None)
                           or shorts.DEFAULT_GAMEPLAY_CROP),
            hook=getattr(args, "hook", None),
            whisper_model=args.whisper_model,
            no_captions=args.no_captions,
            black_bar=getattr(args, "black_bar", False))
        print(f"[done] {res['mp4']} ({res['cues']} caption cues)"
              + (f" + {res['srt']}" if res["srt"] else ""), flush=True)
    else:
        out_mp4 = os.path.join(args.out_dir, f"{stem}-clip.mp4")
        ok, err = video.cut_clip(src, src_start, dur, out_mp4,
                                 args.width, args.height,
                                 crop=not getattr(args, "no_crop", False))
        if not ok:
            print(f"[cut] FAILED: {err}", flush=True)
            return 1
        print(f"[done] {out_mp4}", flush=True)
        if not args.no_captions:
            cap = video.caption_clip(out_mp4, args.whisper_model,
                                     burn=not args.no_burn)
            print(f"[whisper] {cap['segments']} caption segments -> "
                  f"{os.path.basename(cap['srt'])}"
                  + (f" + burned {os.path.basename(cap['captioned'])}"
                     if cap["captioned"] else ""), flush=True)
    return 0
