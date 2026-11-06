"""GitHub 默认和命名账户的凭据声明。"""

from agent.channel import CredentialField, register_provider_key

from .config import GitHubAccount

_USER_FIELDS = (
    CredentialField("api_key", "User token"),
    CredentialField("refresh_token", "Refresh token", required=False),
    CredentialField("expires_at", "Token expiry", required=False),
)


def register_defaults() -> None:
    """登记默认用户令牌和 App 私钥。"""
    register_provider_key("github", domain="channel", title="GitHub", fields=_USER_FIELDS,
                          owner_title="GitHub", destination="/channels?channel=github")
    register_provider_key("github_app", domain="channel", title="GitHub App",
                          fields=(CredentialField("api_key", "Private key (PEM)", multiline=True),),
                          owner_title="GitHub", destination="/channels?channel=github")


def register_accounts(accounts: list[GitHubAccount]) -> None:
    """登记账户引用的用户令牌、私钥与回调密钥。"""
    for account in accounts:
        entries: list[tuple[str, str, tuple[CredentialField, ...]]] = [(account.token_ref, "User token", _USER_FIELDS)]
        if account.auth_mode == "app":
            entries.append((account.app_private_key_ref, "Private key (PEM)",
                            (CredentialField("api_key", "Private key (PEM)", multiline=True),)))
        entries.extend((reference, title, (CredentialField("api_key", title),)) for reference, title in (
            (account.client_secret_ref, "OAuth client secret"), (account.webhook_secret_ref, "Webhook secret"),
        ) if reference)
        for reference, title, fields in entries:
            register_provider_key(reference, domain="channel", title=f"GitHub · {account.name} · {title}",
                                  fields=fields, owner_title="GitHub", destination="/channels?channel=github")
