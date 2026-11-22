"""Unit tests for the spike detector (stdlib only — no network).

Run:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spikeclip.spikes import count_windows, find_spikes, load_offsets

HERE = os.path.dirname(__file__)


class TestFindSpikes(unittest.TestCase):
    def test_spike_above_baseline(self):
        # 30 quiet windows (5 msgs each), then a 90-msg window.
        counts = [5] * 30 + [90] + [5] * 5
        spikes = find_spikes(counts)
        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0]["window_start"], 300)
        self.assertEqual(spikes[0]["spike_time"], 305)
        self.assertEqual(spikes[0]["count"], 90)
        self.assertEqual(spikes[0]["ratio"], 18.0)

    def test_below_min_count_is_not_a_spike(self):
        counts = [5] * 30 + [9] + [5] * 5  # 9 < min_count 10
        self.assertEqual(find_spikes(counts), [])

    def test_below_ratio_is_not_a_spike(self):
        counts = [20] * 30 + [25] + [20] * 5  # 25 < 2.0 x 20
        self.assertEqual(find_spikes(counts), [])

    def test_zero_baseline_needs_big_absolute_count(self):
        # Stream just started: no baseline yet.
        counts = [0] * 3 + [60] + [0] * 5
        spikes = find_spikes(counts)
        self.assertEqual(len(spikes), 1)
        self.assertIsNone(spikes[0]["ratio"])
        self.assertEqual(find_spikes([0] * 3 + [49] + [0] * 5), [])

    def test_min_gap_keeps_strongest(self):
        # Two spiking windows 30s apart: only the stronger survives.
        counts = [5] * 30 + [80, 5, 5, 60] + [5] * 5
        spikes = find_spikes(counts, min_gap=60)
        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0]["count"], 80)

    def test_custom_thresholds(self):
        counts = [4] * 30 + [12] + [4] * 5
        # 12 msgs is 3x the baseline of 4.
        self.assertEqual(len(find_spikes(counts, min_ratio=2.0,
                                         min_count=10)), 1)
        self.assertEqual(len(find_spikes(counts, min_ratio=5.0,
                                         min_count=10)), 0)


class TestLoadOffsets(unittest.TestCase):
    def test_fixture_chat_log(self):
        # Synthetic fixture: 180 message offsets, no usernames.
        path = os.path.join(HERE, "fixtures", "chat.json")
        offsets = load_offsets(path)
        self.assertEqual(len(offsets), 180)
        self.assertEqual(offsets, sorted(offsets))
        counts = count_windows(offsets, 10, 120)
        self.assertEqual(max(counts), 90)
        spikes = find_spikes(counts)
        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0]["spike_time"], 65)
        self.assertEqual(spikes[0]["count"], 90)
        self.assertEqual(spikes[0]["ratio"], 16.4)

    def test_plain_list_format(self):
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            json.dump([{"offset": 5}, {"offset": 15}, {"offset": 25}], f)
            path = f.name
        try:
            self.assertEqual(load_offsets(path), [5.0, 15.0, 25.0])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
