"""Guard the public STT UI contract while refining the visual surface.

The stylesheet is strictly additive: it does not alter auth, uploads, session
storage, API routes or the operator console.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_stylesheet_loads_after_base_only_on_public_page():
    page = (ROOT / "web/index.html").read_text(encoding="utf-8")
    admin = (ROOT / "web/admin.html").read_text(encoding="utf-8")
    assert page.count('href="/ui.css"') == 1
    assert page.count('href="/public-polish.css"') == 1
    assert page.index('href="/ui.css"') < page.index('href="/public-polish.css"')
    assert "public-polish.css" not in admin


def test_public_interactions_and_privacy_contract_remain_present():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    for identifier in [
        'id="file-tab"', 'id="link-tab"', 'id="drop"', 'id="file"',
        'id="url"', 'id="submit"', 'id="upload-progress"',
        'id="health"', 'id="library"', 'id="jobs"', 'id="search"',
        'id="preview"', 'id="preview-text"', 'id="language"',
    ]:
        assert identifier in html, f"Missing original UI control: {identifier}"
    for key in ['data-filter="all"', 'data-filter="active"', 'data-filter="done"',
                'data-reader-mode="timeline"', 'data-reader-mode="plain"',
                'class="privacy"', '24h']:
        assert key in html


def test_refinement_scoped_accessible_and_motion_safe():
    css = (ROOT / "web/public-polish.css").read_text(encoding="utf-8")
    assert css.count("{") == css.count("}"), "CSS braces must stay balanced"
    assert "@import" not in css and "@font-face" not in css
    assert "url(" not in css, "No extra tracking, requests or external assets"

    for selector in [
        ".public-page .public-header", ".public-page .public-main",
        ".public-page .public-intro h1", ".public-page .upload-panel.panel",
        ".public-page .drop", ".public-page .link-panel",
        ".public-page .upload-bottom", ".public-page .steps",
        ".public-page .library", ".public-page .transcript-card",
        ".public-page .format-button", ".public-page .privacy",
        ".public-page .reader-dialog", ".public-page .reader-paragraph",
    ]:
        assert selector in css, f"Missing refinement: {selector}"

    assert ".public-page .drop:focus-visible" in css
    assert ".public-page .button:focus-visible" in css
    assert "min-width: 1100px" in css
    assert "max-width: 720px" in css
    assert "max-width: 620px" in css
    assert "max-width: 380px" in css
    assert "prefers-reduced-motion: reduce" in css


def test_brand_palette_is_not_replaced():
    base_css = (ROOT / "web/ui.css").read_text(encoding="utf-8")
    polish = (ROOT / "web/public-polish.css").read_text(encoding="utf-8")
    for token in ["--bg:#0e1117", "--panel:#151a23", "--accent:#62adff",
                  "--green:#76d8a6", "--yellow:#e6cc72", "--red:#f5848d"]:
        assert token in base_css
    assert "var(--accent)" in polish
    assert ".admin-locked" not in polish and ".admin-shell" not in polish
