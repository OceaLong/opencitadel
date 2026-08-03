import pytest

from opencitadel_ops_collector.config import CollectorSettings
from opencitadel_ops_collector.main import build_parser
from opencitadel_ops_collector.security import ProbeDenied, require_namespace, require_registered, validate_redirect


def test_streamable_http_is_default_transport():
    assert CollectorSettings().transport == "streamable-http"
    assert build_parser().parse_args([]).transport is None


def test_namespace_and_probe_allowlists_fail_closed():
    settings = CollectorSettings(allowed_namespaces=["allowed"])
    with pytest.raises(ProbeDenied, match="NAMESPACE_DENIED"):
        require_namespace(settings, "other")
    with pytest.raises(ProbeDenied, match="PROBE_DENIED"):
        require_registered({}, "unknown")


@pytest.mark.parametrize("target", ["http://127.0.0.1/admin", "http://169.254.169.254/latest", "https://evil.example/path"])
def test_redirect_cannot_escape_registered_origin(target):
    with pytest.raises(ProbeDenied):
        validate_redirect("https://service.example/health", target)
