"""orchestrate.py

Starts the three pipeline stages in parallel:
  1. crop-parcels.py     — batch-crops the mosaic for each matching parcel
  2. parcel-processor.py — watches .raster-crops/ and runs tree detection
  3. tree-merger.py      — watches .output-trees-parcels/ and builds union-trees.shp

Each script's stdout/stderr is forwarded to this process's stdout with a
[stage-name] prefix so all logs appear in one place.

Usage:
    python orchestrate.py

Press Ctrl+C to stop all processes.
"""

import os
import sys
import time
import subprocess
import threading
import logging

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ORCHESTRATOR] %(message)s'
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# (display-name, script-filename) ordered by pipeline stage
STAGES = [
    ('crop-parcels',     'crop-parcels.py'),
    ('parcel-processor', 'parcel-processor.py'),
    ('tree-merger',      'tree-merger.py'),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def start_process(script_path: str) -> subprocess.Popen:
    """Launch *script_path* as a child process and return the Popen handle."""
    return subprocess.Popen(
        [sys.executable, '-u', script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=BASE_DIR,
    )


def stream_output(proc: subprocess.Popen, tag: str):
    """Read *proc* stdout in a daemon thread and print each line with *tag*."""
    def _reader():
        try:
            for line in proc.stdout:
                print(f"[{tag}] {line}", end='', flush=True)
        except Exception:
            pass   # process ended
    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    processes = {}   # name -> Popen

    logger.info("Starting pipeline stages …")
    for name, script in STAGES:
        script_path = os.path.join(BASE_DIR, script)
        proc = start_process(script_path)
        processes[name] = proc
        stream_output(proc, name)
        logger.info(f"  {script} started (PID {proc.pid})")

    logger.info("All stages running. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(5)

            for name, script in STAGES:
                proc = processes[name]
                ret  = proc.poll()
                if ret is None:
                    continue   # still running

                if name == 'crop-parcels':
                    # This stage is expected to exit after finishing the batch
                    logger.info(
                        f"crop-parcels finished (exit code {ret}) — "
                        "parcel-processor and tree-merger will continue until "
                        "all queued files are processed."
                    )
                else:
                    logger.warning(
                        f"{name} exited unexpectedly (rc={ret}) — restarting …"
                    )
                    script_path      = os.path.join(BASE_DIR, script)
                    new_proc         = start_process(script_path)
                    processes[name]  = new_proc
                    stream_output(new_proc, name)
                    logger.info(f"  {name} restarted (PID {new_proc.pid})")

    except KeyboardInterrupt:
        logger.info("Ctrl+C received — stopping all stages …")
        for name, proc in processes.items():
            if proc.poll() is None:
                proc.terminate()
        for name, proc in processes.items():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        logger.info("All stages stopped.")


if __name__ == '__main__':
    main()
