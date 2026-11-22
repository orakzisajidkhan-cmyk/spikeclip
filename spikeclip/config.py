"""YAML config file support.

A config file sets the defaults; every command-line flag overrides it.
Copy ``spikeclip.yaml.example`` to ``spikeclip.yaml`` and edit it.

Example::

    min_count: 10
    min_ratio: 2.0
    clip_length: 30
    pre_roll: 15
    whisper_model: tiny
    out_dir: runs
    channels:
      example_channel:
        facecam_crop: "195:141:9:314"   # w:h:x:y of the camera box
        gameplay_crop: "590:437:330:141"
"""

import os

DEFAULTS = {
    "quality": "best",
    "window": 10,
    "min_ratio": 2.0,
    "min_count": 10,
    "baseline_win": 30,
    "pre_roll": 15,
    "clip_length": 30,
    "min_gap": 60,
    "width": 1080,
    "height": 1920,
    "whisper_model": "tiny",
    "poll_interval": 30,
    "out_dir": ".",
    "channels": {},
}


def load_config(path=None):
    """Load config from ``path`` (or ./spikeclip.yaml if it exists).

    Returns a dict of settings (DEFAULTS merged with the file).
    Raises a clear error if the file exists but is not valid YAML.
    """
    cfg = dict(DEFAULTS)
    cfg["channels"] = dict(DEFAULTS["channels"])
    if path is None:
        path = "spikeclip.yaml"
        if not os.path.isfile(path):
            return cfg
    if not os.path.isfile(path):
        raise FileNotFoundError(f"config file not found: {path}")
    try:
        import yaml
    except ImportError as e:
        raise RuntimeError(
            "PyYAML is required to load a config file "
            "(pip install pyyaml)") from e
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config file must be a YAML mapping: {path}")
    channels = data.pop("channels", None)
    cfg.update(data)
    if isinstance(channels, dict):
        cfg["channels"] = channels
    return cfg


def channel_settings(cfg, channel):
    """Facecam/gameplay crops for one channel ({} if unconfigured)."""
    channels = cfg.get("channels") or {}
    entry = channels.get(channel.lower()) or {}
    return entry if isinstance(entry, dict) else {}
