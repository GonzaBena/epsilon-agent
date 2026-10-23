# Conector de ARCA (ex AFIP), solo lectura

Consultar tus comprobantes electrónicos desde Epsilon, sin gastar IA: puntos de venta, último comprobante, ver uno
y verificar que la configuración esté bien. Usa los web services oficiales de ARCA (WSAA + WSFEv1).

> Por ahora **no emite** facturas: primero se prueba la lectura. Todo arranca en **homologación** (el entorno de
> pruebas de ARCA, donde no pasa nada real).

## 1. Crear la cuenta (clave + pedido de certificado)

```
uv run /ruta/a/epsilon/conectores/arca/arca_mcp.py nueva negocio --cuit 20123456789 --nombre "Mi Negocio"
```

- `negocio` es el nombre de la cuenta (minúsculas, 2 a 20 caracteres). El CUIT son 11 números, sin guiones.
- Crea en `~/.config/epsilon-arca/` (o donde diga `ARCA_HOME`): la clave privada `negocio.key`, el pedido
  `negocio.csr` y `cuentas.json`. **La clave no se comparte ni se sube a ningún lado.** Nunca pisa una existente.
- Opciones: `--ambiente homologacion|produccion` (por defecto homologación) y
  `--condicion monotributo|responsable_inscripto|exento` (monotributo usa Factura C por defecto).
- Homologación y producción usan **certificados distintos**: si querés los dos para el mismo CUIT, creá dos cuentas
  (por ejemplo `negocio_test` y `negocio`).

## 2. Conseguir el certificado en ARCA (una sola vez por cuenta)

Entrás con tu clave fiscal (de la persona física).

**Homologación (pruebas):**
1. Servicio **WSASS** (se habilita desde «Administrador de Relaciones de Clave Fiscal»).
2. Crear certificado: nombre cualquiera y pegá el contenido del `.csr`. Descargá el `.crt`.
3. Guardalo como `~/.config/epsilon-arca/negocio.crt`.
4. En WSASS, «Crear autorización a servicio»: elegí `wsfe` (Facturación Electrónica) para ese certificado.

**Producción:**
1. Servicio **Administración de Certificados Digitales**: agregar alias y subir el `.csr`; descargar el `.crt`.
2. En «Administrador de Relaciones de Clave Fiscal»: nueva relación → servicio `wsfe`, y como representante el
   alias del certificado.

**Punto de venta:** el de «Comprobantes en línea» (el que usás en la web) **no sirve** para web services. Creá otro
en «Administración de puntos de venta y domicilios» con el sistema **«RECE para aplicativo y web services»**.
En homologación el punto de venta se crea desde la propia herramienta de pruebas de ARCA.

## 3. Agregarlo a Epsilon

Página **Conectores** → agregar:
- Nombre: `arca`
- Comando (ruta absoluta; la primera vez baja sus dependencias solo):
  `uv run /ruta/a/epsilon/conectores/arca/arca_mcp.py`
- Variables opcionales: `ARCA_HOME` (otra carpeta), `ARCA_DEFAULT` (cuenta predeterminada si hay varias).

Después: `/c arca verificar`. Revisa en orden los archivos, el certificado (vigencia, CUIT, que la clave le
corresponda), el ingreso a ARCA, el estado de los servidores y los puntos de venta, y marca con ✗ lo que falla
y por qué.

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `cuentas` | Cuentas configuradas: CUIT, ambiente y si están el certificado y la clave |
| `verificar` | Revisa paso a paso que la cuenta esté lista |
| `puntos_de_venta` | Puntos de venta habilitados para web services |
| `tipos_de_comprobante` | Los tipos que ARCA permite (11 = Factura C, 13 = Nota de crédito C…) |
| `ultimo_comprobante` | Último número autorizado. Sin `tipo` usa el de la cuenta (monotributo: 11); sin `punto_venta`, el guardado en `cuentas.json` |
| `ver_comprobante` | Fecha, importes, receptor, concepto y CAE de uno ya autorizado: `/c arca ver_comprobante numero=45` |

Todas aceptan `cuenta=<nombre>` para cambiar de cuenta. Para que la IA **no** las use, poné `deny` en `/permisos`.

## `cuentas.json`

Solo datos, sin secretos (las claves son archivos aparte con permisos 600):

```json
{
  "negocio": {"cuit": "20123456789", "ambiente": "homologacion", "condicion": "monotributo", "punto_venta": 3}
}
```

- `cert` y `key` (opcionales): rutas a otros archivos; por defecto `<cuenta>.crt` y `<cuenta>.key` en la misma carpeta.
- `urls` (opcional): `{"wsaa": "https://…", "wsfe": "https://…"}`, por si ARCA cambia las direcciones. Siempre `https://`.
- El ticket que da ARCA (dura ~12 h) se guarda en `cache/` con permisos 600: ARCA no da otro mientras haya uno vigente.

## Si algo falla

- **«ya hay un ticket vigente»**: ARCA no da uno nuevo hasta que venza el anterior. Si borraste `cache/`, esperá.
- **«no confía en el certificado»**: el ambiente de la cuenta no coincide con el del certificado.
- **«No pude hablar con ARCA»**: revisá la conexión; si ARCA cambió de dominio, ajustá `urls`.
- **No hay puntos de venta**: falta crear uno de tipo «RECE para aplicativo y web services».
