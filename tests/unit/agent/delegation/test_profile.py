"""子代理档案 schema（agent.delegation.profile）单元测试。"""

from __future__ import annotations

from agent.delegation.profile import (
    AgentFacets,
    SubAgentProfile,
    normalize_instructions,
    normalize_tag_list,
    parse_output_schema,
    valid_sub_agent_name,
)


class TestProfileRoundtrip:
    def test_full_facets_survive_roundtrip(self) -> None:
        facets = AgentFacets(
            instructions="只读调研，禁止修改文件",
            tool_tags=["heartbeat", "web"],
            blocked_tools=["run_shell_command"],
            output_schema={"summary": "", "facts": []},
        )
        profile = SubAgentProfile(name="researcher", models=["m1"], facets=facets)
        restored = AgentFacets.from_dict(profile.to_dict())
        assert restored == facets

    def test_empty_facets_omitted_from_dict(self) -> None:
        profile = SubAgentProfile(name="plain", models=["m1"])
        d = profile.to_dict()
        assert "instructions" not in d
        assert "tool_tags" not in d
        assert "output_schema" not in d

    def test_facets_truthiness(self) -> None:
        assert not AgentFacets()
        assert AgentFacets(instructions="x")
        assert AgentFacets(tool_tags=["web"])
        assert AgentFacets(output_schema={"a": 1})


class TestNormalization:
    def test_tag_list_variants(self) -> None:
        assert normalize_tag_list("heartbeat, web", max_items=12) == ["heartbeat", "web"]
        assert normalize_tag_list(["a", "a", "b"], max_items=12) == ["a", "b"]
        assert normalize_tag_list(None, max_items=12) == []
        assert normalize_tag_list("a，b c", max_items=12) == ["a", "b", "c"]
        assert len(normalize_tag_list(" ".join(f"t{i}" for i in range(30)), max_items=12)) == 12

    def test_instructions_clamped(self) -> None:
        assert len(normalize_instructions("x" * 9000)) == 4000
        assert normalize_instructions("  守则  ") == "守则"

    def test_output_schema_accepts_dict_and_json_string(self) -> None:
        schema, err = parse_output_schema({"a": 1})
        assert err is None and schema == {"a": 1}
        schema, err = parse_output_schema('{"b": [1, 2]}')
        assert err is None and schema == {"b": [1, 2]}
        schema, err = parse_output_schema("")
        assert err is None and schema is None

    def test_output_schema_rejects_invalid(self) -> None:
        assert parse_output_schema("not json")[1] is not None
        assert parse_output_schema([1, 2])[1] is not None  # 非 object
        long = "{" + ",".join(f'"k{i}": {i}' for i in range(500)) + "}"
        assert parse_output_schema(long)[1] is not None  # 超长


class TestNames:
    def test_valid_names(self) -> None:
        assert valid_sub_agent_name("researcher")
        assert valid_sub_agent_name("web-2_fetch")
        assert not valid_sub_agent_name("2fast")
        assert not valid_sub_agent_name("带中文")
        assert not valid_sub_agent_name("")
