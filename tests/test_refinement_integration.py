"""Integration tests for refinement module with ensemble filter."""
from ad_break_identifier.refinement import refine_single_advert
import inspect


class TestRefineSingleAdvertSignature:
    """Verify the refine_single_advert function accepts ensemble filter params."""

    def test_ensemble_filter_parameter_exists(self):
        sig = inspect.signature(refine_single_advert)
        assert "ensemble_filter" in sig.parameters

    def test_ensemble_filter_threshold_parameter_exists(self):
        sig = inspect.signature(refine_single_advert)
        assert "ensemble_filter_threshold" in sig.parameters

    def test_ensemble_filter_default(self):
        sig = inspect.signature(refine_single_advert)
        param = sig.parameters["ensemble_filter"]
        assert param.default == "none"

    def test_ensemble_filter_threshold_default(self):
        sig = inspect.signature(refine_single_advert)
        param = sig.parameters["ensemble_filter_threshold"]
        assert param.default == 3.0
