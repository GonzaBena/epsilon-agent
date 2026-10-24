# Conector de ARCA (ex AFIP)

Emitir y consultar comprobantes electrónicos desde Epsilon, **sin gastar IA**: Factura C (monotributo), notas de crédito,
facturas en lote desde un CSV, PDF con el QR de ARCA y búsqueda de clientes en el padrón. Usa los web services oficiales
de ARCA (WSAA + WSFEv1 + padrón A13); no scrapea la página ni usa tu clave fiscal: entra con un **certificado** propio.

> **Todo arranca en homologación** (el ambiente de pruebas de ARCA: no pasa nada real ni tiene validez fiscal). En
> **producción** la emisión está apagada hasta que la habilites a propósito, cuenta por cuenta.

**Índice**: [Resumen](#resumen-de-los-pasos) · [Instalar](#1-agregar-el-conector-a-epsilon) · [Cuenta de pruebas](#2-cuenta-de-pruebas-homologación) ·
[Usar](#3-usar-el-conector) · [Producción](#4-pasar-a-producción) · [Varias cuentas](#5-varias-cuentas) ·
[Referencia](#6-referencia) · [Preguntas frecuentes](#7-preguntas-frecuentes-y-errores)

## Resumen de los pasos

| # | Paso | Dónde | Cuánto |
|---|---|---|---|
| 1 | Agregar el conector a Epsilon | Epsilon → Conectores | 2 min |
| 2 | Crear la cuenta y su clave | Terminal | 1 min |
| 3 | Certificado + autorizar `wsfe` (y el padrón) | ARCA → WSASS | 10 min |
| 4 | Punto de venta de web services | ARCA → Puntos de venta | 5 min |
| 5 | `/c arca verificar` y primera factura de **prueba** | Epsilon | 5 min |
| 6 | Repetir 2–4 para **producción** (otro certificado) | ARCA | 20 min |
| 7 | Habilitar `"emision": true` y emitir | `cuentas.json` | — |

## 1. Agregar el conector a Epsilon

Página **Conectores** → agregar:

- **Nombre**: `arca`
- **Comando** (ruta absoluta; la primera vez instala solo `mcp`, `cryptography`, `fpdf2` y `segno`, y tarda unos segundos):
  `uv run /ruta/a/epsilon/conectores/arca/arca_mcp.py`
- **Variables** (todas opcionales): `ARCA_HOME` (otra carpeta de datos), `ARCA_DEFAULT` (cuenta predeterminada si hay varias),
  `ARCA_PDF_DIR` (otra carpeta para los PDF).

Las herramientas arrancan en `ask` en `/permisos`. Para que **la IA no las use**, poné `deny`: son para vos, desde `/c`.

> **Después de actualizar el código del conector, reiniciá Epsilon** (`systemctl --user restart epsilon`): el proceso del conector
> queda abierto un rato y puede seguir con el código viejo.

## 2. Cuenta de pruebas (homologación)

### 2.1 Crear la cuenta (clave + pedido de certificado)

```
uv run /ruta/a/epsilon/conectores/arca/arca_mcp.py nueva negocio_test --cuit 20123456789 --nombre "Mi Negocio"
```

- `negocio_test` es el nombre de la cuenta (minúsculas, 2 a 20 caracteres, empieza con letra). El CUIT son 11 números, sin guiones.
  Conviene **dejar el nombre «bueno» (`negocio`) para la cuenta de producción**.
- Crea en `~/.config/epsilon-arca/`: la clave privada `negocio_test.key` (permisos 600), el pedido de certificado `negocio_test.csr`
  y la entrada en `cuentas.json`. **La clave no se comparte ni se sube a ningún lado.** Nunca pisa una existente.
- Opciones: `--ambiente homologacion|produccion` (por defecto homologación), `--condicion monotributo|responsable_inscripto|exento`
  (monotributo usa Factura C), `--nombre` (el que sale en el PDF).
- Homologación y producción usan **certificados distintos**: son dos cuentas, aunque sea el mismo CUIT.

### 2.2 Certificado de pruebas en ARCA (una sola vez)

Entrás con tu clave fiscal (la de la persona física). Los nombres de las pantallas de ARCA cambian de vez en cuando: si alguno no coincide, buscá el parecido.

1. En el **Administrador de Relaciones de Clave Fiscal**, adherí el servicio **WSASS** (autogestión de certificados de prueba).
2. Entrá a WSASS → **Nuevo certificado**: un nombre cualquiera y pegá el contenido del `.csr`. Descargá el `.crt`.
3. Guardalo como `~/.config/epsilon-arca/negocio_test.crt`.
4. En WSASS → **Crear autorización a servicio**: elegí tu certificado y el servicio **`wsfe`** (Facturación Electrónica).
5. *(Opcional, para buscar clientes)* repetí la autorización con **`ws_sr_padron_a13`** (Consulta a Padrón Alcance 13).

### 2.3 Punto de venta

El de «Comprobantes en línea» (el que usás en la web) **no sirve** para web services: hace falta otro.

1. Servicio **Administración de puntos de venta y domicilios** → **A/B/M de puntos de venta** → **Agregar**.
2. Un número libre (por ejemplo 2) y el sistema **«Factura Electrónica - Monotributo - Web Services»** (si no sos monotributista,
   «RECE para aplicativo y web services»). Elegí un domicilio ya registrado.
3. Anotalo en `cuentas.json` como `"punto_venta": 2` (ver [cuentas.json](#cuentasjson)).

> El punto de venta es del CUIT, no del ambiente: el que creaste para las pruebas debería servir también para producción (si ARCA dice lo contrario, creá otro).

### 2.4 Verificar

```
/c arca verificar cuenta=negocio_test
```

Revisa en orden los archivos, el certificado (vigencia, CUIT, que la clave le corresponda), el ingreso a ARCA, el estado de los
servidores, los puntos de venta y la actividad configurada, y marca con ✗ lo que falla y por qué. Cuando todo esté en ✓, ya podés probar.

## 3. Usar el conector

Todo se usa con `/c arca <herramienta> clave=valor` (sin IA). Todas aceptan `cuenta=<nombre>`.

### Emitir una Factura C

Siempre en **dos pasos**, para no emitir nada por error:

```
/c arca emitir_factura importe=1500.50 concepto=productos_y_servicios documento=12345678 descripcion="Repuestos y reparaciones"
```

1. **Simula**: muestra la cuenta, el ambiente, el número que le tocaría, el receptor (con nombre y domicilio del padrón), el importe y
   los avisos ⚠. **No emite nada** y termina con un código: `confirmar=7f3a9c`.
2. **Emite** si repetís el mismo pedido agregando ese código. Devuelve el número, el **CAE** y su vencimiento, y arma el **PDF**.

El código depende de todos los datos **y del número que le toca**: no sirve dos veces ni si cambiás algo, y repetir un comando ya
emitido no emite otra factura.

| Argumento | Qué es |
|---|---|
| `importe` | Total, hasta 2 decimales (no redondea por su cuenta). En Factura C no hay IVA |
| `concepto` | `productos`, `servicios` o `productos_y_servicios` (o guardalo en la cuenta) |
| `documento`, `tipo_documento` | DNI, CUIL o CUIT del receptor, con puntos o guiones si querés. El tipo se deduce (hasta 8 números: DNI; 11 que empiezan con 20/23/24/27: CUIL; otros: CUIT) y se puede forzar con `tipo_documento=dni\|cuil\|cuit`. Se revisa el dígito verificador |
| `sin_identificar` | `true` para un consumidor final sin documento (solo si no pasás `documento`) |
| `condicion_receptor` | `consumidor_final` (por defecto), `monotributo`, `responsable_inscripto`, `exento`, `no_categorizado`, `monotributo_social`, o el número (ver `condiciones_iva_receptor`) |
| `descripcion` | Qué vendiste. **ARCA no la guarda**: queda en el registro local y en el PDF |
| `localidad` | P. ej. «CABA»: domicilio del comprobante cuando no se tiene el del cliente. Queda en el registro y el PDF |
| `fecha` | AAAA-MM-DD, por defecto hoy. ARCA acepta hasta 5 días antes o después con productos y 10 con servicios |
| `servicio_desde`, `servicio_hasta`, `vencimiento_pago` | Para servicios; por defecto la fecha del comprobante |
| `punto_venta` | Por defecto el de la cuenta |
| `pdf` | `false` para no generar el PDF (por defecto sí) |

**Datos obligatorios.** Por defecto solo se exige lo que pide ARCA (más el concepto): si faltan el documento o la descripción, la
simulación avisa con ⚠ pero deja seguir. Para que **no se pueda emitir sin ellos**, como en el facturador web, la cuenta lo exige:
`"obligatorios": ["documento", "descripcion", "localidad"]` (ponés los que quieras). El pago es siempre «Contado».

**Cliente según el padrón.** Con un documento, la simulación consulta el padrón (solo lectura) y muestra `Cliente (padrón): nombre · domicilio`.
Si un DNI tiene **varias claves** (CUIL distintos), no deja seguir hasta que pases el CUIL como `documento`, como hace la web. Si el
padrón no responde o no conoce a esa persona, avisa con ⚠ y la factura se puede emitir igual.

**PDF.** Un comprobante simple (A4): tus datos, el cliente, la descripción, «Contado», el total, el CAE y su vencimiento, y el **QR de
ARCA** (el enlace oficial `afip.gob.ar/fe/qr/?p=…`). Se guarda en `~/.config/epsilon-arca/comprobantes/` (o `ARCA_PDF_DIR` / `pdf_dir`)
como `<cuenta>-0002-00000002.pdf`, con permisos 600. En homologación lleva un cartel rojo «comprobante de prueba». Si falla, la factura
igual queda emitida y el aviso dice cómo rehacerlo: `/c arca pdf_comprobante numero=2` (de una nota de crédito: `tipo=13`). Si la
factura salió sin los datos del cliente porque el padrón no respondía, al rehacer el PDF vuelve a consultarlo.

**Si ARCA no responde después de recibir el pedido**, el conector **no lo repite**: mira el último número autorizado y te dice si se
emitió o no. Si tampoco puede mirar, te pide que **no repitas** y que revises con `ultimo_comprobante`.

### Facturas desde un CSV

```
/c arca emitir_lote archivo=/ruta/facturas.csv
```

```csv
ID,nombre,dni,precio,localidad
MP-1001,Juan Perez,12345678,15000.50,
MP-1002,Ana Gomez,20111222334,"8.200,00",Palermo
```

- **Columnas**: `ID` (tu referencia), `nombre`, `dni` (también `cuil`, `cuit` o `documento`) y `precio` son obligatorias. `localidad` es
  opcional (vacía: la de la cuenta, y si no, `CABA`). También se aceptan `descripcion`, `concepto` y `fecha`; lo que falte sale de la cuenta.
  Sirve el CSV de Excel (coma o punto y coma, UTF-8 o Windows) y precios como `15000.50`, `15.000,50` o `$ 1.500`.
- **Dos pasos, como siempre.** Sin `confirmar` **no emite nada**: valida cada fila con las mismas reglas que `emitir_factura`, consulta el
  padrón y lista una línea por fila con el número que le tocaría, el total y un `confirmar=<código>`. El código depende del archivo
  completo y del número que toca: si cambiás algo, deja de servir.
- **Nombres**: se compara el `nombre` del archivo con el del padrón (alcanza con que estén todas las palabras que escribiste, sin importar
  orden, mayúsculas ni acentos: «Juan Perez» coincide con «PEREZ JUAN CARLOS»). Si no coincide, o el padrón no responde o no conoce el
  documento, la fila queda **REVISAR** y **no sale** salvo que la aceptes de forma explícita: `aceptar=MP-1002,MP-1003` (los ID).
- **Errores**: una fila con **ERROR** (importe mal, falta el documento, ID repetido) frena todo el lote hasta que arregles el CSV.
- **No factura dos veces**: un ID que ya salió se saltea (**YA EMITIDA**). Si un intento anterior de ese ID quedó sin confirmar, avisa y no sigue.
- **Se frena en la primera falla** al emitir (ARCA rechaza o no contesta) y **nunca reintenta**; te dice cuáles quedaron sin procesar.
  Cada factura sale con su PDF y su línea en `emitidas.jsonl` (con el ID como `referencia`).
- Tope de 50 filas por lote (`"max_lote"` en la cuenta, hasta 500).

### Corregir una factura (nota de crédito C)

Una factura emitida no se borra ni se edita: se corrige con una **nota de crédito** que la anula, entera o en parte.

```
/c arca emitir_nota_credito numero=46 motivo="Devolución"
/c arca emitir_nota_credito numero=46 importe=100 motivo="Descuento"      (parcial)
```

- **Dos pasos**: sin `confirmar` simula (qué factura corrige, el importe, si es total o parcial y el número que le toca) y da un código.
  Una nota emitida **no se puede deshacer**.
- `numero` es el de la **factura** (mismo punto de venta de la cuenta, o `punto_venta=`). Sin `importe` anula lo que quede por acreditar.
- Toma los datos (cliente, importe, concepto) del registro local; si la factura salió por la web de ARCA, los lee de ARCA y lo avisa.
- **No deja acreditar de más**: suma las notas ya emitidas desde acá y rechaza un importe que supere lo que queda, o una factura ya
  anulada por completo. (Solo conoce las notas hechas con este conector: si hiciste otra por la web, tenelo en cuenta.)
- Se envía asociada a la factura. Genera su PDF (`<cuenta>-NC-0003-00000008.pdf`, «NOTA DE CRÉDITO · COD. 013»).

### Consultas (solo lectura, no emiten nada)

| Herramienta | Qué hace |
|---|---|
| `cuentas` | Cuentas configuradas: CUIT, ambiente y si están el certificado y la clave |
| `verificar` | Revisa paso a paso que la cuenta esté lista |
| `puntos_de_venta` | Puntos de venta habilitados para web services |
| `tipos_de_comprobante` | Los tipos que ARCA permite (11 = Factura C, 13 = Nota de crédito C…) |
| `ultimo_comprobante` | Último número autorizado. Sin `tipo` usa el de la cuenta (monotributo: 11); `tipo=13` para notas de crédito |
| `ver_comprobante` | Fecha, importes, receptor, concepto y CAE de uno ya autorizado: `/c arca ver_comprobante numero=45` |
| `actividades` | Las actividades del emisor que ARCA acepta asociar a un comprobante (el código va en `actividad`) |
| `condiciones_iva_receptor` | Las condiciones frente al IVA del receptor que ARCA acepta en una Factura C |
| `buscar_persona` | Nombre y domicilio fiscal de un DNI, CUIL o CUIT: `/c arca buscar_persona documento=12345678`. Con un DNI muestra todas las claves |

## 4. Pasar a producción

La primera factura de producción **es real**: no existe «factura de prueba» ahí y solo se corrige con una nota de crédito. Por eso
todo se prepara y se verifica **sin emitir** (las consultas y las simulaciones son de solo lectura) y emitir es el último paso.

### 4.1 Cuenta de producción

```
uv run /ruta/a/epsilon/conectores/arca/arca_mcp.py nueva negocio --cuit 20123456789 --ambiente produccion --nombre "Mi Negocio"
```

Si todavía tenés la cuenta de pruebas con el nombre `negocio`, primero renombrala (dejá el nombre «bueno» para la real):

```
cd ~/.config/epsilon-arca
for e in key crt csr; do mv negocio.$e negocio_test.$e; done
for f in cache/negocio-homologacion*.json; do mv "$f" "${f/negocio-/negocio_test-}"; done
jq 'with_entries(if .key=="negocio" then .key="negocio_test" else . end)' cuentas.json > cuentas.tmp && chmod 600 cuentas.tmp && mv cuentas.tmp cuentas.json
```

(Las líneas viejas de `emitidas.jsonl` quedan con el nombre anterior: solo es historial de pruebas.)

### 4.2 Certificado real

1. Con tu clave fiscal, adherí el servicio **Administración de Certificados Digitales** (desde el Administrador de Relaciones). Suele pedir una clave fiscal con nivel de seguridad alto.
2. Entrá → **Nuevo alias** (por ejemplo `epsilon`) → subí `~/.config/epsilon-arca/negocio.csr` → descargá el `.crt`.
3. Guardalo como `~/.config/epsilon-arca/negocio.crt`.

### 4.3 Relaciones (autorizar los servicios)

En el **Administrador de Relaciones de Clave Fiscal** → **Nueva relación**, **una por servicio**:

1. Servicio: *ARCA → WebServices → **Facturación Electrónica*** (`wsfe`). En «Representante» elegí el **alias** del certificado del paso anterior (aparece como computador fiscal). Confirmar.
2. *(Opcional, para buscar clientes)* Servicio: **Consulta a Padrón Alcance 13** (`ws_sr_padron_a13`), con el mismo representante.

### 4.4 Punto de venta de web services

Si el de la sección [2.3](#23-punto-de-venta) ya existe, se puede usar el mismo. Si no, creá uno nuevo (sistema «Factura Electrónica - Monotributo - Web Services»).
Cada punto de venta numera por su cuenta: **la primera factura será la 0001**, aunque ya hayas emitido miles por la web en otro punto de venta.

> **Aviso** (RG 5893/2026, según lo informado: confirmalo en ARCA): desde el 1/11/2026 ARCA crearía un punto de venta automático «Factura en línea - Monotributo» para los monotributistas. Ese punto **no sirve para web services** (ARCA lo rechaza con el error 10005): conservá uno de web services y usá ese número en `punto_venta`.

### 4.5 Configurar y verificar (sin emitir)

Editá `~/.config/epsilon-arca/cuentas.json` y completá la cuenta (los campos están en la [referencia](#cuentasjson)). Un ejemplo para un monotributista de comercio:

```json
"negocio": {
  "cuit": "20123456789", "ambiente": "produccion", "condicion": "monotributo", "nombre": "Mi Negocio",
  "domicilio_emisor": "Av. Siempreviva 742, CABA", "punto_venta": 3,
  "concepto": "productos_y_servicios", "descripcion": "Repuestos y reparaciones", "localidad": "CABA",
  "obligatorios": ["documento", "descripcion", "localidad"]
}
```

Y verificá, en este orden (nada de esto emite):

1. `/c arca verificar cuenta=negocio`: todo en ✓ (la cuenta debe figurar «solo lectura»).
2. `/c arca puntos_de_venta cuenta=negocio`: aparece el tuyo.
3. `/c arca actividades cuenta=negocio`: si en la web asociabas una actividad (por ejemplo `479900`), tiene que figurar acá; entonces agregá `"actividad": "479900"` a la cuenta. Si ARCA la rechaza (errores 10220 / 10223), quitá `actividad` o usá una de la lista.
4. `/c arca buscar_persona documento=<tu DNI> cuenta=negocio`: debe traer tu nombre y domicilio. Si da error de autorización, falta la relación de `ws_sr_padron_a13`.
5. `/c arca ultimo_comprobante cuenta=negocio`: 0 si el punto de venta es nuevo.
6. `/c arca emitir_factura importe=… documento=… cuenta=negocio`: **sin** `confirmar=`: es la simulación, no emite. Mirá número, cliente y avisos.

### 4.6 Habilitar la emisión y la primera factura

1. En `cuentas.json`, agregá `"emision": true` a la cuenta de producción (solo vale el valor `true`). `/c arca cuentas` pasa de «solo lectura» a emisión habilitada.
2. Repetí la simulación y, **recién ahora**, repetí con `confirmar=<código>`: la primera factura real. Conviene que sea una venta de verdad y de importe chico.
3. Revisá el PDF y que la factura figure en ARCA (**Mis Comprobantes**). Si algo salió mal, `emitir_nota_credito`.
4. Para volver a «solo lectura» cuando quieras, sacá la línea `"emision": true`.

## 5. Varias cuentas

Cada CUIT tiene su certificado y su cuenta (`negocio`, `familiar`…), todas en el mismo `cuentas.json`.

- **Elegir la cuenta**: `cuenta=<nombre>` en cualquier herramienta, o `ARCA_DEFAULT=<nombre>` en las variables del conector. Con varias cuentas y sin ninguna de las dos, el conector pregunta cuál.
- **Para otra persona** (por ejemplo, un familiar monotributista): la cuenta lleva **su** CUIT y el trámite de las secciones 2 y 4 se hace entrando con **su** clave fiscal, con su permiso. Los certificados no se comparten entre CUIT.
- Cada cuenta tiene su propio punto de venta, su numeración y su registro de ID: un `ID` ya emitido en una cuenta no afecta a la otra.

## 6. Referencia

### `cuentas.json`

Solo datos, **sin secretos** (las claves son archivos aparte con permisos 600):

```json
{
  "negocio": {"cuit": "20123456789", "ambiente": "homologacion", "condicion": "monotributo", "punto_venta": 3}
}
```

| Campo | Qué es |
|---|---|
| `cuit` | 11 números, sin guiones (obligatorio) |
| `ambiente` | `homologacion` (por defecto) o `produccion` |
| `condicion` | `monotributo` (Factura C), `responsable_inscripto` o `exento` |
| `punto_venta` | Número de tu punto de venta de web services |
| `concepto` | `productos`, `servicios` o `productos_y_servicios`, para no escribirlo cada vez |
| `descripcion` | Texto por defecto de lo vendido, p. ej. `"Repuestos y reparaciones"` |
| `localidad` | Por ejemplo `"CABA"`: domicilio del comprobante cuando no se tiene el del cliente |
| `obligatorios` | Datos que esta cuenta exige además de lo de ARCA: `"documento"`, `"descripcion"`, `"localidad"` |
| `actividad` | Código de actividad que ARCA asocia al comprobante (p. ej. `"479900"`). Solo se envía si lo ponés; tiene que ser una de las activas del emisor (`actividades`). **En homologación casi nunca existe: probá sin ella** |
| `nombre`, `domicilio_emisor` | Lo que sale arriba en el PDF (tu nombre o razón social y tu domicilio comercial) |
| `pdf` | `false` para no generar nunca el PDF |
| `pdf_dir` | Otra carpeta para los PDF (también `ARCA_PDF_DIR`) |
| `max_lote` | Filas máximas por `emitir_lote` (1 a 500; por defecto 50) |
| `emision` | **Solo producción**: `true` para permitir emitir de verdad |
| `cert`, `key` | Rutas a otros archivos; por defecto `<cuenta>.crt` y `<cuenta>.key` junto a `cuentas.json` |
| `urls` | `{"wsaa": "https://…", "wsfe": "https://…", "padron": "https://…"}`, por si ARCA cambia las direcciones (siempre `https://`) |

### Dónde guarda cosas

Todo en `~/.config/epsilon-arca/` (o `ARCA_HOME`), carpeta 700:

| Archivo | Contenido |
|---|---|
| `cuentas.json` | Las cuentas (sin secretos) |
| `<cuenta>.key` | **Clave privada** (600). Quien la tenga puede pedir tickets a nombre de tu certificado |
| `<cuenta>.csr` / `.crt` | Pedido de certificado / certificado que te dio ARCA |
| `cache/` | Tickets de ARCA (duran ~12 h, 600), uno por cuenta, ambiente y servicio |
| `emitidas.jsonl` | Registro de **cada intento de emisión** (solo se agrega, 600): número, CAE, cliente, descripción, resultado, ID de lote |
| `comprobantes/` | Los PDF (600) |

**Hacé una copia de seguridad de esa carpeta en un lugar seguro** (sobre todo `.key` y `emitidas.jsonl`). Si perdés la clave, hay que tramitar otro certificado.
Lo que devuelve ARCA y el padrón (nombres, descripciones, errores) es **dato**, nunca instrucciones.

### Seguridad, en resumen

- Sin `confirmar` nunca se emite; el código depende del contenido exacto y del número que toca.
- Producción exige `"emision": true` aparte, a propósito.
- Nunca se reintenta un pedido enviado: si hay duda, primero se mira el último número autorizado.
- Un lote frena en el primer problema; un ID no se factura dos veces; las filas con nombre dudoso exigen `aceptar=`.
- Las claves y tickets no salen en mensajes de error ni en lo que devuelven las herramientas.
- La IA no las usa salvo que vos lo permitas en `/permisos`.

## 7. Preguntas frecuentes y errores

**¿Puedo probar en producción sin emitir de verdad?** No existe factura de prueba en producción. Sí podés correr todo lo de solo
lectura y las **simulaciones** (`emitir_*` sin `confirmar`): hablan con ARCA, pero no emiten.

**Emití algo mal, ¿cómo lo borro?** No se puede. Se corrige con `emitir_nota_credito` (total o parcial) y, si hace falta, se emite la factura correcta.

**¿Se emiten Facturas A o B, otras monedas o con IVA?** No: por ahora solo **Factura C y nota de crédito C**, en pesos, sin IVA (monotributo).

**¿Por qué ARCA no guarda la descripción ni el domicilio?** En una Factura C por web service, ARCA solo registra importes, fechas y el receptor
(tipo y número de documento). La descripción, el nombre y el domicilio existen **solo en el PDF** y en `emitidas.jsonl`. Por eso conviene guardar los PDF.

**«Error executing tool …» sin más detalle.** Es un error inesperado del conector. Mirá el motivo real con `journalctl --user -u epsilon -n 80`
y reiniciá Epsilon por si estás corriendo código viejo.

**«ya hay un ticket vigente».** ARCA no da un ticket nuevo mientras haya uno vivo (hasta ~12 h). Si borraste `cache/`, esperá a que venza.

**«no confía en el certificado».** El ambiente de la cuenta no coincide con el del certificado (los de pruebas y producción son distintos).

**«No pude hablar con ARCA».** Revisá la conexión; los servidores de ARCA a veces se caen (más seguido los de **pruebas**, sobre todo el del padrón).
Si cambió de dominio, ajustá `urls`. Una emisión con este error no se repite sola: te dice si se emitió o no.

**No hay puntos de venta / «Sin Resultados» (602).** Falta crear uno de web services (sección 2.3) y que se haya habilitado un rato antes.

**Error 10005 o similares con el punto de venta.** Es de un tipo que no sirve para web services (por ejemplo el automático «Factura en línea»).
Usá uno de web services.

**«actividad … no habilitada» (10220 / 10223).** La actividad de `cuentas.json` no está entre las del emisor. Mirá `actividades` o quitá `actividad`.

**ARCA rechaza la fecha.** Productos: hasta 5 días antes o después de hoy; servicios: hasta 10.

**ARCA rechaza el documento (10015).** El tipo o el número del receptor no son válidos para ARCA. Revisá el dígito verificador y el tipo (DNI/CUIL/CUIT).

**El padrón dice que hay más de una clave para mi DNI.** Es normal (por ejemplo, un CUIL y un CUIT). Usá la que corresponda como `documento` (11 números).

**El padrón no conoce a nadie en homologación.** El padrón de pruebas solo tiene personas de prueba. Para probar usá los ejemplos del manual de ARCA
(`11709676`, `27015942210`); con datos reales solo anda en producción.

**El nombre de mi CSV no coincide con el padrón.** El padrón trae el nombre legal (a veces con segundos nombres o abreviado). Alcanza con que estén todas
las palabras que escribiste. Si igual la fila está bien, aceptala con `aceptar=<ID>`.

**«un intento anterior … quedó sin confirmar».** ARCA no contestó en un intento y no se pudo saber si emitió. Mirá `ultimo_comprobante` (o `ver_comprobante`) y, según
eso, corregí o repetí a mano; el conector no sigue hasta que lo aclares.

**¿Dónde veo lo que emití?** En `emitidas.jsonl`, en los PDF, con `/c arca ver_comprobante numero=N`, y en ARCA (**Mis Comprobantes**).

**¿Cuándo vence el certificado?** `verificar` muestra la vigencia. Antes de que venza, creá una cuenta nueva con otro nombre (clave y pedido nuevos), tramitá su
certificado y las mismas relaciones, y pasá a usarla.

**Quiero cambiar el formato del PDF.** Es un diseño simple, en `render_pdf` de `arca_mcp.py`. El «idéntico al de ARCA» queda pendiente.

**Correr los tests.** `uv run pytest tests/connectors/test_arca.py` (no llaman a ARCA: un único punto de red, `_post`, está reemplazado).
