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
        if path:
            writer.writeheader()
        writer.writerows(rows)

    return f"{len(rows)} fila(s) guardadas en {path.name}"


def save_file(original_name: str, data: bytes) -> Path:
    """Guarda un archivo recibido en archivos/, sin pisar nunca uno existente."""
    # Path(...).name se queda solo con el nombre y descarta carpetas:
    # "../../x.pdf" pasa a ser "x.pdf".
    base = Path(original_name).name
    # Reemplazamos por "_" cualquier carácter raro (espacios, símbolos, etc.).
    base = re.sub(r"[^\w.\-]", "_", base)

    # Anteponemos la fecha para tener los archivos ordenados.
    name = f"{date.today().isoformat()}_{base}"
    destiny = safe_path(f"archivos/{name}")
    destiny.parent.mkdir(parents=True, exist_ok=True)

    # Si ya existe uno con ese nombre, probamos con _2, _3...
    # Así nunca se sobreescribe nada.
    n = 2
    while destiny.exists():
        p = Path(name)
        destiny = safe_path(f"archivos/{p.stem}_{n}{p.suffix}")
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
