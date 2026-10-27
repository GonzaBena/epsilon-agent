from pathlib import Path
import re

import epsilon
from epsilon.settings import config

PACKAGE = Path(epsilon.__file__).parent

# En la raíz del paquete solo vive «el centro»; todo lo demás va en la carpeta de su capa (ver CLAUDE.md).
ROOT_MODULES = {"__init__", "core", "main", "tools", "menu", "state"}


def test_only_the_core_modules_live_at_the_package_root():
    found = {p.stem for p in PACKAGE.glob("*.py")}

    assert found == ROOT_MODULES, f"módulos sueltos: {sorted(found - ROOT_MODULES)}; faltan: {sorted(ROOT_MODULES - found)}"


def test_every_folder_of_the_package_is_a_real_package():
    folders = [p for p in PACKAGE.iterdir() if p.is_dir() and p.name not in ("__pycache__", "web_static")]

    assert folders
    for folder in folders:
        if folder.name == "channels" or any(folder.glob("*.py")):
            assert (folder / "__init__.py").is_file(), folder.name


def test_tests_mirror_the_code_folders():
    tests = Path(__file__).parent
    code_folders = {p.name for p in PACKAGE.iterdir() if p.is_dir() and any(p.glob("*.py"))}

    for name in code_folders:
        assert (tests / name).is_dir(), f"falta tests/{name}/"


def test_no_module_reads_a_registered_setting_from_os_environ():
    # Los ajustes con nombre en settings.json se leen con config.value / config.number: el entorno ya no los trae.
    keys = {s.key for s in config.all_settings() if s.name}
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.parent.name == "settings":
            continue
        text = path.read_text()
        for key in keys:
            if re.search(rf'environ(\.get)?\(?\[?\s*f?"{key}"', text) or f'getenv("{key}"' in text:
                offenders.append(f"{path.relative_to(PACKAGE)}: {key}")
    assert offenders == []


def test_no_module_passes_os_environ_to_a_channel():
    # Los canales (y los plugins) reciben config.environ(), que trae también los ajustes de settings.json.
    offenders = [
        f"{path.relative_to(PACKAGE)}:{number}"
        for path in PACKAGE.rglob("*.py")
        if path.parent.name != "settings"
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"(check_configured|enabled_channels|is_configured|read_config|read_port)\(.*os\.environ", line)
    ]
    assert offenders == []


def test_tunable_constants_are_gone():
    from epsilon.ai import browser_guard, chat
    from epsilon.connectors import manager

    assert not hasattr(chat, "MAX_TOOL_STEPS") and not hasattr(browser_guard, "MAX_SEARCHES")
    assert not hasattr(manager, "MAX_RESULT_CHARS") and not hasattr(manager, "IDLE_CLOSE")
