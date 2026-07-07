"""Tests for data models and helper functions."""

import pytest
from ad_break_identifier.models import (
    AdBreakMetadata,
    AdvertMetadata,
    ProgrammeMetadata,
    parse_duration_from_unique_id,
    VALID_DURATIONS,
)


class TestParseDurationFromUniqueId:
    def test_valid_suffix_10(self):
        assert parse_duration_from_unique_id("LMIMTWY004010") == 10

    def test_valid_suffix_20(self):
        assert parse_duration_from_unique_id("WCRSKBR052020") == 20

    def test_valid_suffix_30(self):
        assert parse_duration_from_unique_id("HOGTOLX095030") == 30

    def test_valid_suffix_40(self):
        assert parse_duration_from_unique_id("DTVGOWI001040") == 40

    def test_valid_suffix_60(self):
        assert parse_duration_from_unique_id("NCAALZH038060") == 60

    def test_valid_suffix_90(self):
        assert parse_duration_from_unique_id("MCZVAXV092090") == 90

    def test_no_numeric_suffix(self):
        assert parse_duration_from_unique_id("ADV1") is None

    def test_short_unique_id(self):
        assert parse_duration_from_unique_id("AB") is None

    def test_empty_string(self):
        assert parse_duration_from_unique_id("") is None

    def test_non_digit_ending(self):
        assert parse_duration_from_unique_id("UNKNOWNXXX") is None

    def test_single_advert_real_data(self):
        assert parse_duration_from_unique_id("TAGDDGB048020") == 20

    def test_last_advert_real_data(self):
        assert parse_duration_from_unique_id("BLBFURN204010") == 10

    def test_out_of_range_suffix(self, caplog):
        result = parse_duration_from_unique_id("TEST999999")
        assert result is None

    def test_long_unique_id_valid_suffix(self):
        assert parse_duration_from_unique_id("ANOANGP105020") == 20


class TestAdvertMetadataDuration:
    def test_duration_from_csv_fills_correctly(self):
        adv = AdvertMetadata(
            unique_id="TEST001030",
            advertiser="Test",
            brand="Test",
            category="Test",
            duration_seconds=30,
        )
        assert adv.duration_seconds == 30

    def test_duration_none_allowed(self):
        adv = AdvertMetadata(
            unique_id="TEST001030",
            advertiser="Test",
            brand="Test",
            category="Test",
            duration_seconds=None,
        )
        assert adv.duration_seconds is None

    def test_invalid_duration_raises(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            AdvertMetadata(
                unique_id="TEST001030",
                advertiser="Test",
                brand="Test",
                category="Test",
                duration_seconds=7,
            )


class TestAdBreakMetadata:
    def test_empty_adverts_raises(self):
        with pytest.raises(ValueError, match="At least one advert"):
            AdBreakMetadata(
                programme_before=ProgrammeMetadata(title="News", channel="ITV1"),
                programme_after=ProgrammeMetadata(title="News", channel="ITV1"),
                adverts=[],
            )

    def test_valid_break(self):
        meta = AdBreakMetadata(
            programme_before=ProgrammeMetadata(title="News", channel="ITV1"),
            programme_after=ProgrammeMetadata(title="News", channel="ITV1"),
            adverts=[
                AdvertMetadata(
                    unique_id="ADV001030",
                    advertiser="Test",
                    brand="Test",
                    category="Test",
                    duration_seconds=30,
                ),
            ],
        )
        assert len(meta.adverts) == 1
