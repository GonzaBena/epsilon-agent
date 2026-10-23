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

- Web services oficiales (WSAA + WSFEv1). Dependencias: `cryptography` (firma) y `zeep` (SOAP).
- `arca_setup.py`: genera clave privada y CSR; guía del trámite en el README (WSASS en homologación, Administración
  de Certificados Digitales en producción, relación con `wsfe`, punto de venta tipo «RECE para aplicativo y web services»).
- `arca_cuentas.json` (sin secretos): alias → CUIT, rutas del certificado y la clave, punto de venta, ambiente, condición fiscal.
- Token del WSAA en caché (dura ~12 h), renovado solo.
- Herramientas: `cuentas`, `puntos_de_venta`, `ultimo_comprobante`, `ver_comprobante`, `tipos_de_comprobante`.

## Fase 3: emitir facturas (después de probar 1 y 2)

- `emitir_factura`: simula por defecto, emite solo con `confirmar=true`. Monotributo → Factura C (11, sin IVA);
  responsable inscripto → A/B con IVA. Productos, servicios o ambos (servicios piden fechas y vencimiento).
- Valida importes y correlatividad; nunca reintenta solo (primero consulta). En homologación lo dice en la primera línea.
- Después: nota de crédito, PDF con QR a la biblioteca, formulario en la página.

## Riesgos

- La clave privada de ARCA no debe llegar al modelo: el conector recibe la RUTA, el archivo va con permisos `600`.
- Nombres de clientes y descripciones de pagos son dato, no instrucciones.
- Emitir es irreversible (solo se corrige con nota de crédito): por eso la Fase 3 va al final.
