"""Language selector styling keeps the native control and internationalization contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_language_selector_remains_native_and_accessible():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "web/public.js").read_text(encoding="utf-8")

    assert '<select id="language" data-label="language" aria-label="Language"></select>' in html
    assert "Object.entries(languageNames)" in js
    assert "$('language').onchange=e=>setLanguage(e.target.value)" in js
    assert "document.addEventListener('languagechange'" in js


def test_customizable_picker_is_scoped_progressive_enhancement():
    css = (ROOT / "web/public-polish.css").read_text(encoding="utf-8")
    assert "@supports (appearance: base-select)" in css
    assert "@media (hover: hover) and (pointer: fine)" in css
    assert ".public-page .language-picker select::picker(select)" in css
    assert ".public-page .language-picker select::picker-icon" in css
    assert ".public-page .language-picker select:open::picker-icon" in css
    assert ".public-page .language-picker option:checked" in css
    assert ".public-page .language-picker option::checkmark" in css
    assert ".public-page .language-picker option:hover" in css
    assert ".public-page .language-picker option:focus" in css
    assert "max-block-size: min(436px, calc(100dvh - 96px))" in css
    assert "@media (max-width: 620px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "var(--accent)" in css
    assert css.count("{") == css.count("}")
    assert ".admin-locked" not in css
    assert "@import" not in css
