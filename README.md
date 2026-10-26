# Odoo–Easor connector · TMFCoders

Conector original para **Odoo 19 Community y Easor España**. Odoo prepara la
operación; Easor emite, numera y conserva la contabilidad oficial. La copia local
no origina otra emisión ni se vuelve a exportar como asiento.

**Primera entrega, no habilitada para producción por defecto.** Incluye un flujo
real de integración, no respuestas simuladas en el producto. Las pruebas sustituyen
la API por dobles explícitos. La compatibilidad con un despliegue concreto de Easor
y su aceptación funcional deben verificarse en una empresa de pruebas autorizada.

## Incluido

| Componente | Alcance implementado |
| --- | --- |
| `tmf_easor_base` | Conexión por compañía, credenciales por referencia a entorno, allow-list de hosts, equivalencias cliente/producto y comprobación de identidad. |
| `tmf_easor_invoicing` | Instantánea inmutable, cola persistente, creación de borrador remoto, comparación de líneas/importes, aprobación de emisión, recuperación del documento y PDF, trazabilidad y copia operativa local explícita. |
| Seguridad | Permiso de emisión separado, reglas multicompañía, metadatos protegidos también por RPC, bloqueo de emisión local y de reintentos de escritura inciertos. |
| Verificación | Tests sin Odoo, tests ORM en Odoo 19, CI y guion de UAT. |

El flujo de usuario es:

**Factura Odoo en borrador → Preparar en Easor → borrador remoto verificado →
Autorizar emisión → emisión y PDF verificados → Contabilizar copia operativa.**

La última acción es deliberadamente explícita: no oculta fallos de configuración
contable local. No realiza otra petición de emisión. La factura nativa permanece
borrador hasta esa acción; el panel Easor informa por separado del estado remoto.
Si la copia local falla, se corrige su configuración sin volver a emitir.

## Alcance inicial y límites

Facturas ordinarias de venta domésticas, EUR, régimen general, IVA ordinario
**0/4/10/21 % explícitamente revisado**, 1–50 líneas, precios positivos con precisión
de céntimo, cantidades >= 0,01 y un solo vencimiento. No hay conversión implícita de
unidades ni descuentos. El texto y unidad de cada producto deben coincidir con el
catálogo remoto revisado. Las referencias y condiciones se envían como notas en
texto plano, hasta 2.000 caracteres. Idiomas: es, ca, en, gl, it.

**No incluido todavía:** altas automáticas de maestros, rectificativas, tickets/POS,
compras, retenciones/recargos, N1/N2/suplidos, divisas, pagos parciales, sincronización
bancaria, asientos adicionales ni informes oficiales. No existe una integración
contable completa por tener una copia local de ventas. No se ejecutan exporters.

El contrato revisado de borradores ordinarios **no incluye transferencia bancaria**
como forma de pago. Se rechaza ese valor; nunca se sustituye por otro. La opción
«Sin especificar» solo se utiliza si el usuario la elige expresamente.

Esta entrega bloquea coexistencia con `account_edi` y módulos `l10n_es_edi*`
instalados. Es un límite conservador del piloto, no una incompatibilidad universal
de Odoo y Easor. Su soporte exige una revisión independiente de hooks fiscales.

## Instalación y uso

Lea [Instalación](docs/INSTALL.md) y [Uso y recuperación](docs/OPERATIONS.md).
El servidor necesita `requests` y ambos addons en su `addons_path`:

```sh
odoo -d easor_demo --stop-after-init -i tmf_easor_invoicing --without-demo=all
```

Las escrituras necesitan **dos interruptores**: `TMF_EASOR_ALLOW_WRITES=1` en el
servidor y «Permitir escrituras remotas» en la conexión. Ambos vienen desactivados.
No ejecute este comando sobre una base operativa sin revisar sus módulos y backups.

## Pruebas

```sh
python -m pip install 'requests>=2.32.3,<3'
python -m unittest discover -s tests -v
bash scripts/test_odoo.sh   # Docker, PostgreSQL 16 y Odoo 19; base desechable
```

Los tests puros no acreditan instalación Odoo. Los tests ORM no acreditan emisión
real en Easor. [UAT](docs/UAT.md) define las comprobaciones externas pendientes.
Los resultados y la imagen exacta de Odoo se guardan como artefactos de CI.

## Documentación técnica

[Arquitectura y transacciones](docs/ARCHITECTURE.md) ·
[Contrato y cobertura](docs/API-CONTRACT.md) ·
[Seguridad](docs/SECURITY.md) · [Plan de evolución](docs/ROADMAP.md).

El conector usa la API pública española; no el portal finlandés, contraseñas de la
web, MCP ni llamadas directas a `/v2`. No redistribuye implementación privada de
Easor. Todos los datos de prueba son sintéticos. Licencia: LGPL-3.0-only.
