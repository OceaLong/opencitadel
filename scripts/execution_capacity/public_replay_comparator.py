"""Consume freshly replayed originals against exact sealed public role rows."""

from scripts.acceptance.capacity_public_ids import PublicIDs


class PublicReplayComparator:
    def __init__(self, package):
        package._usable()
        self.package = package
        self.roles = package.roles
        self.positions = {
            ("c2c", "units"): 0,
            ("cleanup", "rounds"): 0,
            ("cleanup", "cohorts"): 0,
            ("diagnostics", "queries"): 0,
        }
        self.origins = PublicIDs(package, "projection-origins")
        self.base_seen = False

    def _usable(self):
        self.package._usable()
        if self.package._private_comparison is not self:
            raise ValueError("inactive public replay comparison")

    def _fail(self):
        from scripts.execution_capacity.offline_context import PrivateProofError

        raise PrivateProofError("private C2c complete safe projection differs")

    def _take(self, role, field, value):
        self._usable()
        rows = getattr(self.roles[role], field)
        position = self.positions[role, field]
        if position >= len(rows) or rows[position] != value:
            self._fail()
        self.positions[role, field] = position + 1

    def unit(self, value):
        self._usable()
        if (
            value.origin_sha256 in self.origins
            or value.schema_version != self.roles["c2c"].projection_version
        ):
            self._fail()
        self.origins.add(value.origin_sha256)
        self._take("c2c", "units", value)

    def base(self, seal, value):
        self._usable()
        if self.base_seen or self.roles["seal"] != seal:
            self._fail()
        self.base_seen = True
        if (self.roles["c2c"].attempt_id, self.roles["c2c"].protocol_id) != (
            seal.attempt_id,
            seal.protocol_id,
        ):
            self._fail()
        self.unit(value)
        for cohort in seal.cohorts:
            self._take("cleanup", "cohorts", cohort)

    def round(self, value):
        self._take("cleanup", "rounds", value)
        for cohort in value.cohorts:
            self._take("cleanup", "cohorts", cohort)

    def query(self, value):
        self._take("diagnostics", "queries", value)

    def finish(self, origins):
        from scripts.execution_capacity.offline_context import validate_round_origins

        self._usable()
        if not self.base_seen:
            self._fail()
        for (role, field), position in self.positions.items():
            if position != len(getattr(self.roles[role], field)):
                self._fail()
        if (self.roles["protocol"].attempt_id, self.roles["protocol"].protocol_id) != (
            self.roles["c2c"].attempt_id,
            self.roles["c2c"].protocol_id,
        ):
            self._fail()
        try:
            validate_round_origins(
                self.roles["protocol"],
                origins,
                (window.round_origin for window in self.roles["workload"].windows),
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            self._fail()
