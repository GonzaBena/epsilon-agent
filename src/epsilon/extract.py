import json
import mimetypes
import sys

from dotenv import load_dotenv
from google.genai import types

from epsilon import llm, notes, usage


def img_to_rows(img: bytes, mime: str, fields: list[str]) -> list[dict]:
    """Analiza la imagen y devuelve una lista de filas (dicts) con SOLO esos campos."""
    # El prompt es corto a propósito: menos tokens de entrada = menos costo.
    # Le pedimos que NO invente: si un dato no está, que deje el campo vacío.
    prompt = (
        "Extraé los datos de la imagen. Devolvé SOLO un JSON: una lista de "
        f"objetos con exactamente estas claves: {', '.join(fields)}. "
        "Un objeto por ítem o registro que aparezca. Si un dato no está "
        "en la imagen, dejá el valor como texto vacío; no inventes nada."
    )

    with usage.action("Imagen a CSV"):  # para la página Uso
        answer = llm.generate(
            # Part.from_bytes empaqueta la imagen para enviarla junto al texto.
            [types.Part.from_bytes(data=img, mime_type=mime)],
            prompt,
            max_tokens=4000,  # tope de salida, para que nada se dispare
            json_mode=True,  # obliga a responder JSON
        )

    data = json.loads(answer)

    # Si el modelo devolvió un solo objeto en vez de una lista, lo envolvemos.
    if isinstance(data, dict):
        data = [data]

    # Nos quedamos SOLO con las columnas pedidas. Si el modelo agregó claves
    # de más (o texto inesperado), no llegan al CSV.
    return [{c: str(row.get(c, "")) for c in fields} for row in data]


# Prueba desde la terminal:
#   uv run extraer.py ticket.jpg "fecha,proveedor,total" ticket-prueba
if __name__ == "__main__":
    load_dotenv()

    if len(sys.argv) != 4:
        raise SystemExit('Uso: uv run extraer.py <imagen> "campo1,campo2" <nombre_csv>')

    img_path, txt_fields, csv_name = sys.argv[1:]
    fields = [c.strip() for c in txt_fields.split(",")]

    mime = mimetypes.guess_type(img_path)[0] or "image/jpeg"
    with open(img_path, "rb") as f:  # "rb" = leer bytes crudos
        imagen = f.read()

    rows = img_to_rows(imagen, mime, fields)
    print(rows)  # mirá esto ANTES de confiar en el CSV
    print(notes.save_csv(csv_name, fields, rows))
