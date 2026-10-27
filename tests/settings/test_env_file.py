import os
from pathlib import Path
import subprocess
import sys

import epsilon


def test_default_env_file_is_next_to_the_code():
    path = epsilon.env_file_path({})

    assert path == Path(epsilon.__file__).parent / ".env"


def test_env_file_can_be_chosen_with_a_variable(tmp_path):
    chosen = tmp_path / "otro.env"

    assert epsilon.env_file_path({"EPSILON_ENV_FILE": str(chosen)}) == chosen


def test_chosen_env_file_expands_the_home_folder():
    path = epsilon.env_file_path({"EPSILON_ENV_FILE": "~/mi-config.env"})

    assert path == Path.home() / "mi-config.env"


def test_an_empty_variable_means_the_default():
    assert epsilon.env_file_path({"EPSILON_ENV_FILE": ""}) == epsilon.env_file_path({})


def test_a_process_with_the_variable_loads_that_file_and_not_the_default(tmp_path):
    # En un proceso aparte, para ver la carga real al importar el paquete.
    chosen = tmp_path / "elegido.env"
    chosen.write_text("GEMINI_MODEL=modelo-del-archivo-elegido\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "GEMINI_MODEL"}
    env["EPSILON_ENV_FILE"] = str(chosen)

    out = subprocess.run(
        [sys.executable, "-c", "import epsilon, os; print(epsilon.ENV_FILE); print(os.environ.get('GEMINI_MODEL'))"],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.split("\n")

    assert out[0] == str(chosen)
    assert out[1] == "modelo-del-archivo-elegido"


def test_env_file_only_loads_secrets_and_bootstrap(tmp_path):
    # En un proceso aparte: lo que no es clave ni de arranque sale del .env (vive en settings.json),
    # pero queda guardado para migrarlo.
    chosen = tmp_path / "elegido.env"
    key = "a" * 30
    chosen.write_text(
        f"USER_NAME=Ana\nGEMINI_API_KEY={key}\nALLOWED_USER_ID=42\nDATA_DIR={tmp_path / 'datos'}\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k not in ("USER_NAME", "GEMINI_API_KEY", "ALLOWED_USER_ID", "DATA_DIR")}
    env["EPSILON_ENV_FILE"] = str(chosen)
    code = (
        "import os; from epsilon.settings import config; "
        "print('USER_NAME' in os.environ, os.environ.get('GEMINI_API_KEY') == 'a' * 30, "
        "os.environ.get('ALLOWED_USER_ID'), config.ENV_AT_START.get('USER_NAME'), "
        "'GEMINI_API_KEY' in config.ENV_AT_START)"
    )

    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout

    assert out.split() == ["False", "True", "42", "Ana", "False"]


def test_a_setting_from_the_process_stays_and_counts_as_from_the_environment(tmp_path):
    chosen = tmp_path / "elegido.env"
    chosen.write_text("USER_NAME=Ana\n", encoding="utf-8")
    env = dict(os.environ, EPSILON_ENV_FILE=str(chosen), USER_NAME="Beto")
    code = "from epsilon.settings import config; print(config.value('USER_NAME'), config.from_process('USER_NAME'))"

    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout

    assert out.split() == ["Beto", "True"]
