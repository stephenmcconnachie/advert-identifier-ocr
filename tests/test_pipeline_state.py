"""Tests for pipeline_state module."""
import json
import pytest
from ad_break_identifier.pipeline_state import (
    derive_state_path,
    create_initial_state,
    read_state,
    write_state,
    update_break_adverts,
    get_adjusted_starts,
    _tod_to_seconds,
)
from pathlib import Path


SAMPLE_METADATA = {
    "video_info": {
        "filepath": "/videos/2024-03-26_ITV1HD_09:00:00.mp4",
        "start_time": "09:00:00",
    },
    "ad_breaks": [
        {
            "index": 1,
            "start_time": "09:48:30",
            "adverts": [
                {
                    "unique_id": "ADV001",
                    "brand": "BrandA",
                    "advertiser": "AdvA",
                    "category": "Food",
                    "duration_seconds": 30,
                },
                {
                    "unique_id": "ADV002",
                    "brand": "BrandB",
                    "advertiser": "AdvB",
                    "category": "Drink",
                    "duration_seconds": 20,
                },
            ],
        },
        {
            "index": 2,
            "start_time": "10:15:00",
            "adverts": [
                {
                    "unique_id": "ADV003",
                    "brand": "BrandC",
                    "advertiser": "AdvC",
                    "category": "Car",
                    "duration_seconds": 60,
                },
            ],
        },
    ],
}


class TestTodToSeconds:
    def test_hh_mm_ss(self):
        # 09:48:30 = 9*3600 + 48*60 + 30 = 35310
        assert _tod_to_seconds("09:48:30") == 35310.0

    def test_hh_mm_ss_midnight(self):
        assert _tod_to_seconds("00:00:00") == 0.0

    def test_mm_ss(self):
        assert _tod_to_seconds("04:30") == 270.0

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            _tod_to_seconds("not-a-time")


class TestDeriveStatePath:
    def test_metadata_suffix(self, tmp_path):
        meta = tmp_path / "2024-03-26_ITV1HD_09:00:00_metadata.json"
        meta.write_text("{}")
        state = derive_state_path(str(meta))
        assert state.endswith("_pipeline_state.json")
        assert "metadata" not in Path(state).stem

    def test_no_metadata_suffix(self, tmp_path):
        meta = tmp_path / "data.json"
        meta.write_text("{}")
        state = derive_state_path(str(meta))
        assert state.endswith("_pipeline_state.json")


class TestCreateInitialState:
    def test_creates_ad_breaks(self, tmp_path):
        meta_path = tmp_path / "sample_metadata.json"
        meta_path.write_text(json.dumps(SAMPLE_METADATA))

        state = create_initial_state(str(meta_path), before_secs=10.0)
        assert state["pipeline_version"] == 2
        assert len(state["ad_breaks"]) == 2

    def test_clip_offset_computation(self, tmp_path):
        meta_path = tmp_path / "sample_metadata.json"
        meta_path.write_text(json.dumps(SAMPLE_METADATA))

        state = create_initial_state(str(meta_path), before_secs=10.0)
        # Break 1: 09:48:30 = 35310, 09:00:00 = 32400
        # clip_offset = 35310 - 32400 - 10 = 2900
        break1 = state["ad_breaks"][0]
        assert break1["clip_offset"] == pytest.approx(2900.0, abs=0.001)

    def test_advert_initial_status(self, tmp_path):
        meta_path = tmp_path / "sample_metadata.json"
        meta_path.write_text(json.dumps(SAMPLE_METADATA))

        state = create_initial_state(str(meta_path), before_secs=10.0)
        adv0 = state["ad_breaks"][0]["adverts"][0]
        assert adv0["status"] == "metadata_extracted"
        assert adv0["detection"] is None
        assert adv0["unique_id"] == "ADV001"


