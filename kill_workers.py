#!/usr/bin/env python3
"""
Script para eliminar workers huérfanos de ocr_documents.py
Uso: python kill_workers.py
"""

import os
import sys
import signal
import subprocess

def get_process_list():
    """Obtiene la lista de procesos activos"""
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        return result.stdout.split('\n')
    except Exception as e:
        print(f"Error al obtener procesos: {e}")
        return []

def find_pids():
    """Encuentra los PIDs de procesos relacionados con ocr_documents.py"""
    processes = get_process_list()
    
    main_pids = []
    worker_pids = []
    tracker_pids = []
    
    for line in processes:
        # Ignorar línea vacía y grep
        if not line or 'grep' in line or 'kill_workers' in line:
            continue
        
        # Buscar proceso principal
        if 'ocr_documents.py' in line:
            parts = line.split()
            if len(parts) > 1:
                main_pids.append(parts[1])
        
        # Buscar workers
        elif 'multiprocessing.spawn' in line:
            parts = line.split()
            if len(parts) > 1:
                worker_pids.append(parts[1])
        
        # Buscar resource tracker
        elif 'multiprocessing.resource_tracker' in line:
            parts = line.split()
            if len(parts) > 1:
                tracker_pids.append(parts[1])
    
    return main_pids, worker_pids, tracker_pids

def kill_processes(pids, label):
    """Elimina procesos por PID"""
    if not pids:
        return 0
    
    killed = 0
    for pid in pids:
        try:
            pid_int = int(pid)
            os.kill(pid_int, signal.SIGKILL)
            print(f"  ✓ {label} {pid} eliminado")
            killed += 1
        except ProcessLookupError:
            print(f"  ✗ {label} {pid} no existe")
        except PermissionError:
            print(f"  ✗ {label} {pid} - permiso denegado")
        except ValueError:
            print(f"  ✗ PID inválido: {pid}")
        except Exception as e:
            print(f"  ✗ Error eliminando {pid}: {e}")
    
    return killed

def main():
    print("Buscando procesos de ocr_documents.py...\n")
    
    main_pids, worker_pids, tracker_pids = find_pids()
    
    total_found = len(main_pids) + len(worker_pids) + len(tracker_pids)
    
    if total_found == 0:
        print("✓ No se encontraron procesos activos")
        return 0
    
    print("Procesos encontrados:")
    if main_pids:
        print(f"  Principales: {', '.join(main_pids)}")
    if worker_pids:
        print(f"  Workers: {', '.join(worker_pids)}")
    if tracker_pids:
        print(f"  Trackers: {', '.join(tracker_pids)}")
    
    print("\nEliminando procesos...")
    
    killed = 0
    killed += kill_processes(main_pids, "Principal")
    killed += kill_processes(worker_pids, "Worker")
    killed += kill_processes(tracker_pids, "Tracker")
    
    print(f"\n✓ Limpieza completada: {killed}/{total_found} procesos eliminados")
    
    # Verificar que no queden procesos
    import time
    time.sleep(0.5)
    
    remaining_main, remaining_workers, remaining_trackers = find_pids()
    remaining_total = len(remaining_main) + len(remaining_workers) + len(remaining_trackers)
    
    if remaining_total == 0:
        print("✓ Todos los procesos fueron eliminados exitosamente")
        return 0
    else:
        print(f"⚠ Aún quedan {remaining_total} procesos activos")
        return 1

if __name__ == "__main__":
    sys.exit(main())
