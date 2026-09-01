"""Stateless per-sandbox data-plane token derivation.

The sandbox HTTP data plane (shell/file API on :8080) is guarded by a bearer
token. Instead of minting a random token that only lives in the creating
process' memory -- which is lost the moment any other replica re-attaches to an
existing sandbox via :func:`DockerSandbox.get` / :func:`KubernetesSandbox.get`
-- the token is *derived* from a deployment-wide secret seed and the opaque
sandbox id:

    token = HMAC-SHA256(SANDBOX_TOKEN_SEED, sandbox_id)

Every replica that knows the seed (api + execution-kernel) computes the same
token for the same sandbox, so re-attachment always authenticates. The seed
never leaves the trusted control plane; only the derived token is injected into
the untrusted sandbox container (as ``SANDBOX_ACCESS_TOKEN``), which compares it
against the ``Authorization: Bearer`` header on every data-plane request.
"""

from __future__ import annotations

import hashlib
import hmac

__all__ = ["derive_sandbox_token"]


def derive_sandbox_token(seed: str, sandbox_id: str) -> str:
    """Derive the deterministic data-plane bearer token for one sandbox.

    The same ``(seed, sandbox_id)`` pair always yields the same token, and
    distinct sandbox ids yield distinct tokens. The result is a lowercase hex
    SHA-256 HMAC digest (64 chars), safe to place in an HTTP header and to
    compare with :func:`hmac.compare_digest`.
    """
    return hmac.new(
        seed.encode("utf-8"),
        sandbox_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
