import io
import time
import zipfile

import pytest

from epsilon.data import notes
from epsilon.skills import importer, store

SKILL = "---\nname: informe\ndescription: Resume un informe\n---\nPasos."


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    importer._staged.clear()


def make_zip(files, symlink=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (0o120777) << 16
            z.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def test_from_file_md_parses():
    item = importer.from_file("SKILL.md", SKILL.encode())

    assert (item.name, item.description, item.extra, item.skipped) == ("informe", "Resume un informe", {}, [])


def test_from_text_rejects_without_frontmatter():
    with pytest.raises(store.SkillError):
        importer.from_text("hola")


def test_from_file_rejects_non_utf8_and_unknown_type():
    with pytest.raises(store.SkillError):
        importer.from_file("x.md", b"\xff\xfe\x00")
    with pytest.raises(store.SkillError):
        importer.from_file("x.exe", b"MZ")


@pytest.mark.parametrize("prefix", ["", "mi-skill/"])
def test_zip_with_skill_md_at_root_or_in_single_folder(prefix):
    data = make_zip({f"{prefix}SKILL.md": SKILL, f"{prefix}ejemplos/uno.md": "hola"})

    item = importer.from_file("x.zip", data)

    assert item.name == "informe"
    assert item.extra == {"ejemplos/uno.md": b"hola"}


@pytest.mark.parametrize("evil", ["../../evil.md", "/etc/x.md", "a/../../b.md", "a\\..\\b.md"])
def test_zip_slip_paths_are_never_extracted(evil):
    data = make_zip({"SKILL.md": SKILL, evil: "malo"})

    item = importer.from_file("x.zip", data)

    assert item.extra == {}
    assert item.skipped  # se avisa que no se tomó


def test_zip_symlink_entries_are_skipped():
    item = importer.from_file("x.zip", make_zip({"SKILL.md": SKILL}, symlink="enlace.md"))

    assert "enlace.md" not in item.extra
    assert any("enlace.md" in s for s in item.skipped)


def test_zip_scripts_are_skipped_with_warning():
    item = importer.from_file("x.zip", make_zip({"SKILL.md": SKILL, "run.py": "print(1)", "x.sh": "ls"}))

    assert item.extra == {}
    assert len(item.skipped) == 2


def test_zip_limits(monkeypatch):
    many = {"SKILL.md": SKILL, **{f"f{i}.md": "x" for i in range(importer.MAX_FILES + 1)}}
    with pytest.raises(store.SkillError):
        importer.from_file("x.zip", make_zip(many))
    with pytest.raises(store.SkillError):
        importer.from_file("x.zip", make_zip({"SKILL.md": SKILL, "grande.md": "a" * (importer.MAX_FILE_BYTES + 1)}))
    monkeypatch.setattr(importer, "MAX_ZIP_BYTES", 50)
    with pytest.raises(store.SkillError):
        importer.from_file("x.zip", make_zip({"SKILL.md": SKILL}))


def test_zip_bomb_is_rejected_by_bytes_actually_read(monkeypatch):
    data = make_zip({"SKILL.md": SKILL, "bomba.md": "a" * 5_000})
    monkeypatch.setattr(importer, "MAX_FILE_BYTES", 1_000)

    with pytest.raises(store.SkillError):
        importer.from_file("x.zip", data)


def test_zip_without_skill_md_or_not_a_zip_fails():
    with pytest.raises(store.SkillError):
        importer.from_file("x.zip", make_zip({"otro.md": "x"}))
    with pytest.raises(store.SkillError):
        importer.from_file("x.zip", b"no es un zip")


def test_from_url_uses_get_and_parses(monkeypatch):
    monkeypatch.setattr(importer, "_get", lambda url: SKILL.encode())

    item = importer.from_url("https://ejemplo.com/SKILL.md")

    assert item.name == "informe" and item.source == "https://ejemplo.com/SKILL.md"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "::1", "169.254.169.254", "192.168.1.2"])
def test_get_rejects_private_hosts(monkeypatch, ip):
    monkeypatch.setattr(importer, "_resolve", lambda host: [ip])
    monkeypatch.setattr(importer, "_fetch", lambda url: (200, "", b"x"))

    with pytest.raises(store.SkillError):
        importer._get("https://ejemplo.com/SKILL.md")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.com/a", "http://", "javascript:alert(1)"])
def test_get_rejects_bad_schemes(url):
    with pytest.raises(store.SkillError):
        importer._get(url)


def test_get_rejects_redirect_to_private_host(monkeypatch):
    monkeypatch.setattr(importer, "_resolve", lambda host: ["10.0.0.5"] if host == "interno.lan" else ["93.184.216.34"])
    monkeypatch.setattr(importer, "_fetch", lambda url: (302, "http://interno.lan/x", b""))

    with pytest.raises(store.SkillError):
        importer._get("https://publico.com/SKILL.md")


def test_get_follows_public_redirect_and_returns_body(monkeypatch):
    monkeypatch.setattr(importer, "_resolve", lambda host: ["93.184.216.34"])
    answers = iter([(302, "https://otro.com/SKILL.md", b""), (200, "", b"hola")])
    monkeypatch.setattr(importer, "_fetch", lambda url: next(answers))

    assert importer._get("https://publico.com/a") == b"hola"


def test_get_rejects_too_many_redirects_and_errors(monkeypatch):
    monkeypatch.setattr(importer, "_resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(importer, "_fetch", lambda url: (302, "https://x.com/otra", b""))
    with pytest.raises(store.SkillError):
        importer._get("https://x.com/a")
    monkeypatch.setattr(importer, "_fetch", lambda url: (404, "", b""))
    with pytest.raises(store.SkillError):
        importer._get("https://x.com/a")


def test_fetch_is_blocked_in_tests():
    with pytest.raises(RuntimeError):
        importer._fetch("https://ejemplo.com/")


def test_stage_take_and_expiry(monkeypatch):
    item = importer.from_text(SKILL)
    token = importer.stage(item)

    assert importer.peek(token) is item
    assert importer.take(token) is item
    assert importer.take(token) is None

    token = importer.stage(item)
    now = time.monotonic()
    monkeypatch.setattr(importer.time, "monotonic", lambda: now + importer.TTL + 1)
    assert importer.peek(token) is None


def test_stage_caps_pending():
    item = importer.from_text(SKILL)
    first = importer.stage(item)
    for _ in range(importer.MAX_STAGED):
        importer.stage(item)

    assert importer.peek(first) is None  # el más viejo se descartó


def test_commit_writes_extras_inside_skill_folder():
    item = importer.from_file("x.zip", make_zip({"SKILL.md": SKILL, "ejemplos/uno.md": "hola"}))

    skill = importer.commit(item)

    assert skill.name == "informe" and skill.usable
    assert (notes.DATA_DIR / "skills" / "informe" / "ejemplos" / "uno.md").read_bytes() == b"hola"
    assert not (notes.DATA_DIR / "ejemplos").exists()


def test_commit_with_name_override_and_collision():
    item = importer.from_text(SKILL)

    assert importer.commit(item, "otro").name == "otro"
    with pytest.raises(store.SkillError, match="Ya existe"):
        importer.commit(item, "otro")
    with pytest.raises(store.SkillError):
        importer.commit(item, "ver")
