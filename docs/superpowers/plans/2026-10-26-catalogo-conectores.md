# Catálogo de conectores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Una pestaña «Catálogo» en `/conectores` desde donde la persona instala un conector tocando «Instalar», sin escribir el comando.

**Architecture:** Módulo nuevo `connectors/catalog.py` (lee y valida la lista incluida más un archivo opcional de la persona, resuelve marcadores y comprueba programas) sin red ni procesos. La web suma `GET /api/catalog` y `POST /api/catalog/{id}/install`, que terminan en lo mismo que `POST /api/connectors` (`store.add` + arrancar). La página suma la pestaña y un diálogo de instalación con el comando exacto y la confirmación.

**Tech Stack:** Python 3 / FastAPI / pytest (sin modelos ni red), HTML + JS plano (todo con `textContent`, CSP sin estilos en línea).

**Spec:** `docs/superpowers/specs/2026-10-26-catalogo-conectores-design.md`

## Global Constraints

- Código en inglés, mensajes al usuario en español rioplatense con voseo; en la UI «IA», no el nombre del modelo.
- `command` es siempre una lista de palabras (nunca un shell). Los únicos marcadores son `{python}` y `{examples}`.
- Instalar exige `confirm: true` en el servidor. La IA no instala ni prende conectores.
- Las claves (`env`) nunca vuelven del servidor ni se escriben en logs; pasan por `connectors/store._check_env`.
- Listar el catálogo no ejecuta ni descarga nada; los tests no usan red ni lanzan procesos (el conector se arranca con el enlace falso de `tests/connectors/fakes.py`).
- La web muestra todo con `textContent`; el menú y la barra se repiten en cada HTML (no se tocan acá).
- Commits en inglés (`feat:`/`fix:`), SIN línea `Co-Authored-By`; nunca push; reiniciar el servicio tras tocar Python (`systemctl --user restart epsilon` y confirmar `is-active`).
- No leer ni mostrar `.env`. Trabajar en un worktree (`git worktree add ../epsilon-catalogo -b feat/catalogo`) porque `master` puede tener cambios sin commitear de la persona.

## Review Focus

- Una entrada de `DATA_DIR/catalog.json` con `command` como string (con `;` o `&&`), con un marcador desconocido (`{home}`), o con tipos equivocados: se descarta y las demás siguen (Task 1).
- El archivo de la persona con JSON roto, enorme o que no es un objeto: la lista incluida sigue funcionando (Task 1).
- Instalar dos veces, o con un nombre que ya existe o está reservado (`notas`): 422 con el motivo y nada nuevo guardado (Task 2).
- Falta `uvx`/`npx` (`needs`): mensaje claro que nombra el programa, nada guardado y nada arrancado (Task 2).
- Faltan claves `required`, o llegan claves que la entrada no declara: se rechaza; el valor de una clave nunca vuelve en ninguna respuesta (Task 2).

---

### Task 1: Módulo del catálogo y el conector de ejemplo

**Files:**
- Create: `src/epsilon/connectors/catalog.py`, `src/epsilon/connectors/catalog.json`, `tests/connectors/test_catalog.py`
- Move: `docs/ejemplos/luces_mcp.py` → `src/epsilon/connectors/examples/luces_mcp.py` (`git mv`; crear `examples/__init__.py` NO: es un archivo suelto que se lanza por ruta)
- Modify: `tests/connectors/test_example.py` (la ruta `EXAMPLE`), referencias a `docs/ejemplos/luces_mcp.py` en `docs/plan-conectores.md` y `CLAUDE.md`, y el comentario del propio ejemplo (cómo se agrega)

**Interfaces:**
- Consumes: `connectors.store.NAME` (regex del nombre), `store.ENV_KEY`, `store.get(name) -> dict | None`, `notes.safe_path`.
- Produces (`epsilon.connectors.catalog`):
  - `FILE = "catalog.json"`, `MAX_ENTRIES = 100`, `PLACEHOLDERS = ("{python}", "{examples}")`.
  - `class CatalogError(Exception)` (texto en español para la persona).
  - `@dataclass(frozen=True) class Entry`: `id: str`, `title: str`, `description: str`, `name: str`, `command: tuple[str, ...]`, `env: tuple[dict, ...]` (cada uno `{"key","label","secret","required"}`), `needs: tuple[str, ...]`, `homepage: str`, `source: str` (`"incluido"` o `"tuyo"`).
  - `load() -> list[Entry]`: incluida + la de `DATA_DIR/catalog.json`; mismo `id` → gana la de la persona; entradas inválidas se descartan; tope `MAX_ENTRIES`; nunca levanta.
  - `get(entry_id: str) -> Entry | None`.
  - `resolve(entry: Entry) -> list[str]`: el comando con `{python}` = `sys.executable` y `{examples}` = carpeta `examples/` del paquete.
  - `missing(entry: Entry) -> list[str]`: los `needs` que `shutil.which` no encuentra (`{python}` no cuenta).
  - `installed(entry: Entry) -> bool`: ya hay un conector llamado `entry.name`.

