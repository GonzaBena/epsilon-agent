from pathlib import Path

import epsilon

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
