"""Session-binding resolution helpers used by TaskRunnerFactory.create_runner.

Split out of task_runner_factory.py (phase-4 engineering-quality Task 3) so
each governed-session binding (Ops Patrol, Ops Patrol Remediation, codebase/
knowledge-base resource authorization) can be read, tested, and reasoned
about independently of the ~900-line factory. TaskRunnerFactory remains the
sole caller and owns wiring the results into the runner it builds; the
modules here own no state of their own.
"""
