# Conector de ARCA (ex AFIP)

Consultar y emitir comprobantes electrónicos desde Epsilon, sin gastar IA: puntos de venta, último comprobante,
ver uno, verificar la configuración y **emitir Factura C** (monotributo). Usa los web services oficiales de ARCA
(WSAA + WSFEv1).

> Todo arranca en **homologación** (el entorno de pruebas de ARCA, donde no pasa nada real). En **producción** la
> emisión está apagada hasta que la habilites a propósito en cada cuenta (ver «Emitir»).

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
| `actividades` | Las actividades del emisor que ARCA acepta asociar a un comprobante (el código va en `actividad`). En homologación suele no haber ninguna |
| `condiciones_iva_receptor` | Las condiciones frente al IVA del receptor que ARCA acepta en una Factura C |
| `emitir_factura` | Emite una Factura C. Ver la sección siguiente |

Todas aceptan `cuenta=<nombre>` para cambiar de cuenta. Para que la IA **no** las use, poné `deny` en `/permisos`.

## Emitir una Factura C

Siempre en **dos pasos**, para no emitir nada por error:

```
/c arca emitir_factura importe=1500.50 concepto=productos_y_servicios documento=12345678 descripcion="Repuestos y reparaciones"
```

Por defecto solo se exige lo que pide ARCA (más el concepto). Si faltan el documento o la descripción, la simulación
avisa con ⚠. Para que **no se pueda emitir sin ellos**, como en el facturador web, la cuenta lo exige en `cuentas.json`:
`"obligatorios": ["documento", "descripcion", "localidad"]` (los tres nombres posibles; ponés los que quieras). El pago es siempre contado.

1. **Simula**: muestra la cuenta, el ambiente, el número que le tocaría, el receptor y el importe, y **no emite nada**.
   Termina con un código: `confirmar=7f3a9c`.
2. **Emite** si repetís el mismo pedido agregando ese código:
   `/c arca emitir_factura importe=1500.50 concepto=productos_y_servicios documento=12345678 descripcion="Repuestos y reparaciones" confirmar=7f3a9c`

El código depende de todos los datos **y del número que le toca**, así que no sirve dos veces ni si cambiás algo:
repetir un comando ya emitido no emite otra factura.

| Argumento | Qué es |
|---|---|
| `importe` | Total, hasta 2 decimales (no redondea por su cuenta). En Factura C no hay IVA |
| `concepto` | `productos`, `servicios` o `productos_y_servicios` (o guardalo en la cuenta) |
| `documento`, `tipo_documento` | DNI, CUIL o CUIT del receptor, con puntos o guiones si querés. Si la cuenta exige `documento`, sin él hace falta `sin_identificar=true` para emitir a un consumidor final sin identificar. El tipo se deduce (hasta 8 números: DNI; 11 que empiezan con 20/23/24/27: CUIL; otros: CUIT) y se puede forzar con `tipo_documento=dni\|cuil\|cuit`. Se revisa el dígito verificador |
| `condicion_receptor` | `consumidor_final` (por defecto), `monotributo`, `responsable_inscripto`, `exento`, `no_categorizado`, `monotributo_social`, o el número (ver `condiciones_iva_receptor`) |
| `fecha` | AAAA-MM-DD, por defecto hoy. ARCA acepta hasta 5 días antes o después con productos y 10 con servicios |
| `servicio_desde`, `servicio_hasta`, `vencimiento_pago` | Para servicios, por defecto la fecha del comprobante |
| `descripcion` | Opcional salvo que la cuenta la exija (o `descripcion` en la cuenta). Queda en el registro local: **ARCA no guarda la descripción** (existirá en el PDF) |
| `localidad` | Opcional salvo que la cuenta la exija (o `localidad` en la cuenta), p. ej. «CABA»: queda en el registro local, para el PDF |
| `punto_venta` | Por defecto el de la cuenta |

- **Si ARCA no responde después de recibir el pedido**, el conector **no lo repite**: mira el último número autorizado y
  te dice si se emitió o no. Si tampoco puede mirar, te pide que **no repitas** y que revises con `ultimo_comprobante`.
- Cada intento queda anotado en `emitidas.jsonl` (junto a `cuentas.json`; solo se agrega, permisos 600).
- **Producción** exige `"emision": true` en la cuenta de `cuentas.json` (solo el valor `true`, a propósito). Sin eso,
  `cuentas` la muestra como «solo lectura» y la emisión se niega. Una factura real solo se corrige con una nota de crédito.

## `cuentas.json`

Solo datos, sin secretos (las claves son archivos aparte con permisos 600):

```json
{
  "negocio": {"cuit": "20123456789", "ambiente": "homologacion", "condicion": "monotributo", "punto_venta": 3}
}
```

- `concepto` (opcional): `productos`, `servicios` o `productos_y_servicios`, para no escribirlo en cada emisión.
- `actividad` (opcional): código de actividad que ARCA debe asociar al comprobante, por ejemplo `"479900"`. Solo se
  envía si lo ponés; ARCA exige que sea una de las actividades activas del emisor (se ven con `actividades`, y
  `verificar` lo comprueba). **En homologación el emisor de pruebas casi nunca las tiene: probá sin `actividad`.**
- `descripcion` (opcional): texto por defecto, por ejemplo `"Repuestos y reparaciones"`; si no está, hay que pasarla en cada emisión.
- `obligatorios` (opcional): datos que esta cuenta exige además de lo de ARCA: `"documento"`, `"descripcion"`, `"localidad"`.
- `localidad` (opcional): por ejemplo `"CABA"`, para el domicilio del comprobante cuando no se tiene el del cliente.
- `emision` (solo producción): `true` para permitir emitir de verdad.
- `cert` y `key` (opcionales): rutas a otros archivos; por defecto `<cuenta>.crt` y `<cuenta>.key` en la misma carpeta.
- `urls` (opcional): `{"wsaa": "https://…", "wsfe": "https://…"}`, por si ARCA cambia las direcciones. Siempre `https://`.
- El ticket que da ARCA (dura ~12 h) se guarda en `cache/` con permisos 600: ARCA no da otro mientras haya uno vigente.

## Si algo falla

- **«ya hay un ticket vigente»**: ARCA no da uno nuevo hasta que venza el anterior. Si borraste `cache/`, esperá.
- **«no confía en el certificado»**: el ambiente de la cuenta no coincide con el del certificado.
- **«No pude hablar con ARCA»**: revisá la conexión; si ARCA cambió de dominio, ajustá `urls`.
- **No hay puntos de venta**: falta crear uno de tipo «RECE para aplicativo y web services».
