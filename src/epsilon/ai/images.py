import io

from PIL import Image, ImageOps

from epsilon.ai.messages import File
from epsilon.settings import config

# Achicar las imágenes ANTES de mandarlas a un modelo, sea el proveedor que sea (lo llama la fachada `llm`). Una
# foto de celular (4000 px, 4 MB) no le dice nada más a un modelo que una de 1024 px: los que cobran por píxel
# gastan menos, uno local la procesa más rápido y la petición pesa mucho menos. Si algo falla, va la original.

# El lado más largo (IMAGES_MAX_SIDE; IMAGES_MAX_SIDE_CSV para Imagen a CSV, que lee letra chica de tickets y
# planillas y necesita más detalle) y la calidad del JPEG (IMAGES_JPEG_QUALITY) son ajustes: se leen al usarlos.
ORIENTATION = 0x0112  # la etiqueta EXIF de la orientación (1 = derecha)
ANIMATED = {"GIF", "PNG", "WEBP"}  # formatos cuyos varios cuadros son una animación


def max_side(task: str | None) -> int:
    """El tope de la tarea en curso (ver llm.task)."""
    return config.number("IMAGES_MAX_SIDE_CSV" if task == "csv" else "IMAGES_MAX_SIDE")


def shrink(file: File, limit: int) -> File:
    """La imagen con su lado más largo en `limit` como mucho. Lo que no es una imagen (un PDF), lo que ya entra,
    una animación y lo que Pillow no sabe abrir vuelven tal cual."""
    if not file.mime.startswith("image/"):
        return file
    limit = max(limit, file.side)  # una captura de pantalla pide más lado que una foto
    try:
        with Image.open(io.BytesIO(file.data)) as image:  # abrir solo lee el encabezado: saber el tamaño es barato
            # Un GIF (o PNG, WebP) animado perdería los cuadros. Ojo: las fotos de iPhone (MPO) también dicen tener
            # varios cuadros (la foto y su mapa de profundidad), pero el primero es la foto: esas sí se achican.
            if getattr(image, "is_animated", False) and image.format in ANIMATED:
                return file
            # Las fotos del celular suelen venir «acostadas» con un dato EXIF (orientación) que dice cómo girarlas.
            orientation = image.getexif().get(ORIENTATION, 1)
            if max(image.size) <= limit and orientation == 1:
                return file  # ya entra y no hay que girarla: la original, sin volver a comprimir
            upright = ImageOps.exif_transpose(image)  # se gira acá, porque al guardar de nuevo ese dato se pierde
            upright.thumbnail((limit, limit))  # achica sin deformar (mantiene la proporción)
            out = io.BytesIO()
            if upright.mode in ("RGBA", "LA", "P"):  # con transparencia o paleta: PNG, que no la pierde
                upright.save(out, "PNG", optimize=True)
                return File(out.getvalue(), "image/png", file.side)
            upright.convert("RGB").save(out, "JPEG", quality=config.number("IMAGES_JPEG_QUALITY"))
            return File(out.getvalue(), "image/jpeg", file.side)
    except Exception:  # noqa: BLE001 - un formato raro (HEIC) o un archivo roto: que el modelo reciba la original
        return file
