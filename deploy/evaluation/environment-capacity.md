# Environment capacity

Environment occupancy is independent of subject/judge Run slots and physical model-call slots. Every E04 lease whose state is not `verified_clean` occupies one environment slot: allocated, preparing, ready, leased, cleaning and quarantine all count. Counts derive directly from durable leases, so policy changes, process restarts and claim expiry cannot reset holds. `verified_clean` is terminal, including against late original callbacks; reuse requires a new allocation.

The default workspace ceiling is 2. Deployment-wide and original-user environment limits default to null (counted without a ceiling); there are no invented positive global/user defaults. Settings are `EVALUATION_ENVIRONMENT_POLICY_REVISION` (default 1), `EVALUATION_ENVIRONMENT_CONCURRENCY` (default 2), `EVALUATION_ENVIRONMENT_GLOBAL_LIMIT`, and `EVALUATION_ENVIRONMENT_USER_LIMIT`. The original user is the immutable lease requester across personal/team scopes. Older missing personal requester metadata conservatively uses its persisted owner; missing team requester metadata uses one legacy-unknown aggregate, with no claim to recovered original-user identity.

Kernel startup initializes revision 1 or requires an exact existing-policy match. A trusted kernel allocator also performs this bootstrap/match in its caller transaction before a new lease. API routes register environment metadata and queue administrator repair; they do not allocate. Allocation checks signed system authorization and actual kernel table privileges before cross-scope counts, repeats authority checks after waiting for the policy lock, and rechecks current requester membership/token before admission. No new API global-lease grants or kernel credentials are introduced.

Using kernel deployment credentials from `api`, explicitly activate a complete new policy:

```sh
python -m scripts.environment_capacity_policy --policy /absolute/path/environment-policy.json --expected-revision 1
```

For example, a policy file preserving uncapped global/user capacity and lowering the workspace ceiling:

```json
{
  "revision": 2,
  "workspace_limit": 1,
  "global_limit": null,
  "user_limit": null
}
```

Expected revision 0 bootstraps revision 1. Exact repeat activation is idempotent; stale revisions or content mismatch fail closed. Coordinate worker settings/restarts with activation. Tightening below current occupancy blocks new allocations without interrupting cleanup or releasing holds. Raising/removing a limit does not rewrite leases. Immutable environment versions need no republication: allocation uses the minimum of the fixed version's requested concurrency and the active workspace limit. Registration still checks the configured ceiling; metadata validation/runtime/cleanup of an already registered version do not fail merely because capacity was tightened.

Allocation order is any caller-owned E06 namespace lock, then environment policy head, workspace allocation advisory lock, and canonical physical target fences. E04 cleanup/claim/completion retain lease → operation lock order and never acquire environment policy locks. Completing verified cleanup only decreases derived occupancy, so it needs no second capacity transaction. Unknown original prepare attempts cannot reach verified-clean through absence checks or TTL; exact original completion plus administrator repair and successful cleanup verification are required. There is no force release.

All allocation, lease insertion and prepare outbox intent share the caller UoW; rejected capacity rolls back all three. External adapter work occurs only after the worker commits its phase claim. These limits cover E04 isolated environment leases, not the ordinary warm sandbox pool or other resource types.

For E06, the API's 202 job-enqueue transaction must not allocate an environment under user-signed authorization. Scheduler materialization runs in a trusted kernel caller UoW with signed system authorization and kernel database privileges. Pass the job's verified original `Principal` separately to namespace/source/budget authorization and persist it in the lease requester; system execution identity is not the original user. Keep namespace, environment allocation/prepare intent and source/budget prebinding in that one materialization transaction, with the lock order above.
