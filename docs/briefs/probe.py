import os, shutil, subprocess, sys
from pathlib import Path
out = [f"which maestri: {shutil.which('maestri')}", f"MAESTRI env: {sorted(k for k in os.environ if 'MAESTRI' in k)}", f"cwd: {os.getcwd()}"]
for args in (["floor", "status", "--floor", "Dev 2"], ["check", "Claude Code #2"], ["list"]):
    try:
        r = subprocess.run(["C:/Users/samuel.almeida/AppData/Local/Programs/Maestri/resources/cli/maestri.exe", *args], capture_output=True, text=True, timeout=60)
        out.append(f"$ maestri {' '.join(args)} -> rc={r.returncode}\n{(r.stdout or '')[:600]}\nSTDERR: {(r.stderr or '')[:300]}")
    except Exception as e:
        out.append(f"$ maestri {' '.join(args)} -> EXC {e!r}")
(Path(__file__).resolve().parents[2] / "runtime" / "probe-out.txt").write_text("\n\n".join(out), encoding="utf-8")
