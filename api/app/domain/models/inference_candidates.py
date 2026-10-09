"""Shared stable fallback ordering for runtime secrets and metadata-only proofs."""


def ordered_candidates(primary, candidates, policy, *, eligible, thinking, thinking_enabled):
    chain = [primary]
    if not (policy.fallback_enabled or policy.fallback_on_quota_exceeded):
        return chain
    allow_cross = (policy.fallback_enabled and policy.allow_cross_provider_fallback) or (
        policy.fallback_on_quota_exceeded and policy.allow_cross_provider_fallback_on_quota
    )
    seen = {primary.id}
    same, cross = [], []
    for candidate in candidates:
        if candidate.id in seen or not eligible(candidate):
            continue
        if candidate.provider == primary.provider:
            same.append(candidate)
        elif allow_cross:
            cross.append(candidate)
        else:
            continue
        seen.add(candidate.id)
    if thinking_enabled:
        same.sort(key=lambda candidate: 0 if thinking(candidate) else 1)
    return chain + same + cross
