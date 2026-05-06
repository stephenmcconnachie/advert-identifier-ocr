"""Tests for ensemble outlier filter."""
import pytest
from ad_break_identifier.ensemble_filter import ensemble_vote_with_filter, compute_mad


class TestComputeMAD:
    def test_mad_identical_values(self):
        assert compute_mad([40, 40, 40]) == 0.0

    def test_mad_normal(self):
        # [38, 39, 40, 41, 42] -> median=40 -> devs [2,1,0,1,2] -> MAD=1
        assert compute_mad([38, 39, 40, 41, 42]) == 1.0

    def test_mad_with_outlier(self):
        # [20, 38, 39, 40, 70] -> median=39 -> devs [19,1,0,1,31] -> MAD=1
        assert compute_mad([20, 38, 39, 40, 70]) == 1.0

    def test_mad_empty(self):
        assert compute_mad([]) == 0.0

    def test_mad_single(self):
        assert compute_mad([42]) == 0.0

    def test_mad_two_values(self):
        # [10, 20] -> median=20 -> devs [10, 0] -> sorted [0, 10] -> MAD=10
        assert compute_mad([10, 20]) == 10.0


class TestEnsembleVoteWithFilter:
    def test_no_outliers_cluster_unchanged(self):
        """All values close together: no outliers filtered, median unchanged."""
        frames = [38, 39, 40, 41, 42]
        voted, stats = ensemble_vote_with_filter(frames, method="mad", threshold=3.0)
        assert voted == 40
        assert stats["outliers_removed"] == 0
        assert stats["original_count"] == 5
        assert stats["filtered_count"] == 5

    def test_symmetric_outliers_removed(self):
        """Symmetric outliers: both removed, median of core cluster returned."""
        frames = [20, 38, 39, 40, 70]
        voted, stats = ensemble_vote_with_filter(frames, method="mad", threshold=3.0)
        # median=39, devs=[19,1,0,1,31], MAD=1, fence=3
        # outliers: 20, 70. Remaining: [38, 39, 40]. Median of 3 = 39
        assert voted == 39
        assert stats["outliers_removed"] == 2
        assert stats["filtered_count"] == 3

    def test_asymmetric_outlier_shifts_median(self):
        """Single low outlier: set becomes even, median shifts by 1 frame (= 40ms at 25fps)."""
        frames = [20, 38, 39, 40, 41]
        voted, stats = ensemble_vote_with_filter(frames, method="mad", threshold=3.0)
        # median=39, devs=[19,1,0,1,2], MAD=1, fence=3
        # outlier: 20. Remaining: [38, 39, 40, 41]. Median of 4 = sorted[4//2] = 40
        # Shift of 1 frame = 40ms = material at 25fps
        assert voted == 40
        assert stats["outliers_removed"] == 1
        assert stats["filtered_count"] == 4

    def test_two_low_outliers_shifts_median(self):
        """Two low outliers: median shifts from 39 to 40."""
        frames = [20, 25, 39, 40, 41]
        voted, stats = ensemble_vote_with_filter(frames, method="mad", threshold=3.0)
        # median=39, devs=[19,14,0,1,2], MAD=2, fence=6
        # outliers: 20 (dev=19>6), 25 (dev=14>6). Remaining: [39, 40, 41]
        # Median of 3 = 40
        assert voted == 40
        assert stats["outliers_removed"] == 2

    def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            ensemble_vote_with_filter([], method="mad")

    def test_single_value(self):
        voted, stats = ensemble_vote_with_filter([42], method="mad")
        assert voted == 42
        assert stats["original_count"] == 1
        assert stats["outliers_removed"] == 0

    def test_mad_zero_all_identical(self):
        """All identical frames: MAD=0, no outliers detected."""
        voted, stats = ensemble_vote_with_filter([40, 40, 40], method="mad")
        assert voted == 40
        assert stats["mad"] == 0.0
        assert stats["outliers_removed"] == 0

    def test_method_none_skips_filtering(self):
        """method='none' bypasses filtering entirely."""
        voted, stats = ensemble_vote_with_filter(
            [20, 38, 39, 40, 70], method="none"
        )
        assert voted == 39
        assert stats["outliers_removed"] == 0
        assert stats["filtered_count"] == stats["original_count"]

    def test_low_threshold_aggressive(self):
        """Very low threshold catches more outliers."""
        frames = [20, 38, 39, 40, 70]
        # threshold=0.5 * MAD(1) = 0.5 — deviation > 0.5 catches everything
        # Actually with MAD=1 and threshold=0.5, fence=0.5
        # All deviations from median (39): [19, 1, 0, 1, 31]
        # Only exact median (dev=0) is within fence
        voted, stats = ensemble_vote_with_filter(frames, method="mad", threshold=0.5)
        assert stats["outliers_removed"] >= 4

    def test_high_threshold_conservative(self):
        """Very high threshold catches nothing."""
        frames = [20, 38, 39, 40, 70]
        voted, stats = ensemble_vote_with_filter(frames, method="mad", threshold=100.0)
        assert voted == 39
        assert stats["outliers_removed"] == 0

    def test_stats_dict_keys(self):
        """Stats dict contains all expected keys."""
        _, stats = ensemble_vote_with_filter([38, 39, 40], method="mad")
        expected_keys = {
            "original_count", "original_median", "mad",
            "outliers_removed", "outlier_values",
            "filtered_count", "filtered_median",
        }
        assert set(stats.keys()) == expected_keys
