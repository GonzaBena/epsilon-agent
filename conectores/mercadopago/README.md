# Conector de Mercado Pago (solo lectura)

Ver tus pagos recibidos, el detalle de un pago y el saldo, desde Epsilon y sin gastar IA.

## Instalar

1. Conseguí el **access token** de producción: https://www.mercadopago.com.ar/developers → Tus integraciones →
   tu aplicación → Credenciales de producción. (Tratalo como una contraseña.)
2. En Epsilon, página **Conectores** → agregar:
   - Nombre: `mp`
   - Comando (ruta absoluta; la primera vez baja sus dependencias solo):
     `uv run /ruta/a/epsilon/conectores/mercadopago/mercadopago_mcp.py`
   - Variables: `MP_TOKEN_NEGOCIO` = tu token (el nombre después de `MP_TOKEN_` es el de la cuenta). Para exportar:
     `MP_EXPORT_DIR` = la carpeta donde guardar los CSV (la de archivos de Epsilon, `<DATA_DIR>/files`, para verlos en la Biblioteca).
3. Confirmá, prendelo y probá: `/c mp saldo` o `/c mp buscar_pagos`.

## Varias cuentas

Una variable por cuenta: `MP_TOKEN_NEGOCIO`, `MP_TOKEN_PERSONAL`… `MP_DEFAULT=personal` elige la que se usa sin
decir nada (con una sola cuenta no hace falta). Para otra: `/c mp saldo cuenta=negocio`.

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `cuentas` | Lista las cuentas cargadas (nunca el token) |
| `buscar_pagos` | Pagos recibidos. `desde`/`hasta` (AAAA-MM-DD; sin fechas, hoy), `estado`, `limite` (máx. 50) |
| `exportar_pagos` | Guarda en un CSV TODOS los pagos del período (`desde`/`hasta`, `estado`; hasta 5000). Nunca pisa un archivo |
| `ver_pago` | Detalle de un pago por número: estado, monto, comisión, neto, medio, fechas |
| `saldo` | Saldo disponible y no disponible |

Las fechas se interpretan en la hora de tu máquina. Para que la IA **no** las use, poné `deny` en `/permisos`.
