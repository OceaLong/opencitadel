"""Pure domain primitives for the event-sourced execution kernel."""

# Keep the package boundary side-effect free. Consumers import the exact domain
# module they depend on; eagerly re-exporting the graph creates policy/execution
# initialization cycles in cold migration and worker processes.
__all__: list[str] = []
