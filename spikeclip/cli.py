"""SpikeClip CLI.

    spikeclip watch <channel> [--short] [--longform] ...
    spikeclip vod <source> --start S --end E [--short] ...
    spikeclip spikes <chat-log> [--video VOD] [--list-only] ...

Run `spikeclip <subcommand> --help` for the full flag list.
Config file (spikeclip.yaml) sets the defaults; flags override it.
"""

import argparse
import os
import sys

from . import __version__, config
from .spikes import DEFAULTS as SPIKE_DEFAULTS


def _spike_flags(ap):
    ap.add_argument("--window", type=float,
                    default=SPIKE_DEFAULTS["window"],
                    help="Seconds per chat-counting window (default 10)")
    ap.add_argument("--min-ratio", type=float,
                    default=SPIKE_DEFAULTS["min_ratio"],
                    help="Spike if count >= this x rolling baseline "
                         "(default 2.0)")
    ap.add_argument("--min-count", type=int,
                    default=SPIKE_DEFAULTS["min_count"],
                    help="Minimum messages in a window (default 10)")
    ap.add_argument("--baseline-win", type=int,
                    default=SPIKE_DEFAULTS["baseline_win"],
                    help="Preceding windows forming the baseline "
                         "(default 30 = 5 min)")
    ap.add_argument("--min-gap", type=float,
                    default=SPIKE_DEFAULTS["min_gap"],
                    help="Min seconds between kept spikes (default 60)")


def _clip_flags(ap):
    ap.add_argument("--pre-roll", type=float, default=15,
                    help="Seconds before the spike the clip starts "
                         "(default 15)")
    ap.add_argument("--clip-length", type=float, default=30,
                    help="Clip duration in seconds (default 30)")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--whisper-model", default="tiny",
                    help="faster-whisper model (default tiny; base is "
                         "slower/better)")
    ap.add_argument("--no-captions", action="store_true",
                    help="Skip Whisper captioning")
    ap.add_argument("--no-burn", action="store_true",
                    help="Write .srt only, do not burn captions in")
    ap.add_argument("--burn", action="store_true",
                    help="Force a burned-caption copy even in --longform")


def _short_flags(ap):
    ap.add_argument("--facecam-crop", default=None,
                    help='Facecam box "w:h:x:y" in the source frame '
                         '(Short layout only)')
    ap.add_argument("--gameplay-crop", default=None,
                    help='Gameplay box "w:h:x:y" (default 590:437:330:141)')
    ap.add_argument("--hook", default=None,
                    help="Yellow hook text for the first 3s of the Short")
    ap.add_argument("--black-bar", action="store_true",
                    help="Black out the bottom of the gameplay window "
                         "(hides in-game subtitle text)")


def _apply_config(args, cfg, keys):
    """Fill in args from the config file where the user gave no flag.

    Only keys the user did not pass on the command line are filled.
    """
    explicit = {a.split("=")[0] for a in sys.argv[1:] if a.startswith("--")}
    for key in keys:
        flag = "--" + key.replace("_", "-")
        if flag in explicit:
            continue
        if key in cfg and getattr(args, key, None) is not None:
            # argparse already filled a default; only override when the
            # config value differs from the builtin default.
            if getattr(args, key) == _builtin_default(key):
                setattr(args, key, cfg[key])


