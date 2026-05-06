"""Tests for refine_advert_timecodes ensemble filter parameters."""
from ad_break_identifier.refinement import refine_advert_timecodes
from ad_break_identifier.refinement_cli import create_parser
import inspect


class TestRefineAdvertTimecodesSignature:
    """Verify refine_advert_timecodes accepts ensemble filter params."""

    def test_ensemble_filter_parameter_exists(self):
        sig = inspect.signature(refine_advert_timecodes)
        assert "ensemble_filter" in sig.parameters

    def test_ensemble_filter_threshold_parameter_exists(self):
        sig = inspect.signature(refine_advert_timecodes)
        assert "ensemble_filter_threshold" in sig.parameters


class TestCLIEnsembleFilter:
    """Verify CLI accepts ensemble filter flags."""

    def test_ensemble_filter_flag_default(self):
        parser = create_parser()
        args = parser.parse_args([
            "--xml-file", "x.xml",
            "--video-url", "v.mp4",
        ])
        assert args.ensemble_filter == "none"

    def test_ensemble_filter_flag_mad(self):
        parser = create_parser()
        args = parser.parse_args([
            "--xml-file", "x.xml",
            "--video-url", "v.mp4",
            "--ensemble-filter", "mad",
        ])
        assert args.ensemble_filter == "mad"

    def test_ensemble_filter_threshold_flag(self):
        parser = create_parser()
        args = parser.parse_args([
            "--xml-file", "x.xml",
            "--video-url", "v.mp4",
            "--ensemble-filter", "mad",
            "--ensemble-filter-threshold", "2.5",
        ])
        assert args.ensemble_filter_threshold == 2.5

    def test_ensemble_filter_threshold_default(self):
        parser = create_parser()
        args = parser.parse_args([
            "--xml-file", "x.xml",
            "--video-url", "v.mp4",
        ])
        assert args.ensemble_filter_threshold == 3.0
