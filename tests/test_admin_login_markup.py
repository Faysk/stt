"""Regression checks for the intentionally text-free admin login."""
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LoginParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.elements = []
        self.visible_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and attrs.get("id") == "admin-login":
            self.inside = True
        if self.inside:
            self.elements.append((tag, attrs))

    def handle_data(self, data):
        if self.inside and data.strip():
            self.visible_text.append(data.strip())

    def handle_endtag(self, tag):
        if self.inside and tag == "section":
            self.inside = False


def test_admin_login_has_qr_and_discreet_legacy_recovery():
    markup = LoginParser()
    markup.feed((ROOT / "web/admin.html").read_text(encoding="utf-8"))
    elements = {attrs['id']: (tag, attrs) for tag, attrs in markup.elements if 'id' in attrs}
    assert elements['login-qr'][0] == 'img'
    assert elements['login-qr'][1]['alt']
    assert elements['qr-start'][0] == 'button'
    assert elements['qr-code'][0] == 'strong'
    assert elements['qr-status'][1]['aria-live'] == 'polite'
    assert elements['legacy-login'][0] == 'details'
    assert elements['admin-key'][1]['type'] == 'password'
    assert elements['admin-key'][1]['autocomplete'] == 'off'
    assert elements['login'][1]['aria-label']
    auth = (ROOT / 'web/admin-auth.js').read_text(encoding='utf-8')
    assert 'localStorage' not in auth and 'sessionStorage' not in auth
    assert 'innerHTML' not in auth.replace('never innerHTML', '')


def test_admin_login_is_scoped_and_keeps_keyboard_submission():
    css = (ROOT / "web/ui.css").read_text(encoding="utf-8")
    js = (ROOT / "web/dashboard.js").read_text(encoding="utf-8")
    assert ".admin-locked .admin-main #admin-login{" in css
    assert ".admin-locked #admin-key:focus-visible{" in css
    assert ".admin-locked #login{" in css
    assert "$('admin-key').onkeydown=e=>{if(e.key==='Enter')$('login').click()}" in js
