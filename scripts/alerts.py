"""
Convierte dist/data/alerts.json en issues de GitHub. GitHub te los manda por email
si tienes activadas las notificaciones del repositorio (Watch → All activity, o
Custom → Issues).

No repite avisos: si ya existe un issue (abierto o cerrado) con el mismo título,
no lo vuelve a crear. Los títulos llevan mes/trimestre/fecha de entrada para que
cada aviso salga una sola vez.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def gh(*args) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main(path: str):
    alerts = json.loads(Path(path).read_text(encoding="utf-8"))
    if not alerts:
        print("[alertas] nada que avisar")
        return
    existing = json.loads(gh("issue", "list", "--state", "all", "--limit", "1000", "--json", "title"))
    titles = {i["title"] for i in existing}
    try:
        gh("label", "create", "alerta-portal", "--color", "1f6feb", "--force")
    except subprocess.CalledProcessError:
        pass
    new = 0
    for a in alerts:
        if a["title"] in titles:
            continue
        gh("issue", "create", "--title", a["title"], "--body", a["body"], "--label", "alerta-portal")
        new += 1
    print(f"[alertas] {new} nuevas de {len(alerts)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dist/data/alerts.json")