def _builtin_default(key):
    defaults = {
        "quality": "best", "window": 10, "min_ratio": 2.0, "min_count": 10,
        "baseline_win": 30, "pre_roll": 15, "clip_length": 30,
        "min_gap": 60, "width": 1080, "height": 1920,
        "whisper_model": "tiny", "poll_interval": 30, "out_dir": ".",
    }
    return defaults.get(key)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="spikeclip",
        description="Free, local Twitch clipper: watch live chat, catch "
                    "the spikes, cut vertical Shorts with captions.")
    ap.add_argument("--version", action="version",
                    version=f"spikeclip {__version__}")
    ap.add_argument("--config", default=None,
                    help="YAML config file (default: ./spikeclip.yaml "
                         "if it exists)")
    sub = ap.add_subparsers(dest="command", required=True)

    # -- watch ------------------------------------------------------------
    w = sub.add_parser("watch",
                       help="Watch a channel live: record, count chat "
                            "spikes, cut + caption clips")
    w.add_argument("channel", help="Twitch channel name")
    w.add_argument("--out-dir", default=".",
                   help="Working folder (recordings/ + clips/ inside it)")
    w.add_argument("--quality", default="best",
                   help="streamlink quality (default best; e.g. 720p60)")
    _spike_flags(w)
    _clip_flags(w)
    w.add_argument("--no-crop", action="store_true",
                   help="Keep the native landscape frame instead of "
                        "centre-cropping to vertical")
    w.add_argument("--longform", action="store_true",
                   help="15-min LANDSCAPE preset (clip 900s, pre-roll "
                        "450s, 1920x1080, min-gap 900s, .srt only)")
    w.add_argument("--short", action="store_true",
                   help="Build the full facecam-top Short layout instead "
                        "of a plain vertical clip (facecam crop from the "
                        "channel's config entry)")
    w.add_argument("--poll-interval", type=int, default=30,
                   help="Seconds between live checks while offline "
                        "(default 30)")
    w.add_argument("--check-only", action="store_true",
                   help="Check live/offline once and exit (no recording)")
    w.add_argument("--timeout", type=float, default=0,
                   help="Stop watching after N seconds (0 = run until "
                        "the stream ends or Ctrl-C)")

    # -- vod --------------------------------------------------------------
    v = sub.add_parser("vod",
                       help="Clip a time range from a Twitch VOD "
                            "(URL/id) or a local video file")
    v.add_argument("source",
                   help="Twitch VOD URL or id, or a local video file")
    v.add_argument("--start", type=float, required=True,
                   help="Range start in seconds")
    v.add_argument("--end", type=float, required=True,
                   help="Range end in seconds")
    v.add_argument("--out-dir", default=".",
                   help="Folder for the output clip")
    v.add_argument("--quality", default="720p60",
                   help="VOD download quality (default 720p60)")
    v.add_argument("--twitchdownloader-path", default=None,
                   help="Path to TwitchDownloaderCLI (else searched on "
                        "PATH)")
    v.add_argument("--width", type=int, default=1080)
    v.add_argument("--height", type=int, default=1920)
    v.add_argument("--whisper-model", default="tiny")
    v.add_argument("--no-captions", action="store_true")
    v.add_argument("--no-burn", action="store_true")
    v.add_argument("--no-crop", action="store_true")
    v.add_argument("--short", action="store_true",
                   help="Build the vertical Short layout (facecam-top) "
                        "instead of a plain clip")
    _short_flags(v)

    # -- spikes -----------------------------------------------------------
    s = sub.add_parser("spikes",
                       help="Find chat spikes in a saved chat log "
                            "(TwitchDownloader chat.json)")
    s.add_argument("chat_log", help="Chat JSON file")
    s.add_argument("--video", default=None,
                   help="VOD video file to cut clips from (omit with "
                        "--list-only)")
    s.add_argument("--out-dir", default="clips",
                   help="Folder for cut clips (default: clips)")
    _spike_flags(s)
    _clip_flags(s)
    s.add_argument("--no-crop", action="store_true",
                   help="Keep the 16:9 frame instead of vertical crop")
    s.add_argument("--list-only", action="store_true",
                   help="Print spikes only, do not cut clips")
    s.add_argument("--max-clips", type=int, default=0,
                   help="Cap on clips to cut (0 = no cap)")
    return ap


def _longform_preset(args):
    """Apply the --longform preset; explicit flags win over the preset."""
    explicit = {a.split("=")[0] for a in sys.argv[1:] if a.startswith("--")}
    if "--clip-length" not in explicit:
        args.clip_length = 900
    if "--pre-roll" not in explicit:
        args.pre_roll = 450
    if "--width" not in explicit:
        args.width = 1920
    if "--height" not in explicit:
        args.height = 1080
    if "--min-gap" not in explicit:
        args.min_gap = 900
    args.no_crop = True
    if not args.burn:
        args.no_burn = True
    print(f"[longform] 15-min landscape mode: clip={args.clip_length:.0f}s "
          f"pre-roll={args.pre_roll:.0f}s {args.width}x{args.height} "
          f"min-gap={args.min_gap:.0f}s no-crop "
          f"{'(burn forced by --burn)' if args.burn else '(srt only)'}",
          flush=True)


