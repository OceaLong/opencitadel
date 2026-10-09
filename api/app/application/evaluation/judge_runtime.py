"""Current durable judge authority before model assembly and physical sends."""

from app.domain.models.authorization import AuthorizationContext


class JudgeRuntime:
    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def authorize(self, scope, request, context):
        async with self.uow_factory(AuthorizationContext.system("execution-kernel")) as work:
            intent = await work.evaluation_judge.authorize_activity(scope, request, context)
            config = await work.evaluation_configuration.get_version(
                scope, "config", intent["config_id"]
            )
            return {
                "materials": intent["materials"],
                "model_id": config["selection"]["model_id"],
                "temperature": config["selection"].get("temperature"),
            }
