from channels.minecraft.access import MinecraftAccessPolicy


def test_owner_and_explicit_grants_are_case_insensitive() -> None:
    policy = MinecraftAccessPolicy()
    policy.configure(owner="HostPlayer", grants=["Builder"])

    assert policy.allow("hostplayer", auto_authorize_online=False)
    assert policy.role("BUILDER", auto_authorize_online=False) == "grant"
    assert not policy.allow("Guest", auto_authorize_online=False)


def test_online_authorization_uses_current_player_list_only() -> None:
    policy = MinecraftAccessPolicy()
    policy.configure(owner="", grants=[])
    policy.observe(["AnelfProbe"])

    assert policy.allow("AnelfProbe", auto_authorize_online=True)
    assert policy.role("AnelfProbe", auto_authorize_online=True) == "online"
    assert not policy.allow("OfflineName", auto_authorize_online=True)
