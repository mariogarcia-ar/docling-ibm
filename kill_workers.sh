#!/bin/bash

# Script para eliminar workers huérfanos de ocr_documents.py

echo "Buscando procesos de ocr_documents.py..."

# Buscar proceso principal
MAIN_PIDS=$(ps aux | grep "ocr_documents.py" | grep -v grep | awk '{print $2}')

# Buscar workers de multiprocessing
WORKER_PIDS=$(ps aux | grep "multiprocessing.spawn" | grep -v grep | awk '{print $2}')

# Buscar resource tracker
TRACKER_PIDS=$(ps aux | grep "multiprocessing.resource_tracker" | grep -v grep | awk '{print $2}')

# Combinar todos los PIDs
ALL_PIDS="$MAIN_PIDS $WORKER_PIDS $TRACKER_PIDS"

if [ -z "$ALL_PIDS" ]; then
    echo "✓ No se encontraron procesos activos"
    exit 0
fi

echo "Procesos encontrados:"
echo "  Principales: $MAIN_PIDS"
echo "  Workers: $WORKER_PIDS"
echo "  Trackers: $TRACKER_PIDS"
echo ""
echo "Eliminando procesos..."

# Eliminar todos los procesos
for pid in $ALL_PIDS; do
    if [ ! -z "$pid" ]; then
        kill -9 $pid 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "  ✓ Proceso $pid eliminado"
        else
            echo "  ✗ No se pudo eliminar proceso $pid (puede que ya no exista)"
        fi
    fi
done

echo ""
echo "✓ Limpieza completada"

# Verificar que no queden procesos
REMAINING=$(ps aux | grep -E "ocr_documents.py|multiprocessing" | grep -v grep | grep -v "kill_workers.sh")
if [ -z "$REMAINING" ]; then
    echo "✓ Todos los procesos fueron eliminados exitosamente"
else
    echo "⚠ Algunos procesos aún están activos:"
    echo "$REMAINING"
fi
