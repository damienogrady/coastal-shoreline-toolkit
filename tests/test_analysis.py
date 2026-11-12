import pandas as pd

from coastwatch.analysis import (
    annual_window,
    summarize_movement,
)


def test_volume_proxy_uses_trapezoidal_area_and_profile_height():
    start = pd.DataFrame(
        {
            "transect_id": ["T001", "T002", "T003"],
            "alongshore_m": [0.0, 10.0, 20.0],
            "spacing_m": [10, 10, 10],
            "position_m": [100.0, 100.0, 100.0],
        }
    )
    end = start.assign(position_m=[110.0, 110.0, 110.0])
    positions, summary = summarize_movement(
        [
            {"year": 2016, "positions": start},
            {"year": 2018, "positions": end},
        ],
        active_profile_height_m=5,
    )

    assert len(positions) == 6
    assert summary.iloc[0]["shoreline_area_change_m2"] == 200
    assert summary.iloc[0]["indicative_volume_change_m3"] == -1_000


def test_volume_proxy_uses_only_transects_present_at_both_endpoints():
    start = pd.DataFrame(
        {
            "transect_id": ["T001", "T002"],
            "alongshore_m": [0.0, 10.0],
            "spacing_m": [10, 10],
            "position_m": [100.0, 100.0],
        }
    )
    end = start.iloc[[0]].assign(position_m=[105.0])
    _, summary = summarize_movement(
        [
            {"year": 2016, "positions": start},
            {"year": 2018, "positions": end},
        ],
        active_profile_height_m=5,
    )
    assert summary.iloc[0]["transects_compared"] == 1
    assert summary.iloc[0]["shoreline_area_change_m2"] == 0


def test_annual_window_covers_the_full_leap_year():
    start, end = annual_window(2024)
    assert start.isoformat() == "2024-01-01"
    assert end.isoformat() == "2025-01-01"

