import re

from tests.channels.web.helpers import PAGES, menu_items, page, static_path


def test_skills_page_served(client):
    reply = client.get("/skills")

    assert reply.status_code == 200 and "Skills" in reply.text


def test_menu_has_skills_in_every_page_in_same_place():
    expected = None
    for name in PAGES:
        items = [item[0] for item in menu_items(page(name))]
        assert "/skills" in items, name
        assert items.index("/skills") == items.index("/conectores") + 1, name
        expected = expected or items
        assert items == expected, name


def test_skills_page_marks_itself_current_and_no_other_page_does():
    for name in PAGES:
        current = re.findall(r'<a class="nav-item" href="([^"]+)" aria-current="page"', page(name))
        assert current == (["/skills"] if name == "skills.html" else current), name
    assert 'href="/skills" aria-current="page"' in page("skills.html")


def test_skills_js_uses_textcontent_not_innerhtml():
    js = static_path("skills.js").read_text(encoding="utf-8")

    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js
    assert "textContent" in js


def test_no_inline_styles_or_scripts_in_skills_html():
    html = page("skills.html")

    assert "style=" not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html)


def test_review_dialog_exists_and_has_save_and_cancel():
    html = page("skills.html")

    for needed in ('id="skill-review"', 'id="skill-review-save"', 'id="skill-review-cancel"', 'id="skill-review-text"'):
        assert needed in html


def test_add_menu_has_three_paths():
    html = page("skills.html")

    for needed in ('id="skill-tab-file"', 'id="skill-tab-url"', 'id="skill-tab-chat"', 'type="file"', 'id="skill-url"'):
        assert needed in html


def test_save_always_goes_through_preview_and_confirmed():
    js = static_path("skills.js").read_text(encoding="utf-8")

    assert "/api/skills/preview-file" in js and "/api/skills/preview" in js
    assert js.count("confirmed: true") >= 2  # guardar nueva y guardar cambios, y confirmar de nuevo


def test_skills_js_is_loaded_with_the_shell():
    html = page("skills.html")

    assert '/static/js/skills.js' in html and '/static/js/shell.js' in html
    assert "conectores.js" not in html
