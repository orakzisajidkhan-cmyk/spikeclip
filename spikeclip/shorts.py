"""Vertical Short builder (1080x1920, 30fps).

Layout, proven across 20+ published Shorts:

* Facecam on top: the streamer's camera box, cropped and scaled to
  1080x780 at y=0. The crop is per-channel configuration — measure it
  from a frame of that channel's broadcast, don't guess it (e.g. a
  bottom-left camera is a different box than a top-left one).
* Gameplay below: a centre crop of the 16:9 frame (default
  590x437 @ (330,141), which excludes the minimap, chat panel and HUD
  on a typical broadcast), scaled to 1080x800 at y=780.
* Big bold white captions with a thick black outline, synced to the
  words, over a black band at the bottom. Burned captions and the .srt
  are censored (F*** / sh*t style); the original audio is untouched.
* Yellow hook text for the first 3 seconds.

Without a facecam crop the builder falls back to a plain full-frame
vertical cut with the same captions and hook.

Caption cues are built automatically from faster-whisper word
timestamps: words are grouped into at most 2 lines of ~30 characters,
split on pauses over 0.7s or cues longer than ~4.5s.
"""

import os
import subprocess

from . import video

DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEFAULT_GAMEPLAY_CROP = "590:437:330:141"  # w:h:x:y in the 1280x720 frame

# Profanity censored in burned captions and the .srt (audio is untouched).
# Value = censored replacement, first letter kept.
CENSOR = {
    "fuck": "f***",
    "fucking": "f***ing",
    "fucked": "f***ed",
    "motherfucker": "m***********",
    "shit": "sh*t",
    "bitch": "b****",
    "bastard": "b******",
    "dick": "d***",
    "pussy": "p****",
    "cunt": "c***",
}


def censor(text):
    """Censor profanity word-by-word (case-insensitive, keeps punctuation)."""
    import re

    def repl(m):
        word = m.group(0)
        low = word.lower()
        if low in CENSOR:
            rep = CENSOR[low]
            return rep.capitalize() if word[:1].isupper() else rep
        return word

    return re.sub(r"[A-Za-z']+", repl, text)


def _wrap(words, max_line=30):
    """Greedy word-wrap into lines of at most max_line characters."""
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip() if cur else w
        if len(trial) <= max_line:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def group_words(words, max_line=30, max_gap=0.7, max_dur=4.5):
    """Group (start, end, word) tuples into caption cues.

    Returns [(start, end, srt_text, burned_lines)] where burned_lines
    is the text with newlines for the drawtext overlay.
    """
    cues = []
    cur = []

    def flush(chunk):
        if not chunk:
            return
        text = " ".join(w[2] for w in chunk)
        lines = _wrap(text.split(), max_line)
        cues.append((chunk[0][0], chunk[-1][1], censor(text),
                     "\n".join(censor(line) for line in lines)))

    for w in words:
        if cur:
            gap = w[0] - cur[-1][1]
            dur = w[1] - cur[0][0]
            trial_lines = _wrap([x[2] for x in cur] + [w[2]], max_line)
            if gap > max_gap or dur > max_dur or len(trial_lines) > 2:
                flush(cur)
                cur = [w]
                continue
        cur.append(w)
    flush(cur)
    return cues


def _wrap_hook(hook, max_line=18):
    """Wrap a hook into lines that fit 1080px at fontsize 68.

    A single long line overflows the frame (verified: "SPEED MEETS THE
    RED HULK" at 68pt clips at both edges). Greedy word-wrap to ~18
    characters per line; a hook that already contains newlines is kept
    as-is (each line wrapped separately).
    """
    out = []
    for part in hook.split("\n"):
        out.extend(_wrap(part.split(), max_line) or [""])
    return "\n".join(out)


def _esc(s):
    return (s.replace("\\", "\\\\").replace(":", "\\:")
             .replace(",", "\\,").replace("'", "\\'").replace("%", "\\%"))
    return (s.replace("\\", "\\\\").replace(":", "\\:")
             .replace(",", "\\,").replace("'", "\\'").replace("%", "\\%"))


