"""Dedicated trusted kernel entry point with the full normal lifecycle."""

import asyncio


async def main():
    from scripts.execution_capacity.composition import load_trusted_runtime

    from app.execution_kernel_main import run_kernel, start_kernel_metrics_server
    from app.infrastructure.logging import setup_logging
    from app.observability.otel import setup_observability

    settings, factories = load_trusted_runtime()
    setup_logging(settings)
    setup_observability(settings=settings)
    start_kernel_metrics_server(settings)
    await run_kernel(settings, runtime_factory=factories.kernel)


if __name__ == "__main__":
    asyncio.run(main())
