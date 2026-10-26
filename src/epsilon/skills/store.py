from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import shutil
import threading

from epsilon import tools
from epsilon.data import notes
from epsilon.settings import config

# Las skills: DATA_DIR/skills/<nombre>/SKILL.md (frontmatter `name` y `description`, y debajo las instrucciones).
# El estado (prendida y el hash del texto que la persona confirmó) vive aparte en DATA_DIR/skills.json: si el
# SKILL.md cambia en disco, el hash ya no coincide y la skill queda sin usar hasta confirmarla de nuevo.
# Es solo texto: nada de acá ejecuta nada. Todo acceso por notes.safe_path.

DIR = "skills"
STATE = "skills.json"
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAX_BODY = 8000
MAX_DESCRIPTION = 500
MAX_SKILLS = 50
RESERVED = {"ia", "chat", "nuevo", "skills"}  # comandos que atiende otro lado (Telegram) o el propio sistema

_lock = threading.Lock()


class SkillError(Exception):
    """No se pudo guardar o leer la skill; el texto va para la persona."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    enabled: bool
    changed: bool  # el SKILL.md de disco ya no es el que se confirmó

    @property
    def usable(self) -> bool:
        return self.enabled and not self.changed


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse(text: str) -> tuple[str, str, str]:
    """(nombre, descripción, cuerpo) de un SKILL.md. Sin dependencia de YAML: líneas «clave: valor»."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        raise SkillError("El archivo tiene que empezar con un encabezado entre «---» con name y description.")
    head, sep, body = text[4:].partition("\n---")
    if not sep:
        raise SkillError("Falta el «---» que cierra el encabezado.")
    fields: dict[str, str] = {}
    for line in head.split("\n"):
        key, colon, value = line.partition(":")
        if colon:
            fields[key.strip().lower()] = value.strip().strip("\"'").strip()
    name, description = fields.get("name", ""), fields.get("description", "")
    if not name:
        raise SkillError("El encabezado no tiene name.")
    if not description:
        raise SkillError("El encabezado no tiene description (una frase que diga para qué sirve).")
    return name, description, body.strip()


def check_name(name: str) -> None:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise SkillError("El nombre va en minúsculas, sin espacios, con letras, números, - o _ (hasta 32 caracteres).")
    if name in RESERVED or name in tools.command_names():
        raise SkillError(f"«{name}» ya es un comando de Epsilon: elegí otro nombre.")


def _folder(name: str):
    """La carpeta de una skill. Una carpeta que es un enlace simbólico no vale: safe_path lo seguiría y
    guardar o borrar actuaría sobre otro lugar de DATA_DIR."""
    if (notes.safe_path(DIR) / name).is_symlink():
        raise SkillError("Esa carpeta de skill no es válida (es un enlace).")
    return notes.safe_path(f"{DIR}/{name}")


def _read_state() -> dict:
    try:
        data = json.loads(notes.safe_path(STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = data.get("skills", {}) if isinstance(data, dict) else {}
    return found if isinstance(found, dict) else {}


def _write_state(state: dict) -> None:
    path = notes.safe_path(STATE)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"skills": state}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def _write_file(path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def get(name: str) -> Skill | None:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return None
    try:
        text = (_folder(name) / "SKILL.md").read_text(encoding="utf-8")
        _, description, body = parse(text)
    except (OSError, UnicodeDecodeError, SkillError, ValueError):
        return None
    entry = _read_state().get(name)
    entry = entry if isinstance(entry, dict) else {}
    return Skill(
        name,
        description[:MAX_DESCRIPTION],
        body,
        entry.get("enabled") is True,
        entry.get("sha") != sha(text),
    )


def read_text(name: str) -> str:
    """El SKILL.md tal como está en disco ("" si no existe)."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return ""
    try:
        return (_folder(name) / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return ""


def list_all() -> list[Skill]:
    try:
        names = sorted(p.name for p in notes.safe_path(DIR).iterdir() if p.is_dir())
    except OSError:
        return []
    return [skill for name in names if (skill := get(name))]


def _clean_extra_path(rel: str) -> PurePosixPath:
    path = PurePosixPath(rel)
    if not rel or path.is_absolute() or "\\" in rel or ".." in path.parts or path.name == "SKILL.md" and len(path.parts) == 1:
        raise SkillError(f"Ruta no permitida dentro de la skill: {rel[:60]}")
    return path


def save(name: str, text: str, extra: dict[str, bytes] | None = None, *, overwrite: bool = False) -> Skill:
    """Guarda (o, con `overwrite`, reemplaza) la skill y la deja prendida y confirmada con el texto de ahora."""
    check_name(name)
    text = config.redact(text)
    _, _, body = parse(text)
    if len(body) > MAX_BODY:
        raise SkillError(f"Las instrucciones son muy largas ({len(body)} de {MAX_BODY} caracteres).")
    paths = {_clean_extra_path(rel): data for rel, data in (extra or {}).items()}
    with _lock:
        exists = (_folder(name) / "SKILL.md").exists()
        if exists and not overwrite:
            raise SkillError("Ya existe una skill con ese nombre.")
        if not exists and len(list_all()) >= MAX_SKILLS:
            raise SkillError(f"Ya hay {MAX_SKILLS} skills: borrá alguna antes de agregar otra.")
        folder = _folder(name)
        for rel, data in paths.items():
            target = (folder / rel).resolve()
            if not target.is_relative_to(folder):
                raise SkillError(f"Ruta no permitida dentro de la skill: {rel}")
            _write_file(target, data)
        _write_file(folder / "SKILL.md", text.encode("utf-8"))
        state = _read_state()
        state[name] = {"enabled": True, "sha": sha(text)}
        _write_state(state)
    return get(name)


def _require(name: str) -> None:
    if get(name) is None:
        raise SkillError("No existe esa skill.")


def set_enabled(name: str, enabled: bool) -> Skill:
    _require(name)
    with _lock:
        state = _read_state()
        entry = state.get(name) if isinstance(state.get(name), dict) else {}
        state[name] = {"enabled": bool(enabled), "sha": entry.get("sha", "")}
        _write_state(state)
    return get(name)


def confirm(name: str, expected_sha: str | None = None) -> Skill:
    """La persona aceptó lo que hay hoy en disco: se anota su hash y se deja prendida. Con `expected_sha` (el del
    texto que vio) se niega si el archivo volvió a cambiar; y valida igual que `save` (un SKILL.md editado a mano
    no se salta los límites)."""
    _require(name)
    text = (_folder(name) / "SKILL.md").read_text(encoding="utf-8")
    if expected_sha is not None and sha(text) != expected_sha:
        raise SkillError("La skill cambió otra vez mientras la mirabas: revisala de nuevo antes de confirmarla.")
    _, _, body = parse(text)
    if len(body) > MAX_BODY:
        raise SkillError(f"Las instrucciones son muy largas ({len(body)} de {MAX_BODY} caracteres).")
    with _lock:
        state = _read_state()
        state[name] = {"enabled": True, "sha": sha(text)}
        _write_state(state)
    return get(name)


def delete(name: str) -> None:
    _require(name)
    with _lock:
        shutil.rmtree(_folder(name), ignore_errors=True)
        state = _read_state()
        state.pop(name, None)
        _write_state(state)
