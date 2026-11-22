"""Chat-spike detection.

A window is a spike when it has >= ``min_count`` messages AND
>= ``min_ratio`` x the median of the previous ``baseline_win`` windows
(30 x 10s windows = 5 minutes of baseline by default). The median, not
the mean, is used so earlier spikes don't inflate the baseline.

Windows are de-duplicated afterwards: within ``min_gap`` seconds only
the strongest spike is kept.
"""

import json
import math
import statistics

DEFAULTS = dict(
    window=10,
    min_ratio=2.0,
    min_count=10,
    baseline_win=30,  # 30 x 10s = 5 minutes
    min_gap=60,
)


def load_offsets(chat_path):
    """Message offsets (seconds) from a TwitchDownloader-style chat.json.

    Accepts a ``{"comments": [...]}`` wrapper, a ``{"messages": [...]}``
    wrapper, a nested ``{"data": {"comments": [...]}}`` object, or a
    plain list of comment objects. Each comment needs one of
    ``content_offset_seconds`` / ``offset`` / ``created_at_offset`` /
    ``timestamp_offset`` / ``contentOffsetSeconds``.
    """
    with open(chat_path, encoding="utf-8") as f:
        data = json.load(f)

    comments = None
    if isinstance(data, dict):
        comments = data.get("comments") or data.get("messages") or []
        if not comments and isinstance(data.get("data"), dict):
            comments = data["data"].get("comments", [])
    elif isinstance(data, list):
        comments = data

    offsets = []
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        off = None
        for key in ("content_offset_seconds", "offset", "created_at_offset",
                    "timestamp_offset", "contentOffsetSeconds"):
            if key in c and c[key] is not None:
                off = c[key]
                break
        if off is None:
            continue
        try:
            offsets.append(float(off))
        except (TypeError, ValueError):
            continue
    offsets.sort()
    return offsets


def count_windows(offsets, window, duration):
    """Bucket message offsets into fixed windows; return list of counts."""
    n = max(1, int(math.ceil(duration / window)))
    counts = [0] * n
    for off in offsets:
        idx = int(off // window)
        if 0 <= idx < n:
            counts[idx] += 1
    return counts


def find_spikes(counts, window=DEFAULTS["window"],
                min_ratio=DEFAULTS["min_ratio"],
                min_count=DEFAULTS["min_count"],
                baseline_win=DEFAULTS["baseline_win"],
                min_gap=DEFAULTS["min_gap"]):
    """Return spike dicts {window_start, spike_time, count, ratio, baseline}.

    ``spike_time`` is the middle of the spiking window — the moment the
    clip should be centred on.
    """
    spikes = []
    for i, count in enumerate(counts):
        if count < min_count:
            continue
        prev = counts[max(0, i - baseline_win):i]
        baseline = statistics.median(prev) if prev else 0.0
        if baseline <= 0:
            # Stream just started: no baseline yet, so require a big
            # absolute count instead of a ratio.
            if count >= max(min_count, 50):
                spikes.append({"window_start": i * window, "count": count,
                               "ratio": None, "baseline": 0.0})
            continue
        ratio = count / baseline
        if ratio >= min_ratio:
            spikes.append({"window_start": i * window, "count": count,
                           "ratio": round(ratio, 1), "baseline": baseline})

    # De-duplicate: keep the strongest spike within min_gap seconds.
    kept = []
    for s in sorted(spikes, key=lambda x: -x["count"]):
        if all(abs(s["window_start"] - k["window_start"]) >= min_gap
               for k in kept):
            kept.append(s)
    kept.sort(key=lambda x: x["window_start"])
    for s in kept:
        s["spike_time"] = s["window_start"] + window // 2
    return kept
