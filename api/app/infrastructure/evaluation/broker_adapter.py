"""Kernel E04 adapter: bounded typed RPCs; no local Docker client or command proxy."""

import httpx

from app.domain.evaluation.errors import EnvironmentTransportUnknown
from app.infrastructure.adapters.evaluation_environment import DockerNetworkEnvironmentAdapter


class BrokerEnvironmentAdapter(DockerNetworkEnvironmentAdapter):
    def __init__(self, *, broker_url, broker_token, request_observer=None, **kwargs):
        super().__init__(command=self.forbidden_command, **kwargs)
        self.broker_url, self.broker_token = broker_url.rstrip("/"), broker_token
        self.request_observer = request_observer

    @staticmethod
    async def forbidden_command(*args, **kwargs):
        raise ValueError("environment_command_proxy_forbidden")

    async def request(self, action, body):
        observed = (
            self.request_observer.before(body)
            if action == "lifecycle" and self.request_observer is not None
            else None
        )
        try:
            async with httpx.AsyncClient(
                timeout=150, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.post(
                    self.broker_url + "/v1/evaluation/" + action,
                    json=body,
                    headers={"Authorization": "Bearer " + self.broker_token},
                )
        except httpx.RequestError as error:
            # Never retry a mutation whose broker receipt may already be committed.
            raise EnvironmentTransportUnknown("environment_broker_outcome_unknown") from error
        if response.status_code != 200:
            raise ValueError("environment_broker_unavailable")
        result = response.json()
        if observed is not None:
            self.request_observer.after(observed, result)
        return result

    async def phase(self, lease, operation, version, targets):
        self.validate(version, targets)
        return await self.request(
            "lifecycle",
            {
                "lease": lease.model_dump(mode="json"),
                "operation": operation.model_dump(mode="json"),
                "version": version.model_dump(mode="json"),
            },
        )

    prepare = phase
    reset = phase
    verify = phase
    cleanup = phase

    async def case(self, lease):
        return (await self.request("case", {"lease": lease.model_dump(mode="json")}))["id"]

    async def check_runtime(self, lease):
        await self.request("check", {"lease": lease.model_dump(mode="json")})

    async def control(self, lease, payload):
        return await self.request(
            "control",
            {
                "lease": lease.model_dump(mode="json"),
                **{key: payload[key] for key in ("path", "method", "body", "headers")},
            },
        )

    async def browser(self, lease, url):
        return await self.request("browser", {"lease": lease.model_dump(mode="json"), "url": url})

    async def target(self, lease, target_id, body):
        return await self.request(
            "target", {"lease": lease.model_dump(mode="json"), "target_id": target_id, "body": body}
        )
