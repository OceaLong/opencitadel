"""ASGI factory: uvicorn scripts.execution_capacity.api_main:create_app --factory."""


def create_app():
    from scripts.execution_capacity.composition import load_trusted_runtime

    from app.main import create_app as normal_app

    settings, factories = load_trusted_runtime()
    return normal_app(settings, runtime_factory=factories.api)
