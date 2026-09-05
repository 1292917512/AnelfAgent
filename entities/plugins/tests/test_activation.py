"""插件激活层测试：技能冲突前缀、MCP 冲突前缀、事件与来源标记。"""




class TestSkillCollision:
    def test_user_skill_not_clobbered(self, manager, plugin_env):
        """用户已有同名技能目录时，插件技能加前缀入库，卸载不波及用户目录。"""
        user_skill = plugin_env.skills_dir / "demo_skill"
        user_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text("---\nname: demo_skill\n---\nmine", encoding="utf-8")

        pkg = plugin_env.make_plugin("demo")
        record = manager.install_from_source(str(pkg))
        assert record.skills == ["demo__demo_skill"]
        assert (plugin_env.skills_dir / "demo__demo_skill" / ".anelf_plugin").is_file()
        # 用户目录未被覆盖
        assert "mine" in (user_skill / "SKILL.md").read_text(encoding="utf-8")

        manager.remove("demo")
        assert not (plugin_env.skills_dir / "demo__demo_skill").exists()
        assert user_skill.is_dir()  # 用户技能保留


class TestMcpCollision:
    def test_server_name_conflict_prefixed(self, manager, plugin_env):
        """已有同名 MCP server 时，插件 server 加前缀合并。"""
        from entities.mcp.config import MCPServerStore

        MCPServerStore().create_server("demo_srv", {"url": "http://127.0.0.1:18888/sse"})
        pkg = plugin_env.make_plugin("demo")
        record = manager.install_from_source(str(pkg))
        assert record.mcp_servers == ["demo__demo_srv"]
        servers = plugin_env.read_mcp_servers()
        assert servers["demo__demo_srv"]["plugin"] == "demo"
        # 原有 server 无 plugin 标记
        assert "plugin" not in servers["demo_srv"]

        manager.remove("demo")
        servers = plugin_env.read_mcp_servers()
        assert "demo__demo_srv" not in servers
        assert "demo_srv" in servers  # 原有 server 保留


class TestActivationToleration:
    def test_broken_tools_module(self, manager, plugin_env):
        """工具模块语法错误时安装成功、工具为空（单组件失败不阻断整体）。"""
        pkg = plugin_env.make_plugin("demo")
        (pkg / "tools.py").write_text("def broken(:\n", encoding="utf-8")
        record = manager.install_from_source(str(pkg))
        assert record.tools == []
        assert record.skills == ["demo_skill"]  # 其余组件照常激活


class TestPluginEntity:
    def test_plugin_entity_registered(self, manager, plugin_env):
        from core.entity import EntityRegistry, EntityType

        pkg = plugin_env.make_plugin("demo")
        manager.install_from_source(str(pkg))
        entity = EntityRegistry.get("plugin:demo")
        assert entity is not None
        assert entity.entity_type == EntityType.PLUGIN
        assert entity.group == "plugins"
        assert entity.meta["version"] == "1.0.0"
        assert entity.meta["tools"] == ["demo_ping"]


class TestActivateInstalled:
    def test_activate_on_discovery(self, manager, plugin_env, monkeypatch):
        """启动激活：已安装且启用的插件全部激活，禁用与缺负载的跳过。"""
        from entities.plugins import activation

        pkg = plugin_env.make_plugin("demo")
        manager.install_from_source(str(pkg))
        manager.toggle("demo", False)
        pkg2 = plugin_env.make_plugin("live")
        manager.install_from_source(str(pkg2))

        # 模拟重启：注销工具/实体后重新激活
        monkeypatch.setattr(
            "core.plugins.get_plugin_manager", lambda: manager)
        count = activation.activate_installed_plugins()
        assert count == 1  # demo 禁用，仅 live 激活


class TestToolVisibility:
    def test_tools_regrouped_and_sleeping(self, manager, plugin_env):
        """插件工具改组到 plugin:<name>、默认沉睡、分组描述进目录。"""
        from core.entity import EntityRegistry

        pkg = plugin_env.make_plugin("demo")
        manager.install_from_source(str(pkg))
        entity = EntityRegistry.get("demo_ping")
        assert entity.group == "plugin:demo"
        assert entity.source == "plugin:demo"
        assert entity.allow_sleep is True
        assert entity.sleep_brief == "插件 demo（1 个工具）"
        # 目录（stable 层）可见该分组
        catalog = EntityRegistry.get_entity_catalog()
        entry = next(e for e in catalog if e["group"] == "plugin:demo")
        assert entry["tool_count"] == 1
        assert entry["description"] == "demo plugin"

    def test_auto_activate_in_conversation_scope(self, manager, plugin_env):
        """对话 scope 中安装：分组自动激活（装完即可用）。"""
        from agent.mind.tool_activation import bind_scope, reset_scope, tool_activation

        token = bind_scope("user_test")
        try:
            pkg = plugin_env.make_plugin("demo")
            manager.install_from_source(str(pkg))
            assert tool_activation.rounds_left("plugin:demo", scope="user_test") > 0
        finally:
            reset_scope(token)
            tool_activation.clear_scope("user_test")

    def test_no_auto_activate_at_boot_scope(self, manager, plugin_env):
        """启动期（无会话 scope）不自动激活，分组沉睡进目录。"""
        from agent.mind.tool_activation import tool_activation

        pkg = plugin_env.make_plugin("demo")
        manager.install_from_source(str(pkg))
        assert tool_activation.rounds_left("plugin:demo", scope="_global") == 0


class TestCommandsConversion:
    def test_commands_become_skills(self, manager, plugin_env):
        """插件 commands/*.md 转换为可手势触发的技能，卸载时回收。"""
        pkg = plugin_env.make_plugin("cmder", skill=False, mcp=False, tools=False)
        (pkg / "commands").mkdir()
        (pkg / "commands" / "review.md").write_text(
            "---\ndescription: 代码评审\nargument-hint: [path]\n---\n请评审 $ARGUMENTS 的代码\n",
            encoding="utf-8")
        (pkg / "commands" / "plain.md").write_text("无 frontmatter 的命令\n", encoding="utf-8")

        record = manager.install_from_source(str(pkg))
        assert sorted(record.skills) == ["cmder__plain", "cmder__review"]
        skill_md = (plugin_env.skills_dir / "cmder__review" / "SKILL.md").read_text(encoding="utf-8")
        assert "代码评审" in skill_md
        assert "user_invocable: true" in skill_md
        assert "请评审 $ARGUMENTS 的代码" in skill_md
        # 命令自身 frontmatter 不进入正文
        assert "argument-hint" not in skill_md.split("---")[2]

        manager.remove("cmder")
        assert not (plugin_env.skills_dir / "cmder__review").exists()
