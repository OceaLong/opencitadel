from authlib.integrations.starlette_client import OAuth


class OAuthClients:
    def __init__(
        self,
        *,
        google_client_id: str = "",
        google_client_secret: str = "",
        github_client_id: str = "",
        github_client_secret: str = "",
    ) -> None:
        self.oauth = OAuth()
        if google_client_id and google_client_secret:
            self.oauth.register(
                name="google",
                client_id=google_client_id,
                client_secret=google_client_secret,
                server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                client_kwargs={"scope": "openid email profile"},
            )
        if github_client_id and github_client_secret:
            self.oauth.register(
                name="github",
                client_id=github_client_id,
                client_secret=github_client_secret,
                access_token_url="https://github.com/login/oauth/access_token",
                authorize_url="https://github.com/login/oauth/authorize",
                api_base_url="https://api.github.com/",
                client_kwargs={"scope": "read:user user:email"},
            )

    def get(self, provider: str):
        return self.oauth.create_client(provider)

    def enabled_providers(self) -> list[str]:
        # Only providers with configured client id + secret are registered, so
        # the UI can hide SSO buttons that would otherwise 400 on click.
        return [name for name in ("google", "github") if self.oauth.create_client(name) is not None]
