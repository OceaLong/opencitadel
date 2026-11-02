"""Context ceilings derive only from reviewed, frozen budget candidate proofs."""

from app.domain.evaluation.budget_capabilities import PROFILES, BudgetProfile
from app.domain.execution.context_models import ContextCandidateProfile, context_digest


def context_profiles(candidates, *, output_tokens):
    proof = candidates.proof
    result = []
    for candidate in proof["candidates"]:
        profile = BudgetProfile.model_validate(candidate["profile"])
        provider, model, _origin, window, maximum, _source = PROFILES[profile.profile]
        identity = candidate["identity"]
        if identity["configured_model"] != model or identity["provider"] != provider:
            raise ValueError("CONTEXT_CANDIDATE_PROFILE_CHANGED")
        if (
            candidate["output"] != output_tokens
            or candidate["settings"].get("max_output_tokens") != output_tokens
        ):
            raise ValueError("CONTEXT_OUTPUT_PROFILE_UNSUPPORTED")
        value = ContextCandidateProfile(
            identity_digest=context_digest(identity),
            profile_digest=context_digest(candidate["profile"]),
            candidate_proof_digest=context_digest(proof),
            input_cap=window,
            window=window,
            output_cap=maximum,
            image_profile="gpt41-inline-high-v1"
            if profile.profile == "openai-gpt41-chat-v1"
            else "none",
        )
        value.input_budget(output_tokens)
        result.append(value)
    if not result:
        raise ValueError("CONTEXT_CANDIDATE_PROFILE_UNAVAILABLE")
    return tuple(result)
