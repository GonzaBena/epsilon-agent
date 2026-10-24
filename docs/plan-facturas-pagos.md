# Plan: conectores de Mercado Pago y ARCA (facturas)

Dos conectores MCP (ver `plan-conectores.md`) para uso de la PERSONA: se manejan con `/c` y con botones, sin
modelo. Que la IA no los use: poner `deny` en `/permisos` (sus herramientas arrancan en `ask`).

## Principios

- **Autocontenidos.** Cada conector es UNA carpeta en `conectores/<nombre>/` (fuera del paquete `epsilon`, así no
  suma nada a quien no lo usa): el script y su `README.md` (los tests, como todos, en `tests/connectors/`). Las dependencias se declaran dentro del
  script (PEP 723, bloque `# /// script`) y se instalan solas, en caché, con `uv run <script>` la primera vez que
  el conector arranca. Quien no lo agrega a `/conectores` no instala nada; Epsilon no suma dependencias.
  Comando a pegar en la página: `uv run conectores/mercadopago/mercadopago_mcp.py`.
- **Solo lectura primero.** Escribir (emitir facturas, crear links de cobro, reembolsos) viene después de probar la lectura.
- **Varias cuentas.** Todas las herramientas aceptan `cuenta=alias`; sin él usan la predeterminada.
- **Para cualquiera, no solo para mí.** Nada de «mi CUIT» ni «mi punto de venta» en el código: todo sale de la
  configuración de cada cuenta (condición fiscal, punto de venta, ambiente). Los mensajes dicen qué falta y cómo arreglarlo.
- **Sin red en los tests.** Un único punto de red por conector (`_http` / `_soap`) que los tests reemplazan.
- **Las claves nunca salen:** ni en errores ni en lo que devuelve una herramienta.

## Fase 1: Mercado Pago, solo lectura

El MCP oficial de Mercado Pago es para desarrolladores (docs, apps, webhooks, usuarios de prueba) y no lee pagos
ni saldo, por eso se hace uno propio.

- Variables de entorno: un token por cuenta, `MP_TOKEN_<ALIAS>` (p. ej. `MP_TOKEN_NEGOCIO`); `MP_DEFAULT` elige la
  predeterminada (si hay una sola, es esa).
- Herramientas: `cuentas`, `buscar_pagos` (desde, hasta, estado, monto), `ver_pago`, `saldo`.
- API: `https://api.mercadopago.com`, `Authorization: Bearer`. Confirmar rutas con un token de prueba.
- Botones sugeridos por el recurso `epsilon://buttons`: «Pagos de hoy», «Saldo».

## Fase 2: ARCA, solo lectura y en homologación

- Web services oficiales (WSAA + WSFEv1). Dependencia: solo `cryptography` (firma); el SOAP se arma a mano con la biblioteca estándar (sin `zeep`).
- `arca_mcp.py nueva <nombre> --cuit <CUIT>`: genera clave privada y CSR y anota la cuenta; guía del trámite en el README (WSASS en homologación, Administración
  de Certificados Digitales en producción, relación con `wsfe`, punto de venta tipo «RECE para aplicativo y web services»).
- `cuentas.json` en `ARCA_HOME` (por defecto `~/.config/epsilon-arca/`; sin secretos): alias → CUIT, rutas del certificado y la clave, punto de venta, ambiente, condición fiscal.
- Token del WSAA en caché (dura ~12 h), renovado solo.
- Herramientas: `cuentas`, `verificar` (revisa archivos, certificado, ingreso, servidores y puntos de venta), `puntos_de_venta`, `ultimo_comprobante`, `ver_comprobante`, `tipos_de_comprobante`.

## Fase 3: emitir facturas (después de probar 1 y 2; primero SOLO en homologación)

Referencia: `docs/afip/` (capturas de «Comprobantes en línea», el flujo que esto reemplaza; contienen datos
personales: no se suben al repositorio).

- `emitir_factura`: **simula por defecto**, emite solo con `confirmar=true`. Monotributo → Factura C (11, sin IVA);
  responsable inscripto → A/B con IVA. Una sola línea: `descripcion` + `importe` (el neto de Mercado Pago o del banco).
- **Qué viaja a ARCA** (WSFEv1): tipo y punto de venta, concepto (productos / servicios / ambos), importes, fecha,
  tipo y número de documento del receptor (DNI 96, CUIL 86, CUIT 80, sin identificar 99), condición del receptor
  frente al IVA (obligatoria desde la RG 5616; consumidor final = 5), y, si el manual lo confirma, la actividad
  (`Actividades`, p. ej. 479900). Con servicios: período y vencimiento del pago, por defecto la fecha de emisión.
  **No viajan**: descripción, domicilio, nombre del receptor ni forma de pago; solo existen en el PDF.
- Por cuenta, en `cuentas.json`: `actividad`, `descripcion`, `localidad` (p. ej. «CABA») y `emision`.
- **Datos obligatorios configurables**: por defecto solo lo que pide ARCA (y avisa con ⚠ si falta documento o descripción).
  Cada cuenta puede exigir más con `"obligatorios": ["documento", "descripcion", "localidad"]` (como el facturador web);
  quien no lo toca usa lo básico. `sin_identificar=true` confirma un consumidor final sin documento. El pago es siempre
  «Contado». Nombre y domicilio saldrán del padrón (fase 4) o de la localidad; sin PDF, cada emisión avisa que el comprobante
  para entregar no se generó.
- **Seguridad**: homologación emite siempre; **producción solo con `"emision": true`** en la cuenta. Sin reintentos
  automáticos: si la red falla tras enviar, primero consulta el último número autorizado y recién ahí dice qué pasó.
  Siempre pide el último y usa el siguiente. Cada emisión se anota en un registro local que solo se agrega.
- No hay «emitir de prueba» en producción: la primera factura real es real (se corrige solo con nota de crédito).

## Fase 4: PDF y padrón

- **PDF** con QR (opcional: `pdf=true` o fijado en la cuenta; el detalle de lo vendido solo queda ahí). Hace falta una
  librería de QR chica (`segno`, sin dependencias) declarada en el script.
- **Padrón** (nombre y domicilio del cliente, y elegir entre dos CUIL con el mismo DNI, como hace la web):
  - Servicio `ws_sr_padron_a13` (manual oficial v1.4): `getIdPersonaListByDocumento` devuelve las claves (CUIL/CUIT)
    de un DNI y `getPersona` da nombre y domicilio. Se habilita en ARCA creando una relación «Consulta a Padrón
    Alcance 13» para el certificado, y pide su propio ticket de WSAA (el nombre del servicio va en el pedido).
  - **Sin verificar**: qué datos devuelve a un monotributista y si tiene condiciones de uso; en homologación los padrones
    suelen traer datos de prueba. **Se decide con una prueba real y de solo lectura**: habilitar la relación junto con el
    certificado de producción y consultar el propio DNI/CUIL.
  - Si no se puede, no bloquea nada: la factura se emite igual con DNI/CUIL y el nombre/domicilio del PDF se escriben a mano.
- Después: nota de crédito, formulario en la página.

## Riesgos

- La clave privada de ARCA no debe llegar al modelo: el conector recibe la RUTA, el archivo va con permisos `600`.
- Nombres de clientes y descripciones de pagos son dato, no instrucciones.
- Emitir es irreversible (solo se corrige con nota de crédito): por eso la Fase 3 va al final.
