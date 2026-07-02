"""Tests for the detect module — two-tier brand matching and ordering enforcement."""

import pytest
from ad_break_identifier.detect import (
    build_exact_patterns,
    build_substring_patterns,
    match_ocr_text,
    search_with_ordering,
    format_xml,
    seconds_to_timecode,
    timecode_to_seconds,
    tod_to_seconds,
    BrandSearchResult,
)
from ad_break_identifier.models import (
    AdBreakMetadata,
    AdvertMetadata,
    ProgrammeMetadata,
)


class TestBuildExactPatterns:
    def test_simple_brand(self):
        patterns = build_exact_patterns("Galaxy", "", "")
        assert len(patterns) == 1
        assert patterns[0].pattern == r"\bGalaxy\b"

    def test_multi_word_brand(self):
        patterns = build_exact_patterns("Tesco Easter", "", "")
        # Full term + individual words (both >= 3 chars)
        assert len(patterns) == 3

    def test_apostrophe_stripped(self):
        patterns = build_exact_patterns("McDonald's", "", "")
        # Original + apostrophe-stripped
        assert len(patterns) == 2
        texts = [p.pattern for p in patterns]
        assert r"\bMcDonald's\b" in texts
        assert r"\bMcDonalds\b" in texts

    def test_deduplication(self):
        patterns = build_exact_patterns("Galaxy", "Galaxy", "")
        # Should deduplicate
        assert len(patterns) == 1

    def test_short_words_excluded(self):
        patterns = build_exact_patterns("AB CD", "", "")
        # Both "AB" (2 chars) and "CD" (2 chars) are < 3 chars, so only full term
        assert len(patterns) == 1

    def test_empty_terms(self):
        patterns = build_exact_patterns("", "", "")
        assert len(patterns) == 0


class TestBuildSubstringPatterns:
    def test_simple_brand(self):
        patterns = build_substring_patterns("Galaxy", "", "")
        assert len(patterns) == 1
        assert patterns[0].pattern == "Galaxy"

    def test_no_word_boundaries(self):
        patterns = build_substring_patterns("galaxy", "", "")
        # Should match "galaxychocolate.com"
        m, _ = match_ocr_text("galaxychocolate.com", patterns)
        assert m is True


class TestMatchOcrText:
    def test_exact_match(self):
        patterns = build_exact_patterns("Tesco", "", "")
        m, terms = match_ocr_text("Welcome to Tesco today", patterns)
        assert m is True
        assert "Tesco" in terms

    def test_no_match_empty_text(self):
        patterns = build_exact_patterns("Tesco", "", "")
        m, terms = match_ocr_text("", patterns)
        assert m is False
        assert terms == []

    def test_no_match_no_text(self):
        patterns = build_exact_patterns("Tesco", "", "")
        m, terms = match_ocr_text("Sainsbury's is better", patterns)
        assert m is False

    def test_word_boundary_prevents_concatenated_match(self):
        patterns = build_exact_patterns("galaxy", "", "")
        m, _ = match_ocr_text("galaxychocolate.com", patterns)
        assert m is False

    def test_substring_catches_concatenated(self):
        patterns = build_substring_patterns("galaxy", "", "")
        m, terms = match_ocr_text("galaxychocolate.com", patterns)
        assert m is True
        assert "galaxy" in terms

    def test_case_insensitive(self):
        patterns = build_exact_patterns("TESCO", "", "")
        m, terms = match_ocr_text("welcome to tesco", patterns)
        assert m is True
        assert "tesco" in terms

    def test_deduplication_of_terms(self):
        patterns = build_exact_patterns("Galaxy", "Galaxy", "")
        m, terms = match_ocr_text("Galaxy Galaxy galaxy", patterns)
        assert m is True
        # Should deduplicate case-insensitively
        assert len(terms) == 1


