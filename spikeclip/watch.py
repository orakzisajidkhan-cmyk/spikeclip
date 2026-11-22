"""Live watcher: poll until a channel is live, record it, count chat
spikes, cut and caption a clip around each spike.

Flow:
  1. Poll Twitch until the channel goes live (streamlink --json).
  2. Record with streamlink to recordings/<channel>-<timestamp>.ts
     (MPEG-TS: ffmpeg can read it while it is still being written).
  3. Read chat anonymously over secure WebSocket (see chat.py) and
     count PRIVMSG per 10s window.
  4. A window is a spike at >= min_count messages AND >= min_ratio x
     the median of the previous 5 minutes. Spikes closer than min_gap
     are de-duplicated (strongest kept).
  5. 15s after the spike, cut ``clip_length`` seconds around it
     (``pre_roll`` before + the rest after) from the live recording:
     vertical 1080x1920 by default, with faster-whisper captions
     (.srt + burned-in copy).
  6. ``--short`` builds the full facecam-top Short layout instead
     (facecam crop from the channel's config entry).
  7. ``--longform`` switches to 15-minute landscape clips (.srt only).

Stop gracefully with Ctrl-C so pending clips can finish cutting; a
hard kill can cut a clip short and lose queued spikes.
"""

import json
import os
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime

from . import shorts, video
from .chat import ChatReader
from .spikes import DEFAULTS


