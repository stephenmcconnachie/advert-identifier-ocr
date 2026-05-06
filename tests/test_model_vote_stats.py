"""Tests for RefinedAdvertResult model ensemble_vote_stats field."""
from ad_break_identifier.models import RefinedAdvertResult


class TestRefinedAdvertResultModel:
    def test_ensemble_vote_stats_default_none(self):
        r = RefinedAdvertResult()
        assert r.ensemble_vote_stats is None

    def test_ensemble_vote_stats_set_via_constructor(self):
        stats = {"outliers_removed": 2, "mad": 1.5}
        r = RefinedAdvertResult(ensemble_vote_stats=stats)
        assert r.ensemble_vote_stats == stats
        assert r.ensemble_vote_stats["outliers_removed"] == 2
