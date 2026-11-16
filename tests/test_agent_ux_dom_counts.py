"""Agent-UX structural counts come from elements, not HTML-looking text."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import agent_ux_check  # noqa: E402

MARKUP = '<main><button>Buy</button><a href="/shop">Shop</a><div onclick="buy()">Buy</div></main>'


@pytest.mark.parametrize(
    "html",
    [
        "<!-- " + MARKUP + " -->",
        "<script>const markup = '" + MARKUP + "';</script>",
        "<style>body::before { content: '" + MARKUP + "'; }</style>",
        '<button-group></button-group><nav-menu></nav-menu><a-link href="/shop"></a-link>',
    ],
)
def test_non_elements_do_not_supply_structural_signals(html):
    findings = agent_ux_check.analyze_html(html)

    assert findings["real_buttons"] == 0
    assert findings["real_anchors"] == 0
    assert findings["div_onclick_widgets"] == 0
    assert findings["semantic_landmarks"] == 0


@pytest.mark.parametrize(
    "html",
    [
        '<div data-onclick="buy()">Buy</div>',
        '<div title=\'onclick="buy()"\'>Buy</div>',
    ],
)
def test_data_and_text_attributes_are_not_click_handlers(html):
    assert agent_ux_check.analyze_html(html)["div_onclick_widgets"] == 0


@pytest.mark.parametrize("handler", ['onclick="buy()"', "onclick='buy()'", "onclick=buy()"])
def test_real_click_handlers_are_counted_regardless_of_quoting(handler):
    assert agent_ux_check.analyze_html(f"<div {handler}>Buy</div>")["div_onclick_widgets"] == 1


def test_real_element_counts_are_preserved():
    findings = agent_ux_check.analyze_html(MARKUP)

    assert findings["real_buttons"] == 1
    assert findings["real_anchors"] == 1
    assert findings["div_onclick_widgets"] == 1
    assert findings["semantic_landmarks"] == 1


def test_audit_does_not_penalize_script_template_text(monkeypatch):
    html = ("<html><body><main>Product information</main><script>const markup = '"
            + MARKUP + "';</script></body></html>")
    monkeypatch.setattr(
        agent_ux_check,
        "render_page",
        lambda *args, **kwargs: {
            "url": "https://example.com/products",
            "status_code": 200,
            "content": html,
            "accessibility_tree": {"role": "document", "children": []},
        },
    )

    report = agent_ux_check.audit("https://example.com/products")

    assert report["score"] == 100
    assert report["html_findings"]["div_onclick_widgets"] == 0
    assert report["html_findings"]["semantic_landmarks"] == 1
    assert report["issues"] == []
