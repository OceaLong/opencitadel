from app.domain.models import error_codes as EC
from app.domain.utils.llm_retry import classify_llm_error_code


def test_unknown_runtime_error_maps_to_infrastructure_failed():
    exc = RuntimeError("context cleanup failed")
    assert classify_llm_error_code(exc) == EC.INFRASTRUCTURE_FAILED
