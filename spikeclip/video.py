"""ffmpeg / faster-whisper helpers: probe, cut, transcribe, burn captions."""

import os
import subprocess


def probe_duration(path):
    """Duration in seconds (ffprobe)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {out.stderr.strip()}")
    return float(out.stdout.strip())


def probe_video(path):
    """Return {width, height, duration} or None if ffprobe fails."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    parts = out.stdout.strip().split(",")
    return {"width": int(parts[0]), "height": int(parts[1]),
            "duration": float(parts[2]) if len(parts) > 2 and parts[2]
            else None}


def cut_clip(video_path, start, length, out_path, width=1080, height=1920,
             crop=True):
    """Cut one vertical (or landscape) clip with ffmpeg.

    ``crop=True`` centre-crops the 16:9 frame to 9:16 and scales to
    ``width``x``height``; ``crop=False`` scales the full frame in and
    pads it to the target size. Returns (ok, stderr).
    """
    start = max(0.0, start)
    if crop:
        vf = f"crop=ih*9/16:ih,scale={width}:{height}"
    else:
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
              f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-ss", f"{start:.2f}", "-i", video_path,
           "-t", f"{length:.2f}", "-vf", vf,
           "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0, proc.stderr.strip()


def _srt_time(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe_clip(clip_path, srt_path, model_name="tiny", language="en"):
    """Transcribe with faster-whisper (local, no cloud key); write .srt.

    Returns the number of segments written. The Whisper model is
    downloaded once from Hugging Face on first use and cached locally.
    """
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(clip_path, language=language,
                                       beam_size=1, vad_filter=True)
    segments = list(segments)
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n{_srt_time(seg.start)} --> {_srt_time(seg.end)}\n"
                    f"{seg.text.strip()}\n\n")
    return len(segments)


def transcribe_words(clip_path, model_name="tiny", language="en"):
    """Transcribe with word-level timestamps; return [(start, end, word)].

    Used by the Short builder to lay out its own caption cues.
    """
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(clip_path, language=language,
                                       beam_size=1, vad_filter=True,
                                       word_timestamps=True)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            text = w.word.strip()
            if text:
                words.append((float(w.start), float(w.end), text))
    return words


def burn_captions(clip_path, srt_path, out_path):
    """Burn a .srt into the clip with ffmpeg's subtitles filter.

    Returns (ok, stderr).
    """
    esc = srt_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf = (f"subtitles='{esc}':force_style="
          f"'FontSize=22,PrimaryColour=&HFFFFFF&,OutlineColour=&H80000000&,"
          f"BorderStyle=1,Outline=2,Alignment=2,MarginV=90'")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", clip_path,
           "-vf", vf, "-c:a", "copy", out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0, proc.stderr.strip()


def caption_clip(clip_path, model_name="tiny", burn=True):
    """Transcribe + .srt + (optionally) a burned-caption copy.

    Returns {"srt": ..., "segments": n, "captioned": path|None,
    "burn_ok": bool|None}.
    """
    base, _ext = os.path.splitext(clip_path)
    srt_path = base + ".srt"
    nseg = transcribe_clip(clip_path, srt_path, model_name)
    result = {"srt": srt_path, "segments": nseg, "captioned": None,
              "burn_ok": None}
    if burn:
        cap_path = base + "_captioned.mp4"
        ok, err = burn_captions(clip_path, srt_path, cap_path)
        result["captioned"] = cap_path if ok else None
        result["burn_ok"] = ok
        result["burn_err"] = err if not ok else ""
    return result
