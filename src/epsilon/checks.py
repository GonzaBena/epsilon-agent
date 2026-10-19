from epsilon import config, llm
from epsilon.channels import telegram

# Pruebas de conexión que se disparan desde la configuración. Ninguna gasta
# tokens: Gemini valida con los metadatos del modelo y Telegram con getMe.
# Cada una devuelve un texto o lanza ValueError con un mensaje claro.
CHECKS = {
    "gemini": lambda: f"Clave válida; el modelo {llm.check()} está disponible.",
    # Prueba el token GUARDADO (aunque todavía no esté aplicado), así sirve
    # para comprobar lo que se acaba de cargar antes de reiniciar.
    "telegram": lambda: telegram.check(config.saved("TELEGRAM_TOKEN")),
}


def run(target: str) -> dict:
    """Corre una prueba y devuelve {"ok": bool, "detail": texto}. Nunca lanza."""
    try:
        return {"ok": True, "detail": config.redact(CHECKS[target]())}
    except ValueError as e:
        # Errores esperables: el mensaje ya está pensado para el usuario.
        return {"ok": False, "detail": config.redact(str(e))}
    except Exception as e:
        # Inesperado: a la consola solo el tipo (el texto podría traer datos
        # sensibles) y al usuario un resumen.
        print(f"Error inesperado en la prueba de {target}: {type(e).__name__}")
        return {"ok": False, "detail": "No pude hacer la prueba (error inesperado)."}
