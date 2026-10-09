"""The finite per-sample producer keeps each original row family typed."""

from capacity_population import slots
from capacity_population_sample import sample_bundle
from scripts.acceptance.capacity_models import (
    Browser,
    Plan,
    Reset,
    ResourceTrace,
    Sample,
    Source,
)


def test_finite_sample_bundle_has_unique_physical_and_all_required_row_kinds():
    seen = set()
    modes = set()
    operations = set()
    for slot in slots():
        plan, sample, source, browser, resource, reset = sample_bundle(slot)
        Plan.model_validate(plan)
        Sample.model_validate(sample)
        Source.model_validate(source)
        if browser is not None:
            Browser.model_validate(browser)
            ResourceTrace.model_validate(resource)
        else:
            assert resource is None
            assert slot.operation == "admission"
        if reset is not None:
            Reset.model_validate(reset)
        else:
            assert slot.mode != "cold"
        assert plan["sample_id"] == sample["sample_id"] == source["sample_id"]
        assert plan["physical_window_id"] not in seen
        seen.add(plan["physical_window_id"])
        if slot.operation == "live_visible":
            assert source["progress_id"].startswith(f"event-{slot.ordinal}-0-")
            assert source["marker_id"].startswith(f"marker-{slot.ordinal}-")
        modes.add(slot.mode)
        operations.add(slot.operation)
    assert len(seen) == 1200
    assert modes == {"warm", "cold", "baseline", "loaded"}
    assert operations == {
        "first_screen",
        "switch",
        "history",
        "analysis",
        "matrix",
        "live_visible",
        "admission",
    }