- [ ] **Step 1: Write the failing tests** en `tests/connectors/test_catalog.py` (fixture como `tests/connectors/test_store.py`: `DATA_DIR` temporal):
  - `test_bundled_catalog_has_the_example_fetch_and_sqlite` (ids `luces`, `fetch`, `sqlite`, todos `source == "incluido"`; cada `name` cumple `store.NAME`).
  - `test_every_bundled_entry_is_valid_and_its_command_resolves` (`resolve` no deja llaves; el de `luces` apunta a un archivo que existe).
  - `test_user_file_adds_and_overrides_entries` (id nuevo `source == "tuyo"`; mismo id pisa).
  - `test_command_must_be_a_list_of_words_and_markers_are_limited` (string `"uvx x; rm -rf /"`, `["{home}/x"]`, `[]`, palabra con salto de línea → descartadas; las demás siguen).
  - `test_bad_types_and_lengths_are_dropped` (`title` número, `env` no lista, `homepage` `http://` o `javascript:`, `description` > 300 caracteres, `id` con `/` o `..`).
  - `test_broken_or_huge_user_file_leaves_the_bundled_list` (JSON roto, lista en vez de objeto, archivo > 200 KB).
  - `test_entries_are_capped`.
  - `test_missing_lists_absent_programs` (monkeypatch `catalog.shutil.which`) y `test_installed_follows_the_store`.
  - `test_get_unknown_and_weird_ids_return_none`.
  - En `tests/connectors/test_example.py`: la misma suite pasa con la nueva ruta.
- [ ] **Step 2: Run** `uv run pytest tests/connectors/test_catalog.py -v` → FAIL (módulo inexistente).
- [ ] **Step 3: Implement** `catalog.py` y `catalog.json` (objeto `{"connectors": [ … ]}` con las tres entradas: `luces` — «Luces (ejemplo)», sin claves, `command ["{python}", "{examples}/luces_mcp.py"]`; `fetch` — `["uvx","mcp-server-fetch"]`, `needs ["uvx"]`; `sqlite` — `["uvx","mcp-server-sqlite","--db","datos.db"]`, `needs ["uvx"]`). Hacer `git mv` del ejemplo y arreglar las referencias. Validación por entrada en una función `_parse(raw: object, source: str) -> Entry | None`; el archivo de la persona se lee con `notes.safe_path` y tope de 200 KB.
- [ ] **Step 4: Run** `uv run pytest tests/connectors -q` → PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: connector catalog module with a bundled example"`

---

### Task 2: API del catálogo

**Files:**
- Modify: `src/epsilon/channels/web.py` (junto a `/api/connectors`, ~línea 880 hoy)
- Test: `tests/channels/web/test_catalog_api.py`

**Interfaces:**
- Consumes: `catalog.load/get/resolve/missing/installed`, `store.add(name, argv, env)`, `connector_start(name)`, `split`-style `shlex.join` para mostrar el comando.
- Produces:
  - `catalog.prepare(entry_id: str, name: str | None, env: dict[str, str]) -> tuple[str, list[str], dict[str, str]]` (en `catalog.py`): `(nombre, argv, env)` listos para `store.add`; levanta `CatalogError` si no existe la entrada, faltan programas (nombra el programa), falta una clave `required`, llega una clave no declarada o el nombre no vale.
  - `GET /api/catalog` → `{"entries":[{"id","title","description","name","command" (texto, ya resuelto),"env":[{"key","label","secret","required"}],"needs","missing","homepage","source","installed"}]}` (nunca valores de claves).
  - `POST /api/catalog/{id}/install` body `{"name"?: str, "env": {…}, "confirm": bool}` → la misma respuesta que `GET /api/connectors`. Sin `confirm: true`: 422 «Confirmá que querés correr este programa.». 404 si el id no existe. 422 con el motivo para `CatalogError`/`StoreError`.

- [ ] **Step 1: Write the failing tests** (cliente y `install(monkeypatch, {...})` de `tests/connectors/fakes.py` como en `test_connectors_api.py`):
  - `test_get_lists_entries_with_resolved_command_and_no_secrets`.
  - `test_install_requires_confirm` (422, nada guardado, `link.opens == []`).
  - `test_install_example_adds_the_connector_and_starts_it` (conector `luces` en el store; `installed` pasa a `True`).
  - `test_install_twice_or_name_taken_is_422` y `test_install_with_reserved_name_is_422` (`name: "notas"`).
  - `test_install_missing_program_is_422_naming_it_and_saves_nothing` (monkeypatch `catalog.shutil.which` → `None`).
  - `test_install_requires_declared_required_env_and_rejects_unknown_keys` (con `DATA_DIR/catalog.json` que declara `TOKEN` requerido).
  - `test_env_values_never_come_back` (el valor `SECRETO` no aparece en ninguna respuesta de `GET /api/catalog` ni de `/api/connectors`).
  - `test_unknown_id_is_404` y `test_install_name_override_is_used`.
  - `test_foreign_origin_cannot_install` (403).
