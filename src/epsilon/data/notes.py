import csv
from datetime import date
import os
from pathlib import Path
import re

# DATA_DIR es la carpeta donde viven las notas, separada del código.
# os.environ.get busca la variable DATA_DIR; si no existe, usa "~/bot-datos".
# .expanduser() convierte "~" en tu carpeta personal (/home/tu_usuario).
# .resolve() la convierte en una ruta absoluta y limpia.
DATA_DIR = Path(os.environ.get("DATA_DIR", "~/epsilon-data")).expanduser().resolve()

# Creamos la carpeta si todavía no existe (exist_ok evita error si ya está).
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Subcarpetas dentro de DATA_DIR. Se definen una sola vez acá.
FILES_DIR = "files"  # archivos recibidos (PDFs, imágenes)
SUMMARIES_DIR = "summarizes"  # resúmenes generados

# Tipos de archivo que sabemos resumir (los mismos que soporta summarize.py).
summarizable_ext = {".md", ".txt", ".pdf", ".jpg", ".jpeg", ".png", ".webp"}


def safe_path(name: str) -> Path:
    """Devuelve la ruta completa de un archivo, pero solo si queda DENTRO de DATA_DIR."""
    # Unimos la carpeta base con el nombre pedido y normalizamos el resultado.
    # .resolve() elimina cosas como ".." (subir de carpeta) y sigue los symlinks.
    destiny = (DATA_DIR / name).resolve()

    # Si el destino quedó fuera de DATA_DIR (por ejemplo "../../etc/passwd"),
    # cortamos con un error en vez de tocar ese archivo.
    if not destiny.is_relative_to(DATA_DIR):
        raise ValueError("Ruta fuera de la carpeta permitida")

    return destiny


def save_note(name: str, texto: str) -> str:
    """Agrega texto al final de una nota (la crea si no existe). Nunca pisa lo anterior."""
    path = safe_path(f"{name}.md")

    # Si el nombre incluye subcarpetas (ej: "casa/compras"), las creamos.
    path.parent.mkdir(parents=True, exist_ok=True)

    # Modo "a" = append: escribe al final sin borrar lo que ya había.
    # encoding="utf-8" evita problemas con tildes y la ñ.
    with path.open("a", encoding="utf-8") as f:
        f.write(texto + "\n")

    return f"Guardado en {path.name}"


def edit_line(name: str, n: int, new: str) -> str:
    """Cambia la línea `n` (desde 1) de una nota que ya existe por `new`; con `new` vacío la borra. Es la única forma
    de pisar una nota: nunca la reescribe entera ni la crea. Deja la versión anterior en «<nota>.md.bak» (una sola,
    la de antes de la última edición) y escribe por un archivo temporal."""
    path = safe_path(f"{name}.md")
    if not path.exists():
        raise ValueError(f"No existe la nota '{name}'.")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not 1 <= n <= len(lines):
        raise ValueError(f"La nota '{name}' tiene {len(lines)} líneas: no existe la {n}.")
    new = new.strip("\n")
    lines[n - 1 : n] = [new + "\n"] if new else []
    path.with_name(path.name + ".bak").write_text(text, encoding="utf-8")
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("".join(lines), encoding="utf-8")
    os.replace(temp, path)  # la nota nunca queda a medias
    return f"Editado {path.name}"


def clean_note_name(name: str) -> str:
    """Nombre de nota seguro a partir de un texto libre; vacío si no queda nada utilizable."""
    # Mismas reglas que clean_folder: sin «/» ni «..», así la nota queda siempre en DATA_DIR.
    return re.sub(r"[^\w\-]", "_", name.strip().lower())[:60].strip("_")


def save_csv(name: str, fields: list[str], rows: list[dict]) -> str:
    """Agrega filas a un CSV (lo crea con encabezado si no existe)."""
    path = safe_path(f"{name}.csv")
    path.parent.mkdir(parents=True, exist_ok=True)

    # Si el archivo no existe todavía, habrá que escribir el encabezado.
    new = not path.exists()

    # Si ya existe, comprobamos que tenga las MISMAS columnas.
    # Si no, agregar filas las dejaría corridas y el archivo quedaría roto.
    if not new:
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f), [])
        if header != fields:
            raise ValueError(f"{path.name} tiene otras columnas: {header}")

    # newline="" es lo que pide el módulo csv para no duplicar saltos de línea.
    with path.open("a", encoding="utf-8", newline="") as f:
        # DictWriter escribe cada dict ubicando cada valor en su columna.
        writer = csv.DictWriter(f, fieldnames=fields)
        if new:  # el encabezado solo se escribe cuando el CSV se crea
            writer.writeheader()
        writer.writerows(rows)

    return f"{len(rows)} fila(s) guardadas en {path.name}"


def clean_folder(folder: str | None) -> str:
    """Nombre de carpeta seguro a partir de un texto libre; vacío o reservado = FILES_DIR."""
    # Solo letras, números, "_" y "-": nada de "/" ni ".." que armen rutas.
    cleaned = re.sub(r"[^\w\-]", "_", (folder or "").strip().lower())[:40].strip("_")
    # No dejamos usar la carpeta de resúmenes: _match_files la ignora.
    if not cleaned or cleaned == SUMMARIES_DIR:
        return FILES_DIR
    return cleaned