class TestSearchWithOrdering:
    @pytest.fixture
    def adverts(self):
        return [
            AdvertMetadata(
                unique_id="ADV1",
                advertiser="Adv1",
                brand="Galaxy",
                category="chocolate",
            ),
            AdvertMetadata(
                unique_id="ADV2", advertiser="Adv2", brand="Mars", category="chocolate"
            ),
            AdvertMetadata(
                unique_id="ADV3", advertiser="Adv3", brand="Tesco", category="retail"
            ),
        ]

    @pytest.fixture
    def ocr_results(self):
        # Frames at 5 FPS: frame 0 = 0s, frame 5 = 1s, etc.
        return [
            {"frame_index": 0, "text": "ITV News"},
            {"frame_index": 1, "text": "Galaxy chocolate bar"},
            {"frame_index": 10, "text": "Galaxy chocolate bar again"},
            {"frame_index": 15, "text": "Mars bar"},
            {"frame_index": 20, "text": "Mars bar again"},
            {"frame_index": 25, "text": "Tesco supermarket"},
            {"frame_index": 30, "text": "Tesco again"},
            {"frame_index": 35, "text": "End of break"},
        ]

    def test_all_match_exact_tier(self, adverts, ocr_results):
        results = search_with_ordering(ocr_results, adverts, fps=5.0)
        assert len(results) == 3

        # Galaxy: last exact match at frame 10
        assert results[0].matched is True
        assert results[0].match_tier == "exact"
        assert results[0].last_match_frame == 10
        assert results[0].last_match_seconds == 10 / 5.0

        # Mars: last exact match at frame 20 (after Galaxy's frame 10)
        assert results[1].matched is True
        assert results[1].match_tier == "exact"
        assert results[1].last_match_frame == 20

        # Tesco: last exact match at frame 30 (after Mars's frame 20)
        assert results[2].matched is True
        assert results[2].match_tier == "exact"
        assert results[2].last_match_frame == 30

    def test_ordering_enforced(self, adverts, ocr_results):
        results = search_with_ordering(ocr_results, adverts, fps=5.0)
        # Each advert's frame must be > previous advert's frame
        assert results[0].last_match_frame < results[1].last_match_frame
        assert results[1].last_match_frame < results[2].last_match_frame

    def test_fallback_when_no_match(self, adverts):
        ocr_results = [
            {"frame_index": 0, "text": "Nothing relevant"},
            {"frame_index": 1, "text": "Still nothing"},
        ]
        results = search_with_ordering(ocr_results, adverts, fps=5.0)
        assert len(results) == 3
        assert results[0].matched is False
        assert results[0].match_tier == "fallback"
        assert results[1].matched is False
        assert results[2].matched is False

    def test_substring_fallback_tier(self, adverts):
        # "galaxychocolate.com" should not match exact but should match substring
        ocr_results = [
            {"frame_index": 0, "text": "Visit galaxychocolate.com"},
            {"frame_index": 5, "text": "marsbar.com promo"},
            {"frame_index": 10, "text": "tescoshop.com deals"},
        ]
        results = search_with_ordering(ocr_results, adverts, fps=5.0)
        assert len(results) == 3
        assert results[0].match_tier == "substring"
        assert results[0].last_match_frame == 0
        assert results[1].match_tier == "substring"
        assert results[1].last_match_frame == 5
        assert results[2].match_tier == "substring"
        assert results[2].last_match_frame == 10

    def test_first_advert_fallback_doesnt_block_others(self, adverts):
        # If first advert doesn't match, second should still search from frame 0
        ocr_results = [
            {"frame_index": 0, "text": "Nothing here"},
            {"frame_index": 5, "text": "Mars bar"},
            {"frame_index": 10, "text": "Tesco supermarket"},
        ]
        results = search_with_ordering(ocr_results, adverts, fps=5.0)
        assert results[0].matched is False
        # Mars should still match (prev_last_frame was -1, so frame 5 > -1)
        assert results[1].matched is True
        assert results[1].last_match_frame == 5
        assert results[2].matched is True
        assert results[2].last_match_frame == 10


