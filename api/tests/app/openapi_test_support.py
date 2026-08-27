"""Side-effect-free OpenAPI application used by schema contract tests."""

from app.main import create_app
from core.config import DeploymentSettings

app = create_app(DeploymentSettings(env="test"))