class TestWriteReadState:
    def test_round_trip(self, tmp_path):
        meta_path = tmp_path / "sample_metadata.json"
        meta_path.write_text(json.dumps(SAMPLE_METADATA))
        state = create_initial_state(str(meta_path), before_secs=10.0)

        state_path = tmp_path / "test_state.json"
        write_state(str(state_path), state)
        loaded = read_state(str(state_path))

        assert loaded["pipeline_version"] == 2
        assert len(loaded["ad_breaks"]) == 2


class TestUpdateBreakAdverts:
    @pytest.fixture
    def state(self, tmp_path):
        meta_path = tmp_path / "sample_metadata.json"
        meta_path.write_text(json.dumps(SAMPLE_METADATA))
        return create_initial_state(str(meta_path), before_secs=10.0)

    def test_update_detection(self, state):
        updates = [
            {"detection": {"last_timecode": "04:30", "last_seconds_clip": 270.0, "last_frame": 1350}},
            {"detection": {"last_timecode": "04:50", "last_seconds_clip": 290.0, "last_frame": 1450}},
        ]
        update_break_adverts(state, ad_break_index=1, updates=updates)

        adv0 = state["ad_breaks"][0]["adverts"][0]
        assert adv0["detection"]["last_timecode"] == "04:30"
        assert adv0["detection"]["last_seconds_clip"] == 270.0

    def test_update_detection_computes_adjusted_start(self, state):
        updates = [
            {
                "detection": {
                    "last_timecode": "00:04:30.000",
                    "last_seconds_clip": 270.0,
                    "last_frame": 1350,
                }
            },
        ]
        update_break_adverts(state, ad_break_index=1, updates=updates)

        det = state["ad_breaks"][0]["adverts"][0]["detection"]
        # clip_offset = 2900
        # Non-last advert: duration=30, effective_end=270.0
        # start_seconds_clip = 270.0 - 30 + 1/25 = 240.04
        # adjusted_start_broadcast = 2900 + 240.04 = 3140.04
        assert det["adjusted_start_broadcast"] == pytest.approx(3140.04, abs=0.001)
        assert det["start_seconds_clip"] == pytest.approx(240.04, abs=0.001)

    def test_single_advert_break_no_start_computed(self, state):
        # First advert with no duration and no preceding advert
        # → single-advert break → logged and skipped → adjusted_start_broadcast is None
        state["ad_breaks"][0]["adverts"][0]["scheduled_duration_seconds"] = None
        updates = [
            {
                "detection": {
                    "last_timecode": "00:04:30.000",
                    "last_seconds_clip": 270.0,
                    "last_frame": 1350,
                }
            },
        ]
        update_break_adverts(state, ad_break_index=1, updates=updates)

        det = state["ad_breaks"][0]["adverts"][0]["detection"]
        assert det.get("adjusted_start_broadcast") is None

    def test_update_status(self, state):
        updates = [{"status": "detected"}, {"status": "detected"}]
        update_break_adverts(state, ad_break_index=1, updates=updates)
        assert state["ad_breaks"][0]["adverts"][0]["status"] == "detected"

    def test_update_second_break(self, state):
        updates = [{"status": "detected"}]
        update_break_adverts(state, ad_break_index=2, updates=updates)
        assert state["ad_breaks"][1]["adverts"][0]["status"] == "detected"
        # First break should be unchanged
        assert state["ad_breaks"][0]["adverts"][0]["status"] == "metadata_extracted"

    def test_invalid_break_index(self, state):
        with pytest.raises(IndexError):
            update_break_adverts(state, ad_break_index=99, updates=[])

    def test_last_advert_start_from_previous(self, state):
        # Second advert has no duration (last advert in multi-advert break)
        state["ad_breaks"][0]["adverts"][1]["scheduled_duration_seconds"] = None
        updates = [
            {"detection": {"last_timecode": "00:04:30.000", "last_seconds_clip": 270.0, "last_frame": 1350}},
            {"detection": {"last_timecode": "00:04:50.000", "last_seconds_clip": 290.0, "last_frame": 1450}},
        ]
        update_break_adverts(state, ad_break_index=1, updates=updates)

        det0 = state["ad_breaks"][0]["adverts"][0]["detection"]
        assert det0["start_seconds_clip"] == pytest.approx(240.04, abs=0.001)
        assert det0["adjusted_start_broadcast"] == pytest.approx(2900 + 240.04, abs=0.001)

        det1 = state["ad_breaks"][0]["adverts"][1]["detection"]
        # start = prev_effective_end + 1/25s = 270.0 + 0.04 = 270.04
        assert det1["start_seconds_clip"] == pytest.approx(270.04, abs=0.001)
        assert det1["adjusted_start_broadcast"] == pytest.approx(2900 + 270.04, abs=0.001)
        assert det1["detected_duration_seconds"] == pytest.approx(20.0 - 0.04, abs=0.1)

    def test_single_advert_break_no_detection_on_second(self, state):
        # Only first advert gets detection data; second has none
        # Second advert should be skipped in Pass 2 (no detection)
        updates = [
            {"detection": {"last_timecode": "00:04:30.000", "last_seconds_clip": 270.0, "last_frame": 1350}},
        ]
        update_break_adverts(state, ad_break_index=1, updates=updates)

        det0 = state["ad_breaks"][0]["adverts"][0]["detection"]
        assert det0["adjusted_start_broadcast"] is not None
        det1 = state["ad_breaks"][0]["adverts"][1].get("detection")
        assert det1 is None

    def test_refined_end_produces_correct_start(self, state):
        """When last_seconds_clip is refined (25fps), the start must be
        derived from the refined value, not last_frame/fps.

        This is the core regression test for the clip-start-too-early bug:
        without the fix, start = last_frame/fps - duration = 270.0 - 30 = 240.0
        (same as un-refined).  With the fix, start = 270.08 - 30 + 1/25 = 240.12,
        which is 3 source frames later.
        """
        updates = [
            {
                "detection": {
                    "last_timecode": "00:04:30.080",
                    "last_seconds_clip": 270.08,  # refined: 270.0 + 2 * 0.04
                    "last_frame": 1350,           # raw 5fps frame (270.0s)
                }
            },
        ]
        update_break_adverts(state, ad_break_index=1, updates=updates)

        det = state["ad_breaks"][0]["adverts"][0]["detection"]
        # Refined: start = 270.08 - 30 + 1/25 = 240.12
        # Old buggy: start = 1350/5 - 30 = 240.0 (3 source frames too early)
        assert det["start_seconds_clip"] == pytest.approx(240.12, abs=0.001)
        assert det["adjusted_start_broadcast"] == pytest.approx(2900 + 240.12, abs=0.001)


class TestGetAdjustedStarts:
    @pytest.fixture
    def state(self, tmp_path):
        meta_path = tmp_path / "sample_metadata.json"
        meta_path.write_text(json.dumps(SAMPLE_METADATA))
        s = create_initial_state(str(meta_path), before_secs=10.0)
        # Apply detection to both adverts in break 1
        update_break_adverts(s, ad_break_index=1, updates=[
            {"detection": {"last_timecode": "00:04:30.080", "last_seconds_clip": 270.08, "last_frame": 1354}},
            {"detection": {"last_timecode": "00:04:50.000", "last_seconds_clip": 290.0, "last_frame": 1450}},
        ])
        return s

    def test_returns_adjusted_starts(self, state):
        starts = get_adjusted_starts(state, ad_break_index=1)
        assert len(starts) == 2
        assert starts[0] is not None  # computed
        assert starts[1] is not None  # computed

    def test_returns_none_for_undetected(self, state):
        # Break 2 has no detection
        starts = get_adjusted_starts(state, ad_break_index=2)
        assert len(starts) == 1
        assert starts[0] is None
