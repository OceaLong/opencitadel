"""Source-only Protocol/Network generation, never measured runtime proof."""

from api.tests.scripts.capacity_population import slots
from api.tests.scripts.capacity_population_control import write_control_roles
from scripts.acceptance.capacity_io import read_relative, strict_json
from scripts.acceptance.capacity_models import Network, Protocol


def test_full_physical_protocol_and_network_have_closed_typed_shards(tmp_path):
    tmp_path.chmod(0o700)
    artifacts = write_control_roles(
        tmp_path,
        attempt_id="synthetic-attempt",
        protocol_id="synthetic-protocol",
        seal_id="seal",
        binding_digest="a" * 64,
    )
    assert artifacts[0]["role"] == "protocol"
    protocol = Protocol.model_validate(
        strict_json(read_relative(tmp_path, artifacts[0]["path"], artifacts[0]["size_bytes"]))
    )
    assert len(protocol.samples) == 1200
    assert len(protocol.windows) == 1100
    assert len({row.physical_window_id for row in protocol.samples}) == 1200
    assert sum(row.window_id is None for row in protocol.samples) == 100
    loaded = {slot.loaded_window_id for slot in slots() if slot.loaded_window_id is not None}
    assert {row.window_id for row in protocol.windows} == loaded
    context_ids, calibrations, intervals = [], 0, 0
    for item in artifacts[1:]:
        network = Network.model_validate(
            strict_json(read_relative(tmp_path, item["path"], item["size_bytes"]))
        )
        context_ids.extend(network.context_ids)
        calibrations += len(network.calibrations)
        intervals += len(network.phase_intervals)
    assert len(context_ids) == len(set(context_ids)) == 11000
    assert calibrations == 1100 * 72
    assert intervals == 1100 * 4
    assert len(artifacts) > 2  # Network families span independent shards.
