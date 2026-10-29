import threading
from typing import Callable

# Un bus mínimo de eventos del Voice Companion. Lo que pasa en una charla hablada (avisos de estado, lo que va
# escribiendo el modelo, las herramientas que usa, la respuesta) se publica acá y cada interesado se suscribe: hoy el
# aviso de escritorio; mañana, por ejemplo, una ventanita que muestre el texto en vivo (ver la spec). Quien publica no
# sabe quién escucha, y un suscriptor que falla no frena a los demás ni a quien publica. Cada suscriptor recibe una
# COPIA del evento, así uno no puede alterar lo que ve el siguiente.


class Bus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[dict], None]] = []

    def subscribe(self, subscriber: Callable[[dict], None]) -> Callable[[], None]:
        """Empieza a recibir los eventos. Devuelve la función que deja de recibirlos (se puede llamar dos veces)."""
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def emit(self, event: dict) -> None:
        with self._lock:
            listening = list(self._subscribers)  # una copia: uno puede darse de baja mientras se reparte
        for subscriber in listening:
            try:
                subscriber(dict(event))
            except Exception:  # noqa: BLE001 - un suscriptor roto no puede frenar a los otros
                pass
