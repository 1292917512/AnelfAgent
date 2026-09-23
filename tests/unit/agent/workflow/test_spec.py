"""工作流规格校验与拓扑分层测试。"""

import pytest

from agent.workflow.spec import (
    WorkflowSpec,
    parse_spec,
    spec_hash,
    topo_layers,
    validate_spec,
)


def _spec(steps, name="w") -> WorkflowSpec:
    return WorkflowSpec(name=name, steps=steps)


class TestValidation:
    def test_valid_spec_passes(self):
        errors = validate_spec(_spec([
            {"key": "a", "kind": "ask", "goal": "g"},
            {"key": "b", "kind": "tool", "tool": "shell", "args": {"command": "ls"},
             "depends_on": ["a"], "gate": {"field": "exit_code", "equals": 0}},
        ]))
        assert errors == []

    def test_duplicate_and_bad_keys(self):
        errors = validate_spec(_spec([
            {"key": "a", "kind": "ask", "goal": "g"},
            {"key": "a", "kind": "ask", "goal": "g"},
            {"key": "bad key!", "kind": "ask", "goal": "g"},
        ]))
        assert any("重复" in e for e in errors)
        assert any("非法" in e for e in errors)

    def test_unknown_dependency(self):
        errors = validate_spec(_spec([
            {"key": "a", "kind": "ask", "goal": "g", "depends_on": ["ghost"]},
        ]))
        assert any("未定义步骤" in e for e in errors)

    def test_cycle_detected(self):
        errors = validate_spec(_spec([
            {"key": "a", "kind": "ask", "goal": "g", "depends_on": ["b"]},
            {"key": "b", "kind": "ask", "goal": "g", "depends_on": ["a"]},
        ]))
        assert any("环" in e for e in errors)

    def test_kind_field_mismatch(self):
        errors = validate_spec(_spec([
            {"key": "a", "kind": "ask", "tool": "shell"},
            {"key": "b", "kind": "tool", "goal": "g"},
            {"key": "c", "kind": "ask", "goal": "g", "gate": {"field": "x", "equals": 1}},
        ]))
        assert any("不接受 tool 字段" in e for e in errors)
        assert any("缺少 tool" in e for e in errors)
        assert any("gate 只适用于 tool" in e for e in errors)

    def test_continue_from_constraints(self):
        errors = validate_spec(_spec([
            {"key": "a", "kind": "tool", "tool": "shell"},
            {"key": "b", "kind": "ask", "goal": "g", "continue_from": "a"},
            {"key": "c", "kind": "ask", "goal": "g", "continue_from": "ghost"},
        ]))
        assert any("只能指向 ask" in e for e in errors)
        assert any("未定义步骤" in e for e in errors)

    def test_parse_spec_raises_with_full_list(self):
        with pytest.raises(Exception) as exc_info:
            parse_spec({"name": "", "steps": [{"key": "a", "kind": "ask"}]})
        assert "goal" in str(exc_info.value)

    def test_step_limit(self):
        steps = [{"key": f"s{i}", "kind": "ask", "goal": "g"} for i in range(65)]
        assert any("超限" in e for e in validate_spec(_spec(steps)))


class TestTopoLayers:
    def test_layering_respects_deps(self):
        spec = _spec([
            {"key": "b", "kind": "ask", "goal": "g", "depends_on": ["a"]},
            {"key": "a", "kind": "ask", "goal": "g"},
            {"key": "c", "kind": "ask", "goal": "g", "depends_on": ["a", "b"]},
        ])
        assert topo_layers(spec) == [["a"], ["b"], ["c"]]

    def test_parallel_steps_share_layer(self):
        spec = _spec([
            {"key": "a", "kind": "ask", "goal": "g"},
            {"key": "b", "kind": "ask", "goal": "g"},
            {"key": "c", "kind": "ask", "goal": "g", "depends_on": ["a", "b"]},
        ])
        assert topo_layers(spec) == [["a", "b"], ["c"]]

    def test_continue_from_is_implicit_edge(self):
        spec = _spec([
            {"key": "a", "kind": "ask", "goal": "g"},
            {"key": "b", "kind": "ask", "goal": "g", "continue_from": "a"},
        ])
        assert topo_layers(spec) == [["a"], ["b"]]


class TestHash:
    def test_hash_stable_and_order_insensitive(self):
        a = _spec([{"key": "x", "kind": "ask", "goal": "g"}])
        b = _spec([{"key": "x", "kind": "ask", "goal": "g"}])
        assert spec_hash(a) == spec_hash(b)
        c = _spec([{"key": "x", "kind": "ask", "goal": "different"}])
        assert spec_hash(a) != spec_hash(c)
