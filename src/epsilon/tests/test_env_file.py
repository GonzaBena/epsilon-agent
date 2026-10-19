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
