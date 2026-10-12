"""Stop only an owned local child; on Windows also stop its Reader descendants."""
import os
import subprocess


def stop_owned_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Only the PID of our Popen handle. No user-supplied PID/process name.
        stopped = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if stopped.returncode == 0:
            process.wait(timeout=5)
            return
        raise RuntimeError("OWNED_PROCESS_TREE_STOP_NOT_VERIFIED")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
