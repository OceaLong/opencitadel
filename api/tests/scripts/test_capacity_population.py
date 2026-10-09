"""The full synthetic schedule is finite and has no shared physical round."""

from collections import Counter

from capacity_population import slots


def test_immutable_full_population_covers_every_unique_physical_round():
    rows = list(slots())
    assert len(rows) == 1200
    assert [row.ordinal for row in rows] == list(range(1200))
    assert len({row.sample_id for row in rows}) == 1200
    assert len({row.physical_window_id for row in rows}) == 1200
    assert sum(row.loaded_window_id is not None for row in rows) == 1100
    assert sum(row.loaded_window_id is None for row in rows) == 100
    assert sum(row.reset_id is not None for row in rows) == 100
    assert all(
        row.loaded_window_id == row.physical_window_id for row in rows if row.loaded_window_id
    )
    assert (
        Counter((row.dimension, row.mode, row.operation) for row in rows)[
            ("admission", "baseline", "admission")
        ]
        == 100
    )
