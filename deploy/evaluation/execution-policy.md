# Evaluation execution capacity

These are Run execution slots: five subjects and two judges per workspace by default. Global and original-requester limits can tighten those pools. They are separate from physical provider occupancy, environment allocations, and the existing 200 active-root admission constraint.

The kernel bootstraps revision 1 only. Restart settings must exactly match the active immutable policy; a mismatch fails startup. Configure `EVALUATION_EXECUTION_POLICY_REVISION`, `EVALUATION_SUBJECT_CONCURRENCY`, `EVALUATION_JUDGE_CONCURRENCY`, and optional `EVALUATION_EXECUTION_GLOBAL_LIMIT` / `EVALUATION_EXECUTION_USER_LIMIT`. Empty optional limits are omitted from environment configuration. Batch budget/configuration proofs remain immutable and independent of these operational revisions.

To change limits, create a JSON file with the next revision and complete limits, following `execution-policy.example.json`. Using the kernel deployment environment and its existing database credentials, run from `api`:

```sh
python -m scripts.evaluation_execution_policy --policy /path/to/execution-policy.json --expected-revision 1
```

This command activates revision 2 only if revision 1 is current. Exact repeated delivery is idempotent. Expected revision 0 permits initial revision 1 bootstrap. API credentials cannot mutate these kernel-only tables; this command neither grants privileges nor migrates schema. Update the kernel environment to the same revision and values, then restart workers. Old workers reject new evaluation work; accepted cancellation/wait/terminal transitions can still release existing execution slots.

Increasing or decreasing limits preserves all occupied slots, including prepared admission with an unknown outcome. A decrease below current occupancy blocks new acquisition until sufficient accepted releases occur. No TTL or restart clears holds. Reverting values requires another increasing policy revision, never reinstalling an older revision.

E06 composes its durable start transaction in this order: namespace and E03/E04 source prebinding, immutable budget binding, `uow.evaluation_execution.prepare(scope, run_id, guard.policy)`, then initial inbox enqueue and commit together. Do not pre-lock Run/inbox rows before preparation. Replaying equal preparation cannot acquire another slot. A released Run resumes only through accepted Run commands. `guard.reconcile(scope, run_id, expected_generation=...)` reads the verified journal under the same lock order and keeps a prepared hold when there is no accepted Run. It accepts no client state or expiry as release evidence. E06 still owns batch rows, retries, cancellation delivery, and scheduling.

The acceptance hook locks namespace, active policy, canonical global/user/workspace counters, and the Run lease before inbox claim and the event store's owner advisory lock. It writes the accepted Run state and occupancy in the acceptance transaction. The activity gate reads this state after all blocking budget locks and commits before `mark_call_started`; accepted `MarkActivityCallStarted` provides a second fence before handler dispatch. Ordinary Runs without evaluation bindings bypass evaluation pool locks. Execution release never releases physical unknown holds or environment fences.