def _srt_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def build_short(src, start, dur, out_mp4, out_srt=None,
                facecam_crop=None, gameplay_crop=DEFAULT_GAMEPLAY_CROP,
                hook=None, whisper_model="tiny", no_captions=False,
                black_bar=False, font=DEFAULT_FONT):
    """Build one vertical Short from ``src`` starting at ``start``.

    Args:
        src: source video (landscape broadcast / VOD cut).
        start: seconds into ``src`` the Short starts.
        dur: Short duration in seconds (30-60 typical).
        out_mp4: output path.
        out_srt: subtitle path (default: ``out_mp4`` with .srt).
        facecam_crop: "w:h:x:y" of the streamer's camera in the source
            frame, or None for a plain full-frame vertical cut.
        gameplay_crop: "w:h:x:y" of the gameplay area in the source
            frame (facecam mode only).
        hook: yellow hook text for the first 3s (use a real newline
            for a two-line hook), or None.
        whisper_model: faster-whisper model (tiny is fast on CPU,
            base is slower/better).
        no_captions: skip transcription and captions entirely.
        black_bar: draw a black bar over the bottom of the gameplay
            window, hiding in-game subtitle text there.
        font: TTF font for hook and captions.

    Returns {"mp4": ..., "srt": ...|None, "cues": n}.
    """
    if not os.path.isfile(font):
        raise RuntimeError(
            f"font not found: {font} (install DejaVu Sans Bold, or pass "
            "--font with a .ttf path)")
    start, dur = float(start), float(dur)
    if out_srt is None:
        out_srt = os.path.splitext(out_mp4)[0] + ".srt"

    cues = []
    if not no_captions:
        # Transcribe just this segment's audio (fast: no video encode),
        # so word timestamps are relative to the Short's start.
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav",
                                         delete=False) as tmp:
            tmp_audio = tmp.name
        try:
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y",
                 "-ss", f"{start:.2f}", "-i", src, "-t", f"{dur:.2f}",
                 "-vn", "-c:a", "pcm_s16le", tmp_audio], check=True)
            words = video.transcribe_words(tmp_audio, whisper_model)
            words = [(max(0.0, s), min(dur, e), w)
                     for (s, e, w) in words if e > 0 and s < dur]
            cues = group_words(words)
        finally:
            try:
                os.unlink(tmp_audio)
            except OSError:
                pass

    draws = []
    if hook:
        hook = _wrap_hook(hook)
        hook_y = 810 if facecam_crop else 80
        draws.append(
            f"drawtext=fontfile={font}:text='{_esc(hook)}':fontsize=68:"
            f"fontcolor=yellow:borderw=5:bordercolor=black:"
            f"x=(w-text_w)/2:y={hook_y}:line_spacing=8:"
            f"enable='between(t,0,3)'"
        )
    for s, e, _txt, burned in cues:
        # drawtext quoting breaks on apostrophes — burned text drops them.
        burned = burned.replace("'", "")
        lines = burned.count("\n") + 1
        # 3-line captions sit higher so they don't clip off the bottom.
        y = 1720 if lines == 1 else (1660 if lines == 2 else 1570)
        draws.append(
            f"drawtext=fontfile={font}:text='{_esc(burned)}':fontsize=64:"
            f"fontcolor=white:borderw=5:bordercolor=black:"
            f"x=(w-text_w)/2:y={y}:line_spacing=10:"
            f"enable='between(t,{s:.2f},{e:.2f})'"
        )

    if facecam_crop:
        fc = (
            f"[0:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS,"
            f"fps=30,split=2[fc][gp];"
            f"[fc]crop={facecam_crop},scale=1080:780:flags=lanczos[fc2];"
            f"[gp]crop={gameplay_crop},scale=1080:800:flags=lanczos[gp2];"
            f"color=black:1080x1920:d={dur}[bg];"
            f"[bg][fc2]overlay=0:0[tmp];[tmp][gp2]overlay=0:780"
        )
        if black_bar:
            # Hide in-game subtitle text at the bottom of the gameplay
            # window (y 1430-1580 of the 1920-tall frame).
            fc += ",drawbox=x=0:y=1430:w=1080:h=150:color=black:t=fill"
    else:
        fc = (
            f"[0:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS,"
            f"fps=30,crop=ih*9/16:ih,scale=1080:1920:flags=lanczos"
        )
    if draws:
        fc += "," + ",".join(draws)
    fc += "[vout]"

    cmd = [
        "ffmpeg", "-loglevel", "error", "-y", "-i", src,
        "-filter_complex", fc,
        "-map", "[vout]",
        "-map", "0:a",
        "-af", f"atrim=start={start}:duration={dur},asetpts=PTS-STARTPTS",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "128k", "-t", str(dur), out_mp4,
    ]
    subprocess.run(cmd, check=True)

    srt_path = None
    if cues:
        with open(out_srt, "w", encoding="utf-8") as f:
            for i, (s, e, txt, _b) in enumerate(cues, 1):
                f.write(f"{i}\n{_srt_time(s)} --> {_srt_time(e)}\n"
                        f"{txt}\n\n")
        srt_path = out_srt
    return {"mp4": out_mp4, "srt": srt_path, "cues": len(cues)}
