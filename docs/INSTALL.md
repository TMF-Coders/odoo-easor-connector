# Instalación Odoo 19

## Requisitos

Odoo 19 Community autogestionado, PostgreSQL compatible con su despliegue y Python
con `requests`. No se requiere un servidor MCP, un modelo de IA, Redis ni otro ERP.
Se usan los modelos nativos `account.move`, `account.move.line` y `account.journal`.

Copie este repositorio a un directorio de addons; añádalo a `addons_path`, reinicie
Odoo, actualice la lista de aplicaciones e instale `tmf_easor_invoicing`. También
puede usar el comando de instalación del README en una base nueva.

## Variables de entorno

```sh
# Son ejemplos de estructura, NO una dirección de Easor ni credenciales válidas.
export TMF_EASOR_ALLOWED_HOSTS='api.example.invalid'
export TMF_EASOR_KEY_EMPRESA_A='<inyectada desde el gestor de secretos>'
export TMF_EASOR_ALLOW_WRITES=0
```

Obtenga de Easor España el origen HTTPS público realmente desplegado y una API key
con permisos apropiados. Sustituya el host reservado anterior por ese host. No
use el portal de Finlandia ni una URL de `nomo-export-service`.

El formulario guarda el nombre `TMF_EASOR_KEY_EMPRESA_A`, no su valor. Use una clave
por compañía o identidad de integración con privilegios mínimos; cada conexión
vincula un `businessId` con un NIF de Odoo. La allow-list se controla en el servidor;
ningún usuario puede hacer que la clave se envíe a una URL arbitraria desde la UI.
Los redirects se rechazan y no se heredan proxies del entorno.

No copie secretos al repositorio, a una URL, a campos de notas o a capturas de UAT.
Para rotar una clave manteniendo su referencia, cambie el secreto y reinicie los
procesos que recibieron el entorno anterior.

## Configuración funcional, por compañía

1. Asigne los grupos Easor y los permisos de facturación de Odoo. «Preparación y
   consulta» no concede emisión. «Configuración» administra conexiones y
   equivalencias. «Autorizar emisión Easor» es una concesión explícita separada.
2. En Easor > Conexiones, indique compañía, origen, `businessId`, variable de la
   clave, ID de serie ordinaria y actividad económica. Pruebe el acceso: esta
   acción solo lee y comprueba el NIF.
3. Cree un **diario de ventas nuevo y dedicado** y asígnele la conexión Easor.
   No reutilice un diario que ya tenga facturas contabilizadas.
4. Vincule los clientes y productos existentes de ambos sistemas en Equivalencias.
   En esta entrega no se crean maestros automáticamente. Revise identidad fiscal,
   descripción exacta, unidad y uso para ventas. El producto remoto no debe llevar
   stock: la operación física pertenece a Odoo.
5. Marque como «IVA ordinario revisado para Easor» únicamente los impuestos que
   efectivamente tengan esa naturaleza. Un impuesto a tipo cero no identifica por
   sí mismo una exención, suplido o no sujeción.
6. Antes del piloto, autorice escrituras en el servidor y en esa conexión. Para
   revocar todas las escrituras, ponga `TMF_EASOR_ALLOW_WRITES=0` y reinicie. Para
   pausar una compañía, desactive su conexión; los trabajos no se eliminan.

La identidad, serie y actividad de una conexión con historial no se reescriben.
Cambios de año/serie, sustitución de backend y migración de conexiones necesitan
una migración revisada; no se resuelven alterando IDs de operaciones existentes.

## Entorno local aislado

`local/compose.yml` expone Odoo solo en `127.0.0.1:8069`. Cree un `.env` a partir de
`.env.example` con una contraseña de base de datos aleatoria. Cree
`local/odoo.conf` a partir de `local/odoo.conf.example` y sustituya la contraseña
maestra. Son archivos ignorados por Git. Ejecute desde la raíz:

```sh
docker compose --env-file .env -f local/compose.yml up -d db
docker compose --env-file .env -f local/compose.yml run --rm odoo \
  odoo --stop-after-init -d easor_demo -i tmf_easor_invoicing --without-demo=all
docker compose --env-file .env -f local/compose.yml up -d odoo
```

La cuenta inicial de la base local debe cambiar su contraseña antes de conectar
servicios. El compose no configura SMTP ni contiene datos reales. No es una receta
de despliegue en producción: TLS de entrada, backups, secreto maestro, observabilidad
y endurecimiento de Odoo corresponden a la plataforma de despliegue.
