from __future__ import annotations

import pytest

from agent.memory.cognee.graph_html import sanitize_cognee_graph_html


def test_sanitize_inlines_local_d3_and_strips_google_fonts() -> None:
    raw = """
    <html><head>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    </head><body>ok</body></html>
    """
    out = sanitize_cognee_graph_html(raw)
    assert 'src="https://d3js.org' not in out
    assert "fonts.googleapis.com" not in out
    assert "fonts.gstatic.com" not in out
    assert out.count("<script>") >= 1
    assert "zoomIdentity" in out
    assert "ok" in out


def test_sanitize_rejects_html_without_d3() -> None:
    with pytest.raises(RuntimeError, match="d3"):
        sanitize_cognee_graph_html("<html><body>no graph</body></html>")
