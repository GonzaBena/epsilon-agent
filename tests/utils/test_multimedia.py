import json

import pytest

from epsilon.utils import multimedia

CHROMIUM = "org.mpris.MediaPlayer2.chromium.instance42"
SPOTIFY = "org.mpris.MediaPlayer2.spotify"


def fake_system(monkeypatch, players=None, volume="Volume: 0.30\n"):
    """Reemplaza `_run`: anota cada orden y contesta como wpctl y busctl. `players`: nombre -> (estado, artistas, título)."""
    players = players or {}
    seen = []

    def run(argv):
        seen.append(argv)
        if argv[0] == "wpctl":
            return volume if argv[1] == "get-volume" else ""
        if argv[:3] == ["busctl", "--user", "--no-legend"]:
            rows = [":1.73  2984 oampris  atlas :1.73  user@1000.service - -"] + [f"{name}  1 app atlas :1.9 s - -" for name in players]
            return "\n".join(rows) + "\n"
        if "get-property" in argv:
            status, artists, title = players[argv[4]]
            data = {"s": status} if argv[-1] == "PlaybackStatus" else {
                "xesam:artist": {"type": "as", "data": artists}, "xesam:title": {"type": "s", "data": title}}
            return json.dumps({"data": data["s"] if argv[-1] == "PlaybackStatus" else data})
        return ""

    monkeypatch.setattr(multimedia, "_run", run)
    return seen


# --- volumen ---------------------------------------------------------------------


@pytest.mark.parametrize("text, tail", [
    ("40", ["set-volume", "-l", "1.00", multimedia.SINK, "40%"]),
    ("40 %", ["set-volume", "-l", "1.00", multimedia.SINK, "40%"]),
    ("+5", ["set-volume", "-l", "1.00", multimedia.SINK, "5%+"]),
    ("-10", ["set-volume", "-l", "1.00", multimedia.SINK, "10%-"]),
    ("silenciar", ["set-mute", multimedia.SINK, "1"]),
    ("Activar", ["set-mute", multimedia.SINK, "0"]),
])
def test_volume_text_becomes_fixed_wpctl_arguments(text, tail):
    assert multimedia.parse_volume(text) == tail


def test_volume_is_capped_so_nobody_can_blast_the_speakers():
    assert multimedia.parse_volume("500")[-1] == "100%"
    assert multimedia.parse_volume("+999")[-1] == "50%+"  # tope por paso; el -l 1.00 frena el total


@pytest.mark.parametrize("text", ["", "alto", "40; rm -rf /", "--help", "4.5", "1000", "$(id)"])
def test_volume_refuses_anything_that_is_not_a_number_or_a_known_word(text):
    with pytest.raises(ValueError):
        multimedia.parse_volume(text)


def test_volume_without_argument_only_reads(monkeypatch):
    seen = fake_system(monkeypatch)

    assert multimedia.volume("") == "Volumen: 30 %"
    assert seen == [["wpctl", "get-volume", multimedia.SINK]]


def test_volume_changes_and_reports_how_it_ended(monkeypatch):
    seen = fake_system(monkeypatch, volume="Volume: 0.45 [MUTED]\n")

    assert multimedia.volume("45") == "Volumen: 45 % (silenciado)"
    assert seen[0][:2] == ["wpctl", "set-volume"]


def test_a_missing_program_is_a_readable_error(monkeypatch):
    monkeypatch.undo()  # vuelve el `_run` real; wpctl "no existe" con un PATH vacío
    monkeypatch.setenv("PATH", "")

    with pytest.raises(multimedia.MultimediaError, match="Falta el programa"):
        multimedia._run(["wpctl", "get-volume", multimedia.SINK])


# --- qué suena y controles ---------------------------------------------------------


def test_now_playing_prefers_the_player_that_is_playing(monkeypatch):
    fake_system(monkeypatch, {CHROMIUM: ("Paused", ["A"], "Uno"), SPOTIFY: ("Playing", ["Sant Nieto", "Otro"], "Disco  Set")})

    assert multimedia.now_playing() == "Sonando: Sant Nieto, Otro — Disco Set (spotify)"


