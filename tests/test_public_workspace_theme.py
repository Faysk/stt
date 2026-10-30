"""Layout, UI theme and non-deceptive loading regressions."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_public_header_and_workspace_contract():
    html=(ROOT/"web/index.html").read_text(encoding="utf-8")
    nav=html.split('<header class="public-header">',1)[1].split("</header>",1)[0]
    assert 'id="health"' in nav and 'id="language"' in nav and 'id="theme-toggle"' in nav
    assert 'id="count"' not in html
    assert 'class="badge neutral">24h' not in html
    assert html.count('id="library-count"')==1
    assert html.index('class="public-intro"')<html.index('class="library-top"')<html.index('class="submission-column"')<html.index('id="library"')
    assert 'aria-labelledby="library-title"' in html
    assert 'id="jobs" aria-live="polite" aria-busy="true"' in html

def test_theme_is_isolated_and_early():
    html=(ROOT/"web/index.html").read_text(encoding="utf-8")
    admin=(ROOT/"web/admin.html").read_text(encoding="utf-8")
    assert html.index('src="/theme.js"')<html.index('href="/ui.css"')
    assert html.index('href="/public-polish.css"')<html.index('href="/public-theme.css"')
    assert 'src="/theme.js"' not in admin and 'href="/public-theme.css"' not in admin
    script=(ROOT/"web/theme.js").read_text(encoding="utf-8")
    assert "'stt.theme'" in script and "prefers-color-scheme: dark" in script
    assert "localStorage.setItem" in script

def test_light_palette_layout_and_feedback():
    css=(ROOT/"web/public-theme.css").read_text(encoding="utf-8")
    assert css.count("{")==css.count("}")
    assert "--bg:#e9eff5" in css and "--text:#17293e" in css
    assert "--accent:#246dbb" in css
    assert 'grid-template-areas:"intro library-top" "submit library" "privacy privacy"' in css
    assert 'grid-template-areas:"intro" "submit" "library-top" "library" "privacy"' in css
    assert "prefers-reduced-motion:reduce" in css
    assert "theme-toggle:focus-visible" in css and "library-loading" in css and "is-loading" in css
    assert "@import" not in css and "url(" not in css
    js=(ROOT/"web/public.js").read_text(encoding="utf-8")
    assert "setUploadMeter(n)" in js and "Number.isFinite(j.progress)" in js
    assert "classList.add('is-loading')" in js and "classList.remove('is-loading')" in js
    assert "t('expires')" in js
