"""Measure idle/peak RAM and CPU for a tutor session, and check keep-alive unload.

Usage: .venv/bin/python scripts/measure_resources.py [--keep-alive 20s]
Starts the backend (and Ollama), runs a few chat turns, samples memory/CPU of
tutor + Ollama processes, waits for the keep-alive period, confirms the model
unloaded, then stops everything and confirms nothing is left running.
"""

import argparse
import json
import os
import threading
import time

import httpx
import psutil

from magnus_tutor import doctor, runtime
from magnus_tutor.config import load_settings, paths, save_settings

ap = argparse.ArgumentParser()
ap.add_argument("--keep-alive", default="20s")
ap.add_argument("--course", default=None)
a = ap.parse_args()

p = paths()
save_settings({"ollama": {"keep_alive": a.keep_alive}}, p)
s = load_settings(p)
GB = 1024**3


def tutor_procs():
    return [x for x in doctor.processes()]


def sample(stop, peaks):
    psutil.cpu_percent(None)
    while not stop.is_set():
        rss = 0
        cpu = 0.0
        for pr in psutil.process_iter(["cmdline", "name"]):
            try:
                cmd = " ".join(pr.info["cmdline"] or [])
                if "magnus_tutor" in cmd or "ollama" in cmd or "llama-server" in cmd:
                    rss += pr.memory_info().rss
                    cpu += pr.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        vm = psutil.virtual_memory()
        peaks["tutor_rss_gb"] = max(peaks.get("tutor_rss_gb", 0), rss / GB)
        peaks["system_used_gb"] = max(peaks.get("system_used_gb", 0), (vm.total - vm.available) / GB)
        peaks["cpu_percent"] = max(peaks.get("cpu_percent", 0), cpu)
        time.sleep(0.5)


vm = psutil.virtual_memory()
result = {"baseline_used_gb": round((vm.total - vm.available) / GB, 1), "total_gb": round(vm.total / GB, 1)}
r = runtime.start_server(p)
assert r["running"], r
url = r["url"]
time.sleep(1)
idle_rss = sum(x["rss_mb"] for x in tutor_procs())
result["idle_tutor_mb_no_model"] = idle_rss

stop, peaks = threading.Event(), {}
t = threading.Thread(target=sample, args=(stop, peaks))
t.start()
turns = []
for q in ["Explain Gauss's law in about 150 words.", "Now give a one-line intuition for why flux depends only on enclosed charge."]:
    with httpx.stream("POST", f"{url}/api/chat", json={"course": a.course, "messages": [{"role": "user", "content": q}]}, timeout=300) as resp:
        for line in resp.iter_lines():
            if line.startswith("data:") and "total_s" in line:
                turns.append(json.loads(line[5:]))
stop.set()
t.join()
result["turns"] = turns
result["peak"] = {k: round(v, 2) for k, v in peaks.items()}
loaded = httpx.get(f"{url}/api/resources").json()["loaded_models"]
result["loaded_after_turns"] = [m["name"] for m in loaded]

ka = a.keep_alive
secs = int(ka[:-1]) * (60 if ka.endswith("m") else 1)
time.sleep(secs + 8)
loaded = httpx.get(f"{url}/api/resources").json()["loaded_models"]
result["loaded_after_keep_alive"] = [m["name"] for m in loaded]
vm = psutil.virtual_memory()
result["used_after_unload_gb"] = round((vm.total - vm.available) / GB, 1)

stopped = runtime.stop_all(p)
time.sleep(1)
left = tutor_procs()
result["after_stop_processes"] = left
vm = psutil.virtual_memory()
result["after_stop_used_gb"] = round((vm.total - vm.available) / GB, 1)
result["startup_items"] = doctor.startup_items()
save_settings({"ollama": {"keep_alive": "3m"}}, p)
print(json.dumps(result, indent=2))
