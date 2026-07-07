"""Tests for single_advert_clip coordinate fixes."""

from ad_break_identifier.single_advert_clip import (
    timecode_to_seconds,
    parse_advert_xml,
    calculate_durations,
    parse_duration_from_unique_id,
)
import pytest


class TestTimecodeToSeconds:
    """timecode_to_seconds must handle MM:SS, HH:MM:SS, and HH:MM:SS.mmm."""

    def test_mm_ss_integer(self):
        assert timecode_to_seconds("04:30") == 270.0

    def test_mm_ss_with_leading_zeros(self):
        assert timecode_to_seconds("00:05") == 5.0

    def test_hh_mm_ss_integer(self):
        assert timecode_to_seconds("01:30:00") == 5400.0

    def test_hh_mm_ss_mmm(self):
        """Refined_timecode format with ms precision."""
        result = timecode_to_seconds("00:04:30.080")
        assert result == pytest.approx(270.080, abs=1e-6)

    def test_hh_mm_ss_mmm_leading_hour(self):
        result = timecode_to_seconds("01:00:00.500")
        assert result == pytest.approx(3600.500, abs=1e-6)

    def test_mm_ss_float_seconds(self):
        """MM:SS.ss format (edge case)."""
        result = timecode_to_seconds("04:30.5")
        assert result == pytest.approx(270.5, abs=1e-6)


class TestParseAdvertXml:
    """parse_advert_xml must read refined_timecode from refined XML."""

    def test_read_refined_timecode(self, tmp_path):
        xml_content = """<?xml version="1.0"?>
<ad_break>
    <advert>
        <unique_id>TEST001</unique_id>
        <brand>TestBrand</brand>
        <advertiser>TestAdv</advertiser>
        <category>TestCat</category>
        <duration_seconds>30</duration_seconds>
        <last_timecode>04:30</last_timecode>
        <refined_timecode>00:04:30.080</refined_timecode>
        <refined_clip_frame>40</refined_clip_frame>
        <refinement_status>success</refinement_status>
        <description>Test</description>
    </advert>
</ad_break>"""
        xml_path = tmp_path / "test.xml"
        xml_path.write_text(xml_content)

        adverts = parse_advert_xml(str(xml_path))
        assert len(adverts) == 1
        assert adverts[0]["refined_timecode"] == "00:04:30.080"
        assert adverts[0]["refined_clip_frame"] == 40

    def test_missing_refined_timecode(self, tmp_path):
        """XML without refined_timecode — field should be None."""
        xml_content = """<?xml version="1.0"?>
<ad_break>
    <advert>
        <unique_id>TEST001</unique_id>
        <brand>TestBrand</brand>
        <duration_seconds>30</duration_seconds>
        <last_timecode>04:30</last_timecode>
    </advert>
</ad_break>"""
        xml_path = tmp_path / "test_basic.xml"
        xml_path.write_text(xml_content)

        adverts = parse_advert_xml(str(xml_path))
        assert adverts[0]["refined_timecode"] is None
        assert adverts[0]["refined_clip_frame"] is None

    def test_multiple_adverts(self, tmp_path):
        xml_content = """<?xml version="1.0"?>
<ad_break>
    <advert>
        <unique_id>ADV1</unique_id>
        <brand>Brand1</brand>
        <duration_seconds>30</duration_seconds>
        <last_timecode>04:30</last_timecode>
        <refined_timecode>00:04:30.080</refined_timecode>
        <refined_clip_frame>40</refined_clip_frame>
    </advert>
    <advert>
        <unique_id>ADV2</unique_id>
        <brand>Brand2</brand>
        <duration_seconds>20</duration_seconds>
        <last_timecode>04:50</last_timecode>
    </advert>
</ad_break>"""
        xml_path = tmp_path / "test_multi.xml"
        xml_path.write_text(xml_content)

        adverts = parse_advert_xml(str(xml_path))
        assert len(adverts) == 2
        assert adverts[0]["refined_timecode"] == "00:04:30.080"
        assert adverts[1]["refined_timecode"] is None


class TestCalculateDurations:
    def test_single_advert_from_unique_id(self):
        adverts = [
            {
                "index": 1,
                "unique_id": "TAGDDGB048020",
                "brand": "Dove",
                "duration_seconds": None,
                "last_timecode": "00:20.000",
            },
        ]
        result = calculate_durations(adverts)
        assert result[0]["duration_seconds"] == 20

    def test_single_advert_fallback_default(self):
        adverts = [
            {
                "index": 1,
                "unique_id": "ADV1",
                "brand": "Test",
                "duration_seconds": None,
                "last_timecode": "00:20.000",
            },
        ]
        result = calculate_durations(adverts, default_duration=30)
        assert result[0]["duration_seconds"] == 30

    def test_last_advert_from_unique_id(self):
        adverts = [
            {
                "index": 1,
                "unique_id": "ADV001010",
                "brand": "Brand1",
                "duration_seconds": 10,
                "last_timecode": "00:10.000",
            },
            {
                "index": 2,
                "unique_id": "ADV002020",
                "brand": "Brand2",
                "duration_seconds": None,
                "last_timecode": "00:30.000",
            },
        ]
        result = calculate_durations(adverts)
        assert result[1]["duration_seconds"] == 20

    def test_last_advert_calculated_from_timecodes(self):
        adverts = [
            {
                "index": 1,
                "unique_id": "NO_SUFFIX",
                "brand": "Brand1",
                "duration_seconds": 10,
                "last_timecode": "00:10.000",
            },
            {
                "index": 2,
                "unique_id": "NO_SUFFIX_2",
                "brand": "Brand2",
                "duration_seconds": None,
                "last_timecode": "00:30.000",
            },
        ]
        result = calculate_durations(adverts)
        assert result[1]["duration_seconds"] == 20

    def test_first_advert_no_previous_raises(self):
        with pytest.raises(ValueError, match="Cannot calculate duration"):
            adverts = [
                {
                    "index": 1,
                    "unique_id": "NO_SUFFIX",
                    "brand": "Brand1",
                    "duration_seconds": None,
                    "last_timecode": "00:10.000",
                },
                {
                    "index": 2,
                    "unique_id": "ADV002020",
                    "brand": "Brand2",
                    "duration_seconds": 20,
                    "last_timecode": "00:30.000",
                },
            ]
            calculate_durations(adverts)


class TestParseDurationFromUniqueIdLocal:
    def test_valid_suffix(self):
        assert parse_duration_from_unique_id("TAGDDGB048020") == 20

    def test_no_suffix(self):
        assert parse_duration_from_unique_id("ADV1") is None

    def test_short_string(self):
        assert parse_duration_from_unique_id("AB") is None