class TestFormatXml:
    def test_basic_output(self):
        metadata = AdBreakMetadata(
            programme_before=ProgrammeMetadata(title="News", channel="ITV1"),
            programme_after=ProgrammeMetadata(title="News", channel="ITV1"),
            adverts=[
                AdvertMetadata(
                    unique_id="ADV1",
                    advertiser="Adv1",
                    brand="Galaxy",
                    category="chocolate",
                    duration_seconds=30,
                ),
            ],
        )
        results = [
            BrandSearchResult(
                matched=True,
                last_match_frame=100,
                last_match_seconds=20.0,
                match_tier="exact",
                match_count=5,
                all_matching_frames=[90, 95, 100],
                matched_terms=["galaxy"],
            ),
        ]
        xml = format_xml(metadata, results)
        assert "<ad_break>" in xml
        assert "<advert>" in xml
        assert "<unique_id>ADV1</unique_id>" in xml
        assert "<brand>Galaxy</brand>" in xml
        assert "<last_timecode>" in xml
        assert "00:20.000" in xml  # 20.0s -> 00:20.000
        assert "<match_tier>exact</match_tier>" in xml
        assert "<ocr_match_fallback>" not in xml

    def test_fallback_output(self):
        metadata = AdBreakMetadata(
            programme_before=ProgrammeMetadata(title="News", channel="ITV1"),
            programme_after=ProgrammeMetadata(title="News", channel="ITV1"),
            adverts=[
                AdvertMetadata(
                    unique_id="ADV1",
                    advertiser="Adv1",
                    brand="Unknown",
                    category="misc",
                ),
            ],
        )
        results = [
            BrandSearchResult(
                matched=False,
                last_match_frame=None,
                last_match_seconds=None,
                match_tier="fallback",
                match_count=0,
                all_matching_frames=[],
                matched_terms=[],
            ),
        ]
        xml = format_xml(metadata, results)
        assert "<last_timecode></last_timecode>" in xml
        assert "<ocr_match_fallback>true</ocr_match_fallback>" in xml

    def test_refined_end_shifts_start(self):
        """When refined_end_seconds is set, start_timecode must be derived
        from the refined end, not the raw 5fps frame.

        5fps match at frame 200 = 40.0s, refined to 40.12s (3 source frames).
        With the fix: start = 40.12 - 30 + 1/25 = 10.16s.
        Old buggy: start = 200/5 - 30 = 10.0s (4 source frames too early).
        """
        metadata = AdBreakMetadata(
            programme_before=ProgrammeMetadata(title="News", channel="ITV1"),
            programme_after=ProgrammeMetadata(title="News", channel="ITV1"),
            adverts=[
                AdvertMetadata(
                    unique_id="ADV1",
                    advertiser="Adv1",
                    brand="Galaxy",
                    category="chocolate",
                    duration_seconds=30,
                ),
            ],
        )
        results = [
            BrandSearchResult(
                matched=True,
                last_match_frame=200,
                last_match_seconds=40.0,
                refined_end_seconds=40.12,
                match_tier="exact",
                match_count=5,
                all_matching_frames=[190, 195, 200],
                matched_terms=["galaxy"],
            ),
        ]
        xml = format_xml(metadata, results)
        assert "<start_timecode>00:10.160</start_timecode>" in xml
        assert "<last_timecode>00:40.120</last_timecode>" in xml

    def test_unrefined_end_unchanged(self):
        """Without refinement, start is derived from last_match_seconds."""
        metadata = AdBreakMetadata(
            programme_before=ProgrammeMetadata(title="News", channel="ITV1"),
            programme_after=ProgrammeMetadata(title="News", channel="ITV1"),
            adverts=[
                AdvertMetadata(
                    unique_id="ADV1",
                    advertiser="Adv1",
                    brand="Galaxy",
                    category="chocolate",
                    duration_seconds=30,
                ),
            ],
        )
        results = [
            BrandSearchResult(
                matched=True,
                last_match_frame=200,
                last_match_seconds=40.0,
                refined_end_seconds=None,
                match_tier="exact",
                match_count=5,
                all_matching_frames=[],
                matched_terms=["galaxy"],
            ),
        ]
        xml = format_xml(metadata, results)
        # start = 40.0 - 30 + 1/25 = 10.04s → 00:10.040
        assert "<start_timecode>00:10.040</start_timecode>" in xml


class TestTimeHelpers:
    def test_seconds_to_timecode(self):
        assert seconds_to_timecode(0.0) == "00:00.000"
        assert seconds_to_timecode(135.0) == "02:15.000"
        assert seconds_to_timecode(270.2) == "04:30.200"

    def test_timecode_to_seconds(self):
        assert timecode_to_seconds("02:15.000") == 135.0
        assert timecode_to_seconds("01:30:00") == 5400.0

    def test_tod_to_seconds(self):
        assert tod_to_seconds("09:48:30") == 35310.0
        assert tod_to_seconds("00:00:00") == 0.0


class TestTextSimilarity:
    def test_identical_text(self):
        from ad_break_identifier.detect import _text_similarity

        assert _text_similarity("Google Pixel 8", "Google Pixel 8") == 1.0

    def test_completely_different(self):
        from ad_break_identifier.detect import _text_similarity

        assert _text_similarity("Google Pixel 8", "Power Foam") < 0.5

    def test_empty_strings(self):
        from ad_break_identifier.detect import _text_similarity

        assert _text_similarity("", "") == 1.0
        assert _text_similarity("", "text") == 0.0

    def test_whitespace_normalised(self):
        from ad_break_identifier.detect import _text_similarity

        assert _text_similarity("Google  Pixel", "Google Pixel") == 1.0

    def test_case_insensitive(self):
        from ad_break_identifier.detect import _text_similarity

        assert _text_similarity("Google Pixel", "google pixel") == 1.0
