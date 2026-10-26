# Contrato español y cobertura

Referencia revisada: código español al 26-10-2026, revisión
`9444ae8cef2f1a75e740716c08e9dbf0919daee4`. Esto identifica la evidencia de diseño;
no asegura que un entorno remoto despliegue esa revisión. No se incluye código
privado del proveedor ni sus fixtures.

El origen es configurable y debe confirmarlo Easor España. Se añade `/api/v1` y
se autentica con `x-easor-api-key`. No hay login por email ni cookies de navegador.

| Método/ruta utilizada | Uso |
| --- | --- |
| `GET /api/v1/business` | Identidad y acceso a la empresa. |
| `GET /api/v1/business/{id}/invoice/options` | Disponibilidad de régimen general. |
| `GET /api/v1/business/{id}/merchant` | Identidad fiscal del cliente vinculado. |
| `GET /api/v1/business/{id}/product` | Catálogo de venta, descripciones, tipo y unidad. |
| `POST /api/v1/business/{id}/invoice/draft` | Crear un borrador, no emitirlo. |
| `POST /api/v1/business/{id}/income/draft/bulk/issue` | Solicitar emisión de un único borrador. |
| `GET /api/v1/business/{id}/income` | Buscar por ID dentro de listados paginados del periodo. |
| `GET /api/v1/business/{id}/income/{incomeId}/pdf` | PDF binario original. |

Listados: `data` y `pagination`, con `page`, `total`, `end`. Se recorren hasta 100
páginas de 50 registros; se bloquea una respuesta incoherente o más grande. Es un
límite conservador visible, no soporte ilimitado. El contrato no ofrece aquí un
cursor de modificaciones; no se utiliza una fecha de factura como fecha de cambio.

Borrador: `merchantId`, `serialNumberId`, `economicActivityId`, `date`, `dueDate`,
`paymentMethod`, `vatRegime`, `paid`, `language`, `notes`, `lines`. Por línea:
`productId`, `quantity`, `price`, `vat`, `retention`, `equivalenceSurcharge`.
Ejemplo de unidades: 100 € = 10000; 21 % = 2100. Identificadores de referencias:
10 caracteres alfanuméricos. La serie se selecciona por ID, no asignando un número.

El conector exige una respuesta de creación `{data: {id}}`. Tras emisión, exige
`isDraft=false`, `issued=true`, número no vacío y contenido coincidente. No deriva
aceptación tributaria de esos campos ni del HTTP 2xx. Un error 409 puede envolver
un resultado parcial: nunca provoca reenvío automático.

## Disponible en el proveedor pero pendiente en este conector

Altas de clientes/productos/series, recibos y rectificativas tienen contratos
propios. No se reutiliza el payload ordinario para simularlos. El contrato de
rectificativas presenta diferencias de moneda/categoría y precisa su propia suite.

## Capacidades que requieren ampliación o verificación con Easor

Idempotencia con referencia externa, consulta directa por ID y estado fiscal
completo; edición de borradores/maestros; facturas de proveedor estructuradas con
adjunto; cobros/pagos/conciliaciones; plan contable, diario, cierres e informes.
`accounting-export` y `book-entry/export` solicitan exportaciones por correo y no
sustituyen esos contratos. No se llama directamente a la API privada para cubrir
una ausencia de la pública.

## Cambios de contrato

Una respuesta desconocida bloquea el flujo; no activa una heurística de reparación.
Antes de aceptar otro contrato, actualice las pruebas, documente diferencias y
repita UAT en sandbox. No publique capturas o payloads con datos reales en este repo.
