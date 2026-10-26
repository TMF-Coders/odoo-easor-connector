# Seguridad y tratamiento de información

Las claves permanecen en el entorno/gestor de secretos. La base solo guarda una
referencia con prefijo `TMF_EASOR_KEY_`. El origen requiere HTTPS, host autorizado
por operación, sin usuario/password/rutas/query ni redirects. No se registran
cabeceras, cuerpos de error remotos ni URLs firmadas. Las respuestas se limitan a
5 MiB de JSON y 10 MiB de PDF, con timeout de conexión/lectura.

Permisos: preparar y consultar, configurar, autorizar emisión. Los permisos
financieros normales de Odoo siguen siendo necesarios. Las reglas globales aíslan
conexiones, equivalencias, operaciones e historial por compañía. El trabajador
vuelve a comprobar usuario activo, compañía y permisos antes de cada escritura.
Una clave pública no abre las rutas privadas de Easor.

`state`, IDs remotos, instantáneas, huellas y eventos solo cambian mediante métodos
internos. Ni un contexto RPC con un booleano ni valores por defecto deben saltarse
las guardas. Una equivalencia cruzada entre compañías se rechaza por ORM/SQL.
Los documentos enviados no se borran y sus líneas no se reconcilian localmente.

El PDF se adjunta privadamente a `account.move`; se bloquean cambios de contenido,
asociación y publicación. El historial es inmutable mediante la API de aplicación.
Esto **no es almacenamiento WORM** ni protege frente a un administrador de la base
de datos. Backups, cifrado, conservación y permisos de infraestructura requieren
política de despliegue; el conector no impone un plazo legal inventado.

Los snapshot/PDF contienen información financiera y personal: restrinja cuentas,
exports de base, copias y artefactos de soporte. Los tests solo usan identidades
sintéticas y dominios `.invalid`. Este repositorio es público: no suba datos reales,
claves, respuestas de producción o implementación privada del proveedor.

Revocación: detener la conexión o los interruptores de escritura no deshace una
emisión ya aceptada. Incidentes y restauraciones requieren reconciliar con Easor
antes de activar el trabajador. No se generan pagos ni transferencias bancarias.