def streamlink_path():
    """streamlink from the venv next to this package, else PATH."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        ".venv", "bin", "streamlink")
    here = os.path.normpath(here)
    return here if os.path.isfile(here) else "streamlink"


def check_live(channel, timeout=30):
    """Return (is_live, detail). Uses streamlink --json, no login needed."""
    url = f"https://www.twitch.tv/{channel}"
    try:
        proc = subprocess.run(
            [streamlink_path(), "--http-timeout", "60", "--json", url],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "streamlink timed out"
    except FileNotFoundError:
        return False, "streamlink not found (run install.sh first)"
    text = (proc.stdout or "").strip()
    if not text:
        err = (proc.stderr or "").strip().splitlines()
        return False, "; ".join(err[-2:]) if err else "no output from streamlink"
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        return False, f"unparsable streamlink output: {text[:120]}"
    streams = info.get("streams") or {}
    if streams:
        quals = ", ".join(sorted(streams.keys()))
        return True, f"live, qualities: {quals}"
    err = info.get("error") or ""
    return False, err or "offline (no streams)"


class ClipWatcher:
    def __init__(self, args, channel_cfg=None):
        self.args = args
        self.channel = args.channel
        self.channel_cfg = channel_cfg or {}
        self.out_dir = args.out_dir
        self.clips_dir = os.path.join(self.out_dir, "clips")
        self.rec_dir = os.path.join(self.out_dir, "recordings")
        os.makedirs(self.clips_dir, exist_ok=True)
        os.makedirs(self.rec_dir, exist_ok=True)
        self.spikes = []
        self.clips = []
        self.pending = []  # (spike_time, count, ratio)
        self.last_kept_spike = None
        self.lock = threading.Lock()
        self.stream_start = None
        self.rec_path = None
        self.rec_proc = None
        self.stop_flag = threading.Event()
        self.n_clip = 0
        self._chat = None

    def _log_spikes(self):
        path = os.path.join(self.clips_dir, "spikes.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"channel": self.channel, "spikes": self.spikes,
                       "clips": self.clips}, f, indent=2)

    def on_window_closed(self, idx, count):
        with self.lock:
            counts = list(self._chat.counts) if self._chat else []
        prev = counts[max(0, idx - self.args.baseline_win):idx]
        baseline = statistics.median(prev) if prev else 0.0
        is_spike = False
        ratio = None
        if count >= self.args.min_count:
            if baseline <= 0:
                is_spike = count >= max(self.args.min_count, 50)
            else:
                ratio = count / baseline
                is_spike = ratio >= self.args.min_ratio
        spike_note = ""
        if is_spike:
            spike_note = (f"  <-- SPIKE {ratio:.1f}x" if ratio is not None
                          else "  <-- SPIKE (baseline 0)")
        print(f"[chat] window {idx} ({idx * self.args.window}s): "
              f"{count} msgs, baseline {baseline:.1f}" + spike_note,
              flush=True)
        if not is_spike:
            return
        spike_time = idx * self.args.window + self.args.window // 2
        with self.lock:
            if (self.last_kept_spike is not None
                    and spike_time - self.last_kept_spike < self.args.min_gap):
                print(f"[spike] skipped (within {self.args.min_gap}s of "
                      f"last kept spike)", flush=True)
                return
            self.last_kept_spike = spike_time
            spike = {"window_start": idx * self.args.window,
                     "spike_time": spike_time, "count": count,
                     "ratio": round(ratio, 1) if ratio else None,
                     "baseline": baseline}
            self.spikes.append(spike)
            self.pending.append(spike)
            self._log_spikes()
        print(f"[spike] queued clip for spike at {spike_time}s "
              f"({count} msgs)", flush=True)

    def _clip_worker(self):
        """Waits for each spike's post-roll, then cuts + captions it."""
        while not self.stop_flag.is_set() or self.pending:
            with self.lock:
                # Wait until the clip's actual END exists in the
                # recording: end = max(0, spike - pre_roll) + clip_length.
                due = [s for s in self.pending
                       if (time.monotonic() - self.stream_start)
                       >= max(0.0, s["spike_time"] - self.args.pre_roll)
                       + self.args.clip_length + 2]
                for s in due:
                    self.pending.remove(s)
            if not due:
                if self.stop_flag.is_set() and not self.pending:
                    break
                time.sleep(2)
                continue
            for spike in due:
                self._make_clip(spike)
        # drain anything left once the stream ends
        with self.lock:
            rest = list(self.pending)
            self.pending.clear()
        for spike in rest:
            self._make_clip(spike, final=True)

    def _make_clip(self, spike, final=False):
        start = max(0.0, spike["spike_time"] - self.args.pre_roll)
        self.n_clip += 1
        name = f"clip_{self.n_clip:03d}_spike-{int(spike['spike_time'])}s.mp4"
        out_path = os.path.join(self.clips_dir, name)
        print(f"[cut] {name} (start {start:.0f}s, "
              f"{self.args.clip_length:.0f}s)", flush=True)
        entry = {"file": out_path, "spike_time": spike["spike_time"],
                 "start": start, "count": spike["count"],
                 "ratio": spike.get("ratio"), "ok": False}
        if getattr(self.args, "short", False):
            # Full Short layout (facecam top + gameplay bottom) from
            # the channel's configured crops; transcription happens
            # inside the builder.
            try:
                res = shorts.build_short(
                    self.rec_path, start, self.args.clip_length, out_path,
                    facecam_crop=self.channel_cfg.get("facecam_crop"),
                    gameplay_crop=(self.channel_cfg.get("gameplay_crop")
                                   or shorts.DEFAULT_GAMEPLAY_CROP),
                    hook=self.channel_cfg.get("hook"),
                    whisper_model=self.args.whisper_model,
                    no_captions=self.args.no_captions,
                    black_bar=bool(self.channel_cfg.get("black_bar")))
                entry.update({"ok": True, "srt": res["srt"],
                              "caption_segments": res["cues"],
                              "layout": "short"})
                print(f"[short] {res['cues']} caption cues -> "
                      f"{os.path.basename(res['srt'] or out_path)}",
                      flush=True)
            except Exception as e:  # noqa: BLE001 - report and move on
                print(f"[short] FAILED {name}: {e}", flush=True)
                entry["caption_error"] = str(e)
        else:
            ok, err = video.cut_clip(
                self.rec_path, start, self.args.clip_length, out_path,
                self.args.width, self.args.height,
                crop=not getattr(self.args, "no_crop", False))
            if not ok:
                print(f"[cut] FAILED {name}: {err}", flush=True)
                entry["cut_error"] = err
                with self.lock:
                    self.clips.append(entry)
                    self._log_spikes()
                return
            info = video.probe_video(out_path)
            entry.update({"ok": True,
                          "duration": info["duration"] if info else None,
                          "resolution": (f"{info['width']}x{info['height']}"
                                         if info else None)})
            if not self.args.no_captions:
                print(f"[whisper] transcribing {name} "
                      f"(model {self.args.whisper_model})...", flush=True)
                try:
                    # Long-form: .srt only by default (burning a 15-min
                    # 1080p clip re-encodes it and would block the next
                    # clip's cut for minutes); --burn forces it.
                    burn = getattr(self.args, "burn", False) or \
                        not self.args.no_burn
                    cap = video.caption_clip(out_path,
                                             self.args.whisper_model,
                                             burn=burn)
                    entry.update({"srt": cap["srt"],
                                  "caption_segments": cap["segments"],
                                  "captioned": cap["captioned"],
                                  "burn_ok": cap["burn_ok"]})
                    print(f"[whisper] {cap['segments']} caption segments "
                          f"-> {os.path.basename(cap['srt'])}"
                          + (f", burned {os.path.basename(cap['captioned'])}"
                             if cap["captioned"] else ", burn skipped"),
                          flush=True)
                except Exception as e:  # noqa: BLE001 - report and move on
                    print(f"[whisper] FAILED: {e}", flush=True)
                    entry["caption_error"] = str(e)
        with self.lock:
            self.clips.append(entry)
            self._log_spikes()
        print(f"[done] {out_path}", flush=True)

    def run(self):
        print(f"spikeclip watch: channel={self.channel} "
              f"quality={self.args.quality} out={self.out_dir}")
        deadline = None
        if getattr(self.args, "timeout", 0):
            deadline = time.monotonic() + self.args.timeout
        while True:
            live, detail = check_live(self.channel)
            print(f"[live-check] {self.channel}: "
                  f"{'LIVE' if live else 'offline'} — {detail}", flush=True)
            if live:
                break
            if self.args.check_only:
                return 0
            if deadline and time.monotonic() >= deadline:
                print("[watch] timeout reached while offline — exiting.",
                      flush=True)
                return 0
            print(f"[wait] polling again in {self.args.poll_interval}s "
                  f"(Ctrl-C to stop)...", flush=True)
            time.sleep(self.args.poll_interval)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.rec_path = os.path.join(
            self.rec_dir, f"{self.channel}-{stamp}.ts")
        print(f"[record] streamlink -> {self.rec_path}", flush=True)
        self.stream_start = time.monotonic()
        self.streamlink_log_path = os.path.join(self.out_dir,
                                                "streamlink.log")
        self.streamlink_log = open(self.streamlink_log_path, "a",
                                   encoding="utf-8")
        self.rec_proc = subprocess.Popen(
            [streamlink_path(), "--http-timeout", "60",
             f"https://www.twitch.tv/{self.channel}",
             self.args.quality, "-o", self.rec_path],
            stdout=subprocess.DEVNULL, stderr=self.streamlink_log)
        # wait for the recording file to appear before counting chat
        for _ in range(30):
            if os.path.exists(self.rec_path) and \
                    os.path.getsize(self.rec_path) > 0:
                break
            if self.rec_proc.poll() is not None:
                break
            time.sleep(1)
        if self.rec_proc.poll() is not None:
            print(f"[record] streamlink exited early (code "
                  f"{self.rec_proc.returncode}) — see "
                  f"{self.streamlink_log_path}", flush=True)
        self._chat = ChatReader(self.channel, self.args.window,
                                self.stream_start, self.on_window_closed)
        self._chat.start()
        worker = threading.Thread(target=self._clip_worker, daemon=True)
        worker.start()
        print("[watch] recording + chat counting running. "
              "Ctrl-C to stop.", flush=True)
        try:
            while self.rec_proc.poll() is None:
                if deadline and time.monotonic() >= deadline:
                    print("[watch] timeout reached — stopping.",
                          flush=True)
                    break
                time.sleep(5)
            else:
                print("[record] stream ended (streamlink exited).",
                      flush=True)
        except KeyboardInterrupt:
            print("\n[watch] stopped by user.", flush=True)
        finally:
            self.stop_flag.set()
            self._chat.stop_flag.set()
            if self.rec_proc.poll() is None:
                self.rec_proc.terminate()
                try:
                    self.rec_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.rec_proc.kill()
            worker.join(timeout=max(600, self.args.clip_length + 300))
            self._chat.join(timeout=10)
            try:
                self.streamlink_log.close()
            except Exception:
                pass
            self._log_spikes()
        print(f"[done] {len(self.spikes)} spikes, "
              f"{sum(1 for c in self.clips if c['ok'])} clips -> "
              f"{self.clips_dir}/")
        return 0
