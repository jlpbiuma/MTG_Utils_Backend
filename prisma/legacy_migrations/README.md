# Historial anterior a Prisma Migrate

Estos tres SQL incrementales se conservan como referencia, fuera del directorio
activo de Prisma Migrate. Las bases local y de producción no tenían registros de
migraciones: se habían gestionado mediante `db push`. Los cambios de estos SQL ya
están representados en `../migrations/0_init`.

No ejecutar estos archivos sobre una base actual ni marcarlos como aplicados.
La adopción del nuevo historial requiere verificar el esquema existente contra
la instantánea de `0_init` mediante `../baseline.sh`.
