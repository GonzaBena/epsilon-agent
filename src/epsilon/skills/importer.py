from dataclasses import dataclass, field
import io
import ipaddress
import secrets
import socket
import time
from urllib.parse import urljoin, urlsplit
import zipfile
from pathlib import PurePosixPath

import httpx

from epsilon.skills import store

# Traer una skill de afuera (archivo, .zip o URL) SIN guardar nada: se arma un `Staged` que la web muestra a la
# persona y solo `commit` (tras su confirmación) lo escribe. De un .zip salen solo textos y datos: los scripts y
# todo lo que intente salirse de la carpeta se descarta y se avisa. Los ÚNICOS puntos de red son `_fetch` (un
# pedido, sin seguir redirecciones) y `_resolve` (DNS): los tests los bloquean.

MAX_ZIP_BYTES = 1_000_000
MAX_FILES = 20
MAX_FILE_BYTES = 100_000
MAX_URL_BYTES = 200_000
MAX_REDIRECTS = 3
TEXT_EXT = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".toml"}
TTL = 900  # segundos que espera una importación hasta que la persona la confirme
MAX_STAGED = 10
TIMEOUT = 10.0


@dataclass
class Staged:
    name: str
    description: str
    text: str
    extra: dict[str, bytes] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    source: str = ""


_staged: dict[str, tuple[float, Staged]] = {}


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise store.SkillError("El archivo no es texto (UTF-8).") from None


def from_text(text: str, source: str = "texto") -> Staged:
    name, description, _ = store.parse(text)
    return Staged(name, description, text, source=source)


def _zip_skill_md(names: list[str]) -> str:
    """La entrada SKILL.md: en la raíz o dentro de UNA carpeta."""
    for name in names:
        parts = PurePosixPath(name).parts
        if parts and parts[-1] == "SKILL.md" and len(parts) <= 2:
            return name
    raise store.SkillError("El .zip no tiene un SKILL.md (en la raíz o dentro de una sola carpeta).")


def _read_entry(z: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Lee una entrada contando los bytes de verdad (el tamaño declarado puede mentir)."""
    with z.open(info) as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise store.SkillError(f"«{info.filename[:60]}» es muy grande (máximo {MAX_FILE_BYTES // 1000} KB por archivo).")
    return data


def _from_zip(data: bytes, source: str) -> Staged:
    if len(data) > MAX_ZIP_BYTES:
        raise store.SkillError(f"El .zip es muy grande (máximo {MAX_ZIP_BYTES // 1_000_000} MB).")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise store.SkillError("No pude abrir el .zip.") from None
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise store.SkillError(f"El .zip tiene demasiados archivos (máximo {MAX_FILES}).")
        main = _zip_skill_md([i.filename for i in infos])
        root = PurePosixPath(main).parent
        item = from_text(_decode(_read_entry(z, next(i for i in infos if i.filename == main))), source)
        for info in infos:
            if info.filename == main:
                continue
            raw = info.filename
            path = PurePosixPath(raw)
            if (info.external_attr >> 28) == 0xA:  # enlace simbólico
                item.skipped.append(f"{raw[:60]} (enlace simbólico)")
            elif "\\" in raw or path.is_absolute() or ".." in path.parts:
                item.skipped.append(f"{raw[:60]} (ruta no permitida)")
            elif path.suffix.lower() not in TEXT_EXT:
                item.skipped.append(f"{raw[:60]} (no es texto: nunca se ejecuta)")
            elif root != PurePosixPath(".") and root not in path.parents:
                item.skipped.append(f"{raw[:60]} (fuera de la carpeta de la skill)")
            else:
                rel = path.relative_to(root) if root != PurePosixPath(".") else path
                item.extra[rel.as_posix()] = _read_entry(z, info)
    return item


def from_file(filename: str, data: bytes) -> Staged:
    lower = filename.lower()
    if lower.endswith(".zip"):
        return _from_zip(data, filename[:80])
    if lower.endswith((".md", ".txt")):
        return from_text(_decode(data), filename[:80])
    raise store.SkillError("Subí un SKILL.md (o un .md) o un .zip.")


# --- Red ---------------------------------------------------------------------------------------


def _fetch(url: str) -> tuple[int, str, bytes]:
    """UN pedido GET, sin seguir redirecciones: (código, Location, cuerpo cortado a MAX_URL_BYTES + 1)."""
    try:
        with httpx.stream("GET", url, timeout=TIMEOUT, follow_redirects=False) as response:
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_URL_BYTES:
                    break
            return response.status_code, response.headers.get("location", ""), body
    except httpx.HTTPError:
        raise store.SkillError("No pude bajar esa dirección.") from None


def _resolve(host: str) -> list[str]:
    """Las IP del sitio; vacío si no se encuentra."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise store.SkillError("La dirección tiene que empezar con http:// o https://.")
    addresses = _resolve(parts.hostname)
    if not addresses:
        raise store.SkillError("No encontré ese sitio.")
    for text in addresses:
        try:
            ip = ipaddress.ip_address(text.split("%")[0])
        except ValueError:
            raise store.SkillError("No pude verificar la dirección de ese sitio.") from None
        if not ip.is_global:
            raise store.SkillError("Esa dirección es de tu red local: no la bajo.")


def _get(url: str) -> bytes:
    """Baja la URL revalidando cada destino (también tras una redirección). Levanta SkillError."""
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(url)
        status, location, body = _fetch(url)
        if status in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        if status != 200:
            raise store.SkillError(f"El sitio contestó {status}.")
        if len(body) > MAX_URL_BYTES:
            raise store.SkillError("El archivo es muy grande.")
        return body
    raise store.SkillError("Demasiadas redirecciones.")


def from_url(url: str) -> Staged:
    url = url.strip()
    return from_text(_decode(_get(url)), url[:200])


# --- Pendientes de confirmar ---------------------------------------------------------------------


def _expire() -> None:
    now = time.monotonic()
    for token in [t for t, (born, _) in _staged.items() if now - born > TTL]:
        del _staged[token]


def stage(item: Staged) -> str:
    _expire()
    while len(_staged) >= MAX_STAGED:
        del _staged[next(iter(_staged))]
    token = secrets.token_urlsafe(12)
    _staged[token] = (time.monotonic(), item)
    return token


def peek(token: str) -> Staged | None:
    _expire()
    found = _staged.get(token)
    return found[1] if found else None


def take(token: str) -> Staged | None:
    _expire()
    found = _staged.pop(token, None)
    return found[1] if found else None


def commit(item: Staged, name: str | None = None) -> store.Skill:
    """Escribe lo que la persona confirmó. `name` cambia el nombre (la carpeta y el comando)."""
    return store.save(name or item.name, item.text, item.extra)