def save_file(original_name: str, data: bytes, folder: str | None = None) -> Path:
    """Guarda un archivo recibido en su carpeta (por defecto files/), sin pisar nunca uno existente."""
    folder = clean_folder(folder)
    # Path(...).name se queda solo con el nombre y descarta carpetas:
    # "../../x.pdf" pasa a ser "x.pdf".
    base = Path(original_name).name
    # Reemplazamos por "_" cualquier carácter raro (espacios, símbolos, etc.).
    base = re.sub(r"[^\w.\-]", "_", base)

    # Anteponemos la fecha para tener los archivos ordenados.
    name = f"{date.today().isoformat()}_{base}"
    destiny = safe_path(f"{folder}/{name}")
    destiny.parent.mkdir(parents=True, exist_ok=True)

    # Si ya existe uno con ese nombre, probamos con _2, _3...
    # Así nunca se sobreescribe nada.
    n = 2
    while destiny.exists():
        p = Path(name)
        destiny = safe_path(f"{folder}/{p.stem}_{n}{p.suffix}")
        n += 1

    destiny.write_bytes(data)
    return destiny


def read_note(name: str) -> str:
    """Devuelve el contenido completo de una nota."""
    path = safe_path(f"{name}.md")

    # Si no existe, avisamos en vez de dejar que el programa se rompa.
    if not path.exists():
        return f"No existe la nota '{name}'"

    return path.read_text(encoding="utf-8")


def search_note(texto: str) -> list[str]:
    """Busca un texto en todas las notas y devuelve las líneas donde aparece."""
    results = []

    # glob("**/*.md") recorre todos los .md, incluso los de subcarpetas.
    for archive in DATA_DIR.glob("**/*.md"):
        lines = archive.read_text(encoding="utf-8").splitlines()

        # enumerate nos da el número de línea (start=1 para contar desde 1).
        for num, line in enumerate(lines, start=1):
            # Comparamos en minúsculas para que no importe mayúsculas/minúsculas.
            if texto.lower() in line.lower():
                results.append(f"{archive.stem}:{num}: {line}")

    # Si la lista quedó vacía, devolvemos un mensaje en lugar de nada.
    return results or ["Sin resultados"]


def _match_files(text: str, extensions: set[str]) -> list[Path]:
    """Archivos de DATA_DIR con esas extensiones cuyo nombre coincide. Más nuevos primero."""
    wanted = text.lower()
    candidates = []

    # rglob("*") recorre DATA_DIR y todas sus subcarpetas.
    for p in DATA_DIR.rglob("*"):
        # Solo archivos con alguna de las extensiones pedidas.
        if not p.is_file() or p.suffix.lower() not in extensions:
            continue

        # Seguridad: si un symlink apunta a un archivo FUERA de DATA_DIR,
        # lo descartamos (así nunca mandamos al modelo algo ajeno a tus datos).
        if not p.resolve().is_relative_to(DATA_DIR):
            continue

        # No resumimos los resúmenes (sería gastar tokens para nada).
        if p.relative_to(DATA_DIR).parts[0] == SUMMARIES_DIR:
            continue

        candidates.append(p)

    # Si alguno coincide EXACTO (con o sin extensión), solo devolvemos esos.
    # Así "inbox" encuentra inbox.md aunque exista "inbox_viejo.md".
    exacts = [p for p in candidates if wanted in (p.name.lower(), p.stem.lower())]
    # Si no hay exactos, aceptamos coincidencias parciales en el nombre.
    result = exacts or [p for p in candidates if wanted in p.name.lower()]

    # Ordenamos por fecha de modificación: lo más nuevo primero.
    return sorted(result, key=lambda p: p.stat().st_mtime, reverse=True)


def find_files(text: str) -> list[Path]:
    """Busca notas y archivos que se puedan resumir cuyo nombre coincida con el texto."""
    return _match_files(text, summarizable_ext)


def find_csv(text: str) -> list[Path]:
    """Busca CSV guardados cuyo nombre coincida con el texto."""
    return _match_files(text, {".csv"})


def list_files(text: str = "", limit: int = 10) -> tuple[list[Path], int]:
    """Lista lo guardado (notas, archivos y CSV), lo más nuevo primero.

    Devuelve (los primeros `limit`, cantidad total). Texto vacío = todo."""
    result = _match_files(text, summarizable_ext | {".csv"})
    return result[:limit], len(result)


# Este bloque solo corre si ejecutás "python notas.py" directamente.
# Sirve para probar las funciones sin necesitar nada más.
if __name__ == "__main__":
    print(save_note("prueba", "comprar filamento PETG negro"))
    print(save_note("prueba", "revisar la boquilla de la A1"))
    print(read_note("prueba"))
    print(search_note("filamento"))
    # Esto debería fallar con "Ruta fuera de la carpeta permitida":
    try:
        read_note("../../etc/passwd")
    except ValueError as e:
        print("Bloqueado:", e)
