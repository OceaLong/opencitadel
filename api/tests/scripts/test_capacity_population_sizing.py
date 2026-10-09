"""A tight successful-wire upper bound still leaves failure/C2c unresolved."""

from capacity_population_sizing import (
    KNOWN_PUBLIC_ROLE_BYTES_WITH_MARGIN,
    _artifact,
    _line,
    failure_acquired_png_upper,
    success_native_wire_upper,
)


def test_complete_success_native_envelope_exceeds_candidate_before_c2c():
    assert _line("image", 48 * 1024, 170) < 96 * 1024
    assert _line("native-trace", 48 * 1024, 682) < 96 * 1024
    assert _artifact("image", 8 * 1024 * 1024) > 0
    assert _artifact("native-trace", 32 * 1024 * 1024) > 0
    assert success_native_wire_upper() == 434_241_801_400
    assert (
        success_native_wire_upper()
        + KNOWN_PUBLIC_ROLE_BYTES_WITH_MARGIN
        + failure_acquired_png_upper()
        > 512 * 1024**3
    )
    assert failure_acquired_png_upper() == 110_729_625_600
