#!/bin/bash
set -e

# ==============================================================================
# Script de Backup Automático para Odoo Eruvia
# ==============================================================================

BACKUP_DIR="/opt/eruvia-odoo/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DB_NAME="eruvia" # Cambia por el nombre de tu base de datos en Odoo
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

echo ">>> Iniciando backup de PostgreSQL ($DB_NAME)..."
docker exec eruvia_postgres pg_dump -U odoo -d "$DB_NAME" -F c -b -v -f "/tmp/backup_${TIMESTAMP}.dump"
docker cp eruvia_postgres:/tmp/backup_${TIMESTAMP}.dump "$BACKUP_DIR/db_${DB_NAME}_${TIMESTAMP}.dump"
docker exec eruvia_postgres rm "/tmp/backup_${TIMESTAMP}.dump"

echo ">>> Backup de Base de Datos completado: $BACKUP_DIR/db_${DB_NAME}_${TIMESTAMP}.dump"

# Limpieza de backups antiguos (+ de 7 días)
find "$BACKUP_DIR" -type f -name "*.dump" -mtime +$RETENTION_DAYS -delete
echo ">>> Rotación de backups completada (retención: $RETENTION_DAYS días)."
