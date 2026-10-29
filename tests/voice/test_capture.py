import signal
import wave

import pytest

from epsilon.voice import capture


class FakeProcess:
    """Hace de pw-record: al recibir SIGINT cierra el WAV con la duración que se le pidió."""

    def __init__(self, argv, seconds=2.0, write=True):
        self.argv = argv
        self.path = argv[-1]
        self.seconds = seconds
        self.write = write
        self.signals = []
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)
        if self.write:
            with wave.open(self.path, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(capture.SAMPLE_RATE)
                out.writeframes(b"\x01\x00" * int(capture.SAMPLE_RATE * self.seconds))
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.fixture(autouse=True)
def no_startup_wait(monkeypatch):
    monkeypatch.setattr(capture, "STARTUP_CHECK", 0)


@pytest.fixture
def spawned(monkeypatch):
    """Reemplaza el único punto que lanza pw-record y guarda lo que se lanzó."""
    made = []

    def fake(argv, env):
        process = FakeProcess(argv)
        made.append(process)
        return process

    monkeypatch.setattr(capture, "_spawn", fake)
    return made


def test_start_records_16khz_mono_with_the_default_microphone(spawned, tmp_path):
    capture.start(tmp_path)

    argv = spawned[0].argv
    assert argv[0] == "pw-record"
    assert argv[argv.index("--rate") + 1] == "16000"
    assert argv[argv.index("--channels") + 1] == "1"
    assert "--target" not in argv  # sin elegir micrófono: PipeWire usa el predeterminado


def test_the_recording_goes_to_a_private_folder(spawned, tmp_path):
    capture.start(tmp_path / "voice")

    assert (tmp_path / "voice").stat().st_mode & 0o777 == 0o700


def test_stop_ends_the_recording_with_sigint_and_reports_its_length(spawned, tmp_path):
    recording = capture.start(tmp_path)

    result = capture.stop(recording)

    assert spawned[0].signals == [signal.SIGINT]  # SIGINT: pw-record cierra bien el encabezado del WAV
    assert result.path == recording.path
    assert result.path.exists()
    assert result.seconds == pytest.approx(2.0)


def test_a_recorder_that_does_not_end_is_killed(spawned, tmp_path):
    recording = capture.start(tmp_path)

    def hang(timeout=None):
        raise capture.subprocess.TimeoutExpired("pw-record", timeout)

    spawned[0].wait = hang
    capture.stop(recording)

    assert spawned[0].killed


def test_nothing_recorded_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(capture, "_spawn", lambda argv, env: FakeProcess(argv, write=False))
    recording = capture.start(tmp_path)

    with pytest.raises(capture.CaptureError, match="micrófono"):
        capture.stop(recording)


def test_a_missing_pw_record_says_what_to_install(monkeypatch, tmp_path):
    def missing(argv, env):
        raise FileNotFoundError

    monkeypatch.setattr(capture, "_spawn", missing)

    with pytest.raises(capture.CaptureError, match="pw-record"):
        capture.start(tmp_path)


def test_cancel_stops_and_deletes_the_audio(spawned, tmp_path):
    recording = capture.start(tmp_path)

    capture.cancel(recording)

    assert not recording.path.exists()


def test_discard_deletes_the_file_and_never_complains_if_it_is_gone(spawned, tmp_path):
    recording = capture.start(tmp_path)
    result = capture.stop(recording)

    capture.discard(result.path)
    capture.discard(result.path)

    assert not result.path.exists()


def test_starting_again_replaces_a_leftover_recording(spawned, tmp_path):
    leftover = tmp_path / capture.FILE_NAME
    leftover.write_bytes(b"old")

    capture.start(tmp_path)

    assert not leftover.exists()


class DeadProcess(FakeProcess):
    """pw-record cuando PipeWire no tiene a dónde conectarlo: sale enseguida con error y deja solo el encabezado del WAV."""

    def __init__(self, argv):
        super().__init__(argv, write=False)
        self.returncode = 1
        with open(self.path, "wb") as header_only:
            header_only.write(b"RIFF" + b"\x00" * 40)


def test_without_a_default_microphone_it_fails_right_away_and_says_how_to_fix_it(monkeypatch, tmp_path):
    monkeypatch.setattr(capture, "_spawn", lambda argv, env: DeadProcess(argv))

    with pytest.raises(capture.CaptureError, match="predeterminado") as error:
        capture.start(tmp_path)

    assert "wpctl" in str(error.value)
    assert not (tmp_path / capture.FILE_NAME).exists()  # no queda el WAV vacío


def test_a_recorder_that_dies_while_recording_is_reported_not_taken_for_silence(monkeypatch, tmp_path):
    process = FakeProcess(["pw-record", str(tmp_path / capture.FILE_NAME)], write=False)
    monkeypatch.setattr(capture, "_spawn", lambda argv, env: process)
    recording = capture.start(tmp_path)
    process.returncode = 1  # se cayó a mitad de camino (se desconectó el micrófono)

    with pytest.raises(capture.CaptureError, match="se cortó"):
        capture.stop(recording)


def test_waiting_a_moment_after_starting_is_what_lets_it_notice_the_early_death(monkeypatch, tmp_path):
    waited = []
    monkeypatch.setattr(capture, "STARTUP_CHECK", 0.25)
    monkeypatch.setattr(capture.time, "sleep", waited.append)
    monkeypatch.setattr(capture, "_spawn", lambda argv, env: FakeProcess(argv))

    capture.start(tmp_path)

    assert waited == [0.25]
