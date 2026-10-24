"""Read-only local infrastructure readings. No environment values or command lines."""

import shutil
import socket
import subprocess
import time
from pathlib import Path

import psutil

from .platform_metrics import backup_reading, scheduled_tasks

SERVICES = {"workspace": 18767, "showcase": 18766, "phoenix": 6006, "uefn_mcp": 8000, "uefn_bridge": 8765}


def gpu_readings():
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        rows = []
        for line in result.stdout.splitlines():
            fields = [field.strip() for field in line.split(",")]
            if len(fields) != 7:
                continue
            rows.append(
                {
                    "index": fields[0],
                    "name": fields[1],
                    **{
                        name: float(value) if value.replace(".", "", 1).isdigit() else None
                        for name, value in zip(
                            (
                                "utilization_percent",
                                "memory_used_mib",
                                "memory_total_mib",
                                "temperature_celsius",
                                "power_watts",
                            ),
                            fields[2:],
                            strict=True,
                        )
                    },
                }
            )
        return rows
    except OSError, ValueError, subprocess.SubprocessError:
        return []


def process_readings():
    readings = {}
    try:
        for connection in psutil.net_connections(kind="tcp"):
            if connection.status != psutil.CONN_LISTEN or not connection.pid or not connection.laddr:
                continue
            if connection.laddr.port not in SERVICES.values():
                continue
            process = psutil.Process(connection.pid)
            cpu = process.cpu_times()
            readings[str(connection.laddr.port)] = {
                "pid": process.pid,
                "rss_bytes": process.memory_info().rss,
                "threads": process.num_threads(),
                "cpu_seconds": cpu.user + cpu.system,
            }
    except psutil.Error, OSError:
        return readings
    return readings


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def sample_infrastructure(data_directory):
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(data_directory)
    net = psutil.net_io_counters()
    temperatures = getattr(psutil, "sensors_temperatures", lambda: {})()
    gpus = gpu_readings()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "logical_cpus": psutil.cpu_count(),
        "memory_percent": memory.percent,
        "memory_available_bytes": memory.available,
        "memory_total_bytes": memory.total,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "network_sent_bytes": net.bytes_sent,
        "network_received_bytes": net.bytes_recv,
        "uptime_seconds": max(0, time.time() - psutil.boot_time()),
        "process_count": len(psutil.pids()),
        "services": {name: {"port": port, "listening": listening(port)} for name, port in SERVICES.items()},
        "temperature_celsius": [entry.current for entries in temperatures.values() for entry in entries] or None,
        "gpu_utilization_percent": gpus[0]["utilization_percent"] if gpus else None,
        "gpus": gpus,
        "service_processes": process_readings(),
        "database_bytes": (Path(data_directory) / "hq.sqlite3").stat().st_size,
        "scheduled_tasks": scheduled_tasks(),
        "backups": backup_reading(data_directory),
    }
