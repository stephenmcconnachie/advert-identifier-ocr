"""Ensemble vote aggregation with MAD-based outlier rejection.

Provides utility functions for combining multiple ensemble votes into
a single result, with optional outlier filtering using Median Absolute
Deviation (MAD). The final median uses the same indexing convention as
the existing codebase: sorted(frames)[len(frames) // 2], which gives the
upper-middle element for even-sized lists.
"""

from typing import Literal


def compute_mad(values: list[int]) -> float:
    """Compute Median Absolute Deviation from the median.

    MAD is a robust measure of statistical dispersion, less sensitive
    to outliers than standard deviation.

    Args:
        values: List of integer values (e.g., frame numbers).

    Returns:
        MAD as a float. Returns 0.0 for empty or single-element lists.
    """
    if not values:
        return 0.0

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    median = sorted_vals[n // 2]

    abs_devs = sorted(abs(v - median) for v in sorted_vals)
    return float(abs_devs[len(abs_devs) // 2])


def ensemble_vote_with_filter(
    frames: list[int],
    method: Literal["none", "mad"] = "mad",
    threshold: float = 3.0,
) -> tuple[int, dict]:
    """Vote on ensemble frames with optional MAD-based outlier rejection.

    The voting process:
    1. Compute the median of all frames (current behavior)
    2. If method='mad', discard frames whose absolute deviation from the
       median exceeds ``threshold * MAD``
    3. Compute the median of remaining frames

    The median uses the existing codebase convention:
    ``sorted(frames)[len(frames) // 2]`` — for odd-length lists this is
    the true median; for even-length lists this is the upper-middle element.

    Args:
        frames: List of frame numbers from ensemble calls (integers).
        method: 'none' = raw median (no filtering); 'mad' = discard MAD
            outliers then median.
        threshold: MAD multiplier for the outlier fence. Higher values
            filter fewer values (default: 3.0). A value of 3.0 means
            any frame further than 3 MADs from the median is an outlier.

    Returns:
        Tuple of (voted_frame, stats_dict). The stats dict includes:
            - original_count:   Total frames provided
            - original_median:  Median before filtering
            - mad:              Computed MAD value (0.0 if < 3 values)
            - outliers_removed: Number of frames discarded
            - outlier_values:   Sorted list of discarded frame numbers
            - filtered_count:   Frames remaining after filtering
            - filtered_median:  Median after filtering (== original_median
                                when method='none' or no outliers)

    Raises:
        ValueError: If ``frames`` is empty.
    """
    if not frames:
        raise ValueError("Cannot vote on empty frame list")

    sorted_frames = sorted(frames)
    n = len(sorted_frames)
    original_median = sorted_frames[n // 2]

    stats = {
        "original_count": n,
        "original_median": original_median,
        "mad": 0.0,
        "outliers_removed": 0,
        "outlier_values": [],
        "filtered_count": n,
        "filtered_median": original_median,
    }

    if method == "none" or n < 3:
        return original_median, stats

    mad = compute_mad(frames)
    stats["mad"] = round(mad, 2)

    if mad == 0.0:
        return original_median, stats

    fence = threshold * mad
    filtered = [f for f in frames if abs(f - original_median) <= fence]
    outliers = [f for f in frames if abs(f - original_median) > fence]

    if not filtered:
        # All frames discarded — fall back to original median
        stats["outliers_removed"] = len(outliers)
        stats["outlier_values"] = sorted(outliers)
        return original_median, stats

    sorted_filtered = sorted(filtered)
    filtered_median = sorted_filtered[len(sorted_filtered) // 2]

    stats["outliers_removed"] = len(outliers)
    stats["outlier_values"] = sorted(outliers)
    stats["filtered_count"] = len(filtered)
    stats["filtered_median"] = filtered_median

    return filtered_median, stats
