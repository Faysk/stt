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


def test_admin_login_contains_only_accessible_key_field_and_icon_button():
    markup = LoginParser()
    markup.feed((ROOT / "web/admin.html").read_text(encoding="utf-8"))
    assert [tag for tag, _ in markup.elements] == ["section", "input", "button", "svg", "path"]
    assert markup.visible_text == []

    key = markup.elements[1][1]
    submit = markup.elements[2][1]
    icon = markup.elements[3][1]
    assert key["id"] == "admin-key"
    assert key["type"] == "password"
    assert key["aria-label"]
    assert key.get("autocomplete") == "off"
    assert submit["id"] == "login"
    assert submit["type"] == "button"
    assert submit["aria-label"]
    assert icon["aria-hidden"] == "true"


def test_admin_login_is_scoped_and_keeps_keyboard_submission():
    css = (ROOT / "web/ui.css").read_text(encoding="utf-8")
    js = (ROOT / "web/dashboard.js").read_text(encoding="utf-8")
    assert ".admin-locked .admin-main #admin-login{" in css
    assert ".admin-locked #admin-key:focus-visible{" in css
    assert ".admin-locked #login{" in css
    assert "$('admin-key').onkeydown=e=>{if(e.key==='Enter')$('login').click()}" in js
