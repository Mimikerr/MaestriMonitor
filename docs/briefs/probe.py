import os, shutil, subprocess, sys
out = [f"which maestri: {shutil.which('maestri')}", f"MAESTRI env: {sorted(k for k in os.environ if 'MAESTRI' in k)}", f"cwd: {os.getcwd()}"]
for args in (["floor", "status", "--floor", "Dev 2"], ["check", "Claude Code #2"], ["list"]):
    try:
        r = subprocess.run(["C:/Users/samuel.almeida/AppData/Local/Programs/Maestri/resources/cli/maestri.exe", *args], capture_output=True, text=True, timeout=60)
        out.append(f"$ maestri {' '.join(args)} -> rc={r.returncode}\n{(r.stdout or '')[:600]}\nSTDERR: {(r.stderr or '')[:300]}")
    except Exception as e:
        out.append(f"$ maestri {' '.join(args)} -> EXC {e!r}")
open(r"C:\Users\samuel.almeida\StudioProjects\.maestri\monitor\probe-out.txt", "w", encoding="utf-8").write("\n\n".join(out))
