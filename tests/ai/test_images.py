import io

from PIL import Image

from epsilon.ai import images
from epsilon.ai.messages import File

# Achicar imágenes antes de mandarlas al modelo: sin red ni modelo, todo en memoria.


def picture(width, height, fmt="JPEG", mode="RGB", orientation=None) -> File:
    image = Image.new(mode, (width, height), "white")
    out = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[images.ORIENTATION] = orientation
        image.save(out, fmt, exif=exif)
    else:
        image.save(out, fmt)
    return File(out.getvalue(), f"image/{fmt.lower()}")


def size(file: File) -> tuple[int, int]:
    return Image.open(io.BytesIO(file.data)).size


def test_a_big_photo_is_shrunk_keeping_its_proportion():
    shrunk = images.shrink(picture(4000, 3000), 1024)

    assert size(shrunk) == (1024, 768) and shrunk.mime == "image/jpeg"


def test_a_small_image_goes_untouched():
    small = picture(800, 600)

    assert images.shrink(small, 1024) is small


def test_a_sideways_phone_photo_is_turned_upright_even_if_it_fits():
    # Orientación 6: la cámara la guardó acostada; quien la mira la ve parada.
    shrunk = images.shrink(picture(800, 600, orientation=6), 1024)

    assert size(shrunk) == (600, 800)


def test_transparency_stays_png():
    shrunk = images.shrink(picture(3000, 3000, "PNG", "RGBA"), 1024)

    assert shrunk.mime == "image/png" and Image.open(io.BytesIO(shrunk.data)).mode == "RGBA"


def test_what_is_not_an_image_or_cannot_be_opened_goes_as_is():
    pdf = File(b"%PDF-1.7 ...", "application/pdf")
    broken = File(b"esto no es una imagen", "image/jpeg")

    assert images.shrink(pdf, 1024) is pdf and images.shrink(broken, 1024) is broken


def test_an_animation_keeps_its_frames():
    frames = [Image.new("RGB", (3000, 3000), color) for color in ("red", "blue")]
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:])
    gif = File(out.getvalue(), "image/gif")

    assert images.shrink(gif, 1024) is gif


def test_an_iphone_photo_with_a_depth_map_is_still_shrunk():
    # MPO: la foto y su mapa de profundidad en un archivo; Pillow lo ve como «varios cuadros», pero no es animación.
    frames = [Image.new("RGB", (4032, 3024), "white"), Image.new("RGB", (4032, 3024), "black")]
    out = io.BytesIO()
    frames[0].save(out, "MPO", save_all=True, append_images=frames[1:])
    photo = File(out.getvalue(), "image/jpeg")

    assert size(images.shrink(photo, 1024)) == (1024, 768)


def test_image_to_csv_gets_more_detail_than_the_chat():
    assert images.max_side("csv") > images.max_side("chat") == images.max_side(None)


# --- Una imagen puede pedir un lado mínimo (una captura de pantalla: si no, el texto no se lee) ----------------------------


def test_an_image_can_ask_to_keep_more_detail_than_the_default_limit():
    screen = picture(2560, 1440)
    screen.side = 1600

    shrunk = images.shrink(screen, 1024)  # el tope de la tarea es 1024, pero ella pidió 1600

    assert size(shrunk) == (1600, 900)
    assert shrunk.side == 1600  # y se acuerda: la próxima vuelta del modelo no la achica de nuevo


def test_an_image_that_asks_for_less_than_the_limit_gets_the_limit():
    small = picture(4000, 3000)
    small.side = 300

    assert size(images.shrink(small, 1024)) == (1024, 768)


def test_without_a_request_nothing_changes():
    assert size(images.shrink(picture(4000, 3000), 1024)) == (1024, 768)
    assert File(b"x", "image/png").side == 0