- [ ] **Step 2: Run** `uv run pytest tests/channels/web/test_catalog_api.py -v` → FAIL.
- [ ] **Step 3: Implement** `catalog.prepare` y las dos rutas (modelo `CatalogInstallIn`: `name: str | None = Field(None, max_length=40)`, `env: dict[str, str] = Field(default_factory=dict, max_length=20)`, `confirm: bool = False`). La instalación reutiliza `connector_store.add` y `connector_start` tal cual lo hace `create_connector`.
- [ ] **Step 4: Run** `uv run pytest -q` completo → PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: catalog API to install connectors with confirmation"`

---

### Task 3: Pestaña Catálogo en la página Conectores

**Files:**
- Modify: `src/epsilon/channels/web_static/conectores.html`, `js/conectores.js`, `css/style.css` (solo lo propio)
- Test: `tests/channels/web/test_catalog_page.py`

**Interfaces:**
- Consumes: `GET /api/catalog`, `POST /api/catalog/{id}/install`; las clases `claude-*`, el diálogo `claude-dialog` y `setTab` de la página.
- Produces: pestaña `#seg-catalog` / panel `#panel-catalog` con la lista `#cat-list` (vacío: `#cat-empty`), una fila por entrada con título, descripción, etiqueta «incluido»/«tuyo», los programas que faltan (si hay) y «Instalar» (o «Instalado»); diálogo `#cat-dialog` con título, comando exacto en un `<pre>`, campos de claves (un `<input>` por `env`, `type=password` si `secret`), el aviso «Este programa va a correr en tu PC con tu usuario. Instalalo solo si confiás en quien lo escribió.», `#cat-confirm` («Confirmar e instalar») y `#cat-error`.

- [ ] **Step 1: Write the failing tests** (textuales, como `test_skills_page.py`): `test_connectors_page_has_a_catalog_tab_and_dialog` (ids anteriores presentes), `test_catalog_install_always_sends_confirm_true` (`confirm: true` en `conectores.js` dentro del manejador de instalar), `test_catalog_js_never_uses_html_injection` (sin `innerHTML`), `test_catalog_dialog_shows_the_exact_command_and_warning`, `test_secret_fields_are_password_inputs`, `test_no_inline_styles_in_conectores_html`, `test_menu_and_top_bar_still_match_other_pages`.
- [ ] **Step 2: Run** `uv run pytest tests/channels/web -q -k "catalog_page or pages"` → FAIL.
- [ ] **Step 3: Implement** la pestaña (tercera, «Catálogo») y el diálogo reutilizando `setTab` generalizado a tres vistas; cargar con `GET /api/catalog` al abrir la pestaña y refrescar la lista de «Tuyos» tras instalar (la respuesta del POST es la lista de conectores: pasarla a `adopt`). Probar en un Chrome real con un servidor aparte (`EPSILON_ENV_FILE` y `DATA_DIR` temporales, puerto 8799): instalar el ejemplo, verlo en «Tuyos» y sus herramientas; probar `fetch` sin `uvx` (aviso). Dejar la URL para que la persona mire el diseño.
- [ ] **Step 4: Run** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: Catalog tab on the Connectors page"`

---

### Task 4: Documentación y cierre

**Files:**
- Modify: `CLAUDE.md` (sección Conectores MCP: catálogo), `README.md` (cómo instalar un conector), `docs/plan-conectores.md` (ruta nueva del ejemplo y el catálogo), `docs/plan-ui.md`
- Memory: actualizar `epsilon-roadmap-for-other-users.md` (catálogo hecho; sigue rutinas con IA)

- [ ] **Step 1: Write the doc changes**: formato de una entrada, cómo agregar la tuya en `DATA_DIR/catalog.json` (con un ejemplo), marcadores `{python}`/`{examples}`, y que Gmail/Calendar (OAuth) siguen fuera.
- [ ] **Step 2: Run** `uv run pytest -q` → PASS (los tests de README/CLAUDE vigilan comandos y páginas).
- [ ] **Step 3: Commit** `git add -A && git commit -m "docs: connector catalog"`
- [ ] **Step 4: Reiniciar y verificar** `systemctl --user restart epsilon && systemctl --user is-active epsilon` → `active`; abrir `/conectores`, pestaña Catálogo: aparece el ejemplo.

---

## Self-review

- **Cobertura del spec:** fuentes y formato (T1), instalar con confirmación y claves (T2), pestaña y diálogo (T3), documentación y guía de cómo sumar entradas (T4); el ejemplo viaja con el paquete (T1).
- **Fuera del plan, a propósito:** lista de internet, registro público, OAuth, guía web.
- **Tipos consistentes:** `Entry`, `catalog.prepare/resolve/missing/installed` se usan igual en T2 y T3; la respuesta del POST es la de `connectors_view`.