def _run_spikes(args):
    from .spikes import count_windows, find_spikes, load_offsets
    from . import video
    import json
    import statistics

    if not os.path.isfile(args.chat_log):
        print(f"ERROR: chat file not found: {args.chat_log}", file=sys.stderr)
        return 2
    if not args.list_only and (not args.video
                               or not os.path.isfile(args.video)):
        print("ERROR: VOD file not found (needed to cut clips) — "
              "pass --video or use --list-only.", file=sys.stderr)
        return 2

    offsets = load_offsets(args.chat_log)
    if not offsets:
        print("ERROR: no message timestamps found in chat file. "
              "Expected TwitchDownloader JSON with content_offset_seconds.",
              file=sys.stderr)
        return 2

    if args.video:
        duration = video.probe_duration(args.video)
    else:
        duration = offsets[-1] + args.window
    duration = max(duration, offsets[-1] + 1)

    counts = count_windows(offsets, args.window, duration)
    spikes = find_spikes(counts, args.window, args.min_ratio,
                         args.min_count, args.baseline_win, args.min_gap)

    print(f"chat: {len(offsets)} messages over {duration:.0f}s "
          f"({len(counts)} x {args.window:.0f}s windows)")
    print(f"median messages/window: {statistics.median(counts):.1f}  "
          f"max: {max(counts)}")
    print(f"spikes found: {len(spikes)}")
    for i, s in enumerate(spikes, 1):
        ratio = (f"{s['ratio']}x baseline" if s.get("ratio")
                 else "no baseline")
        print(f"  #{i:02d} spike at {s['spike_time']:.0f}s "
              f"(window {s['window_start']:.0f}s, {s['count']} msgs, "
              f"{ratio})")

    if args.list_only:
        return 0

    if args.max_clips:
        spikes = spikes[:args.max_clips]
    os.makedirs(args.out_dir, exist_ok=True)

    results = []
    for i, s in enumerate(spikes, 1):
        start = s["spike_time"] - args.pre_roll
        name = f"clip_{i:03d}_spike-{int(s['spike_time'])}s.mp4"
        out_path = os.path.join(args.out_dir, name)
        ok, err = video.cut_clip(args.video, start, args.clip_length,
                                 out_path, args.width, args.height,
                                 crop=not args.no_crop)
        status = "OK " if ok else "FAIL"
        print(f"  [{status}] {out_path}  (start {max(0.0, start):.0f}s, "
              f"{args.clip_length:.0f}s)")
        if not ok:
            print(f"        ffmpeg: {err}", file=sys.stderr)
        entry = {"file": out_path, "spike_time": s["spike_time"],
                 "start": max(0.0, start), "count": s["count"],
                 "ratio": s.get("ratio"), "ok": ok}
        if ok:
            info = video.probe_video(out_path)
            entry["duration"] = info["duration"] if info else None
            entry["resolution"] = (f"{info['width']}x{info['height']}"
                                   if info else None)
            if not args.no_captions:
                try:
                    cap = video.caption_clip(
                        out_path, args.whisper_model,
                        burn=not args.no_burn)
                    entry.update({"srt": cap["srt"],
                                  "caption_segments": cap["segments"],
                                  "captioned": cap["captioned"],
                                  "burn_ok": cap["burn_ok"]})
                    print(f"        captions: {cap['segments']} segments "
                          f"-> {os.path.basename(cap['srt'])}"
                          + (f" + burned {os.path.basename(cap['captioned'])}"
                             if cap["captioned"] else ""), flush=True)
                except Exception as e:  # noqa: BLE001 - report and move on
                    print(f"        whisper FAILED: {e}", file=sys.stderr)
                    entry["caption_error"] = str(e)
        results.append(entry)

    with open(os.path.join(args.out_dir, "spikes.json"), "w",
              encoding="utf-8") as f:
        json.dump({"spikes": spikes, "clips": results}, f, indent=2)

    done = sum(1 for r in results if r["ok"])
    print(f"done: {done}/{len(results)} clips cut -> {args.out_dir}/")
    return 0 if done == len(results) else 1


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)

    try:
        cfg = config.load_config(args.config)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    keys = ["quality", "window", "min_ratio", "min_count", "baseline_win",
            "pre_roll", "clip_length", "min_gap", "width", "height",
            "whisper_model", "poll_interval", "out_dir"]
    _apply_config(args, cfg, keys)

    if args.command == "watch":
        if getattr(args, "longform", False):
            _longform_preset(args)
        if getattr(args, "check_only", False):
            from .watch import check_live
            live, detail = check_live(args.channel)
            print(f"{args.channel}: {'LIVE' if live else 'offline'} — "
                  f"{detail}")
            return 0
        from .watch import ClipWatcher
        ch_cfg = config.channel_settings(cfg, args.channel)
        watcher = ClipWatcher(args, channel_cfg=ch_cfg)
        return watcher.run()

    if args.command == "vod":
        from .vod import run_vod
        return run_vod(args)

    if args.command == "spikes":
        return _run_spikes(args)

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