def test_now_playing_says_when_paused_and_when_there_is_no_artist(monkeypatch):
    fake_system(monkeypatch, {CHROMIUM: ("Paused", [], "Un video")})

    assert multimedia.now_playing() == "En pausa: Un video (chromium)"


def test_now_playing_cuts_long_titles(monkeypatch):
    fake_system(monkeypatch, {CHROMIUM: ("Playing", [], "x" * 500)})

    assert len(multimedia.now_playing()) < 160


def test_no_player_is_a_readable_error(monkeypatch):
    fake_system(monkeypatch)

    with pytest.raises(multimedia.MultimediaError, match="ningún reproductor"):
        multimedia.now_playing()


@pytest.mark.parametrize("text, method", [("", "PlayPause"), ("pausa", "Pause"), ("play", "Play"), ("Siguiente", "Next"), ("anterior", "Previous")])
def test_music_calls_a_fixed_mpris_method_on_the_active_player(monkeypatch, text, method):
    seen = fake_system(monkeypatch, {CHROMIUM: ("Playing", [], "t")})

    assert "(chromium)" in multimedia.music(text)
    assert seen[-1] == ["busctl", "--user", "call", CHROMIUM, multimedia.MPRIS_PATH, multimedia.PLAYER_IFACE, method]


def test_music_refuses_other_options_without_running_anything(monkeypatch):
    seen = fake_system(monkeypatch, {CHROMIUM: ("Playing", [], "t")})

    with pytest.raises(ValueError):
        multimedia.music("Quit")
    assert seen == []


# --- repetir y aleatorio -----------------------------------------------------------


def fake_modes(monkeypatch, loop="None", shuffle=False, supported=True):
    """Como `fake_system`, pero también contesta LoopStatus y Shuffle y anota los set-property."""
    base = fake_system(monkeypatch, {SPOTIFY: ("Playing", [], "t")})
    original = multimedia._run

    def run(argv):
        if "get-property" in argv and argv[-1] in ("LoopStatus", "Shuffle"):
            if not supported:
                raise multimedia.MultimediaError("El sistema de audio no respondió.")
            return json.dumps({"data": loop if argv[-1] == "LoopStatus" else shuffle})
        return original(argv)

    monkeypatch.setattr(multimedia, "_run", run)
    return base


@pytest.mark.parametrize("text, loop, value, said", [
    ("repetir", "None", "Track", "Repitiendo la canción"),
    ("repetir", "Track", "None", "Repetir apagado"),  # sin opción alterna
    ("repetir canción", "None", "Track", "Repitiendo la canción"),
    ("Repetir  lista", "None", "Playlist", "Repitiendo la lista"),
    ("repetir no", "Track", "None", "Repetir apagado"),
])
def test_repeat_sets_loop_status_with_fixed_values(monkeypatch, text, loop, value, said):
    seen = fake_modes(monkeypatch, loop=loop)

    assert multimedia.music(text).startswith(said)
    assert seen[-1] == ["busctl", "--user", "set-property", SPOTIFY, multimedia.MPRIS_PATH, multimedia.PLAYER_IFACE, "LoopStatus", "s", value]


@pytest.mark.parametrize("text, now, value", [("aleatorio", False, "true"), ("aleatorio", True, "false"), ("shuffle sí", False, "true"), ("aleatorio no", True, "false")])
def test_shuffle_sets_the_boolean(monkeypatch, text, now, value):
    seen = fake_modes(monkeypatch, shuffle=now)

    multimedia.music(text)
    assert seen[-1][-3:] == ["Shuffle", "b", value]


def test_modes_say_so_when_the_player_does_not_support_them(monkeypatch):
    seen = fake_modes(monkeypatch, supported=False)

    with pytest.raises(multimedia.MultimediaError, match="no permite cambiar repetir"):
        multimedia.music("repetir")
    assert not any("set-property" in argv for argv in seen)


def test_modes_reject_made_up_options_and_are_valid_for_routines():
    assert multimedia.is_music_action("repetir lista") and multimedia.is_music_action("aleatorio")
    assert not multimedia.is_music_action("repetir x; reboot")
    with pytest.raises(ValueError):
        multimedia.music("aleatorio quizás")
