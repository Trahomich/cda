#!/usr/bin/env python3
"""deploy_remote.py — деплой ЦДА на сервер из среды без ssh-клиента (paramiko).

Замена deploy/deploy.sh после пересборки workspace 2026-11-06 (из системы
пропали ssh/scp). Делает то же самое: tar-синхронизация backend/,
frontend/dist, deploy/ → ~/cda на сервере, docker compose up -d --build,
ожидание healthz. Секреты — в ~/cda/.env на сервере (вне git).

Использование (из корня workspace):
    python3 deploy/deploy_remote.py            # деплой + ожидание healthz
Требования: pip install paramiko; ключ в ~/.ssh/id_ed25519 (или env CDA_KEY),
хост/юзер — env CDA_HOST/CDA_USER (по умолчанию 192.168.99.201 / cda).
"""
import io
import os
import sys
import tarfile
import time

import paramiko

HOST = os.environ.get("CDA_HOST", "192.168.99.201")
USER = os.environ.get("CDA_USER", "cda")
KEY = os.environ.get("CDA_KEY", os.path.expanduser("~/.ssh/id_ed25519"))
REMOTE_DIR = "cda"
COMPOSE = f"docker compose -f {REMOTE_DIR}/deploy/docker-compose.prod.yml --env-file {REMOTE_DIR}/.env"

SYNC_PATHS = ["backend", "frontend/dist", "deploy"]
EXCLUDES = {
    ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "test-results", ".env",
}


def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = tarinfo.name.split("/")
    if any(p in EXCLUDES for p in parts):
        return None
    if tarinfo.name.startswith("backend/core."):  # core.* дампы из workspace
        return None
    tarinfo.uid = tarinfo.gid = 0
    tarinfo.uname = tarinfo.gname = ""
    return tarinfo


def main() -> int:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in SYNC_PATHS:
            tar.add(path, recursive=True, filter=_filter)
    buf.seek(0)
    size_mb = buf.getbuffer().nbytes / 1e6

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, username=USER, key_filename=KEY, timeout=15,
                allow_agent=False, look_for_keys=False)

    def run(cmd: str, timeout: int = 900) -> int:
        print(f"==> {cmd}")
        _, out, err = cli.exec_command(cmd, timeout=timeout)
        for line in iter(out.readline, ""):
            print("   ", line.rstrip())
        for line in iter(err.readline, ""):
            print("  E", line.rstrip(), file=sys.stderr)
        code = out.channel.recv_exit_status()
        if code != 0:
            print(f"ОШИБКА: код {code}", file=sys.stderr)
        return code

    print(f"==> Проверка .env на сервере + выгрузка архива ({size_mb:.1f} МБ)")
    if run(f"test -f {REMOTE_DIR}/.env") != 0:
        print(f"НЕТ ~/{REMOTE_DIR}/.env — создайте по docs/deploy/README.md", file=sys.stderr)
        return 1
    sftp = cli.open_sftp()
    sftp.putfo(buf, "/tmp/cda_deploy.tgz")
    sftp.close()

    if run(f"tar -xzf /tmp/cda_deploy.tgz -C {REMOTE_DIR} && rm /tmp/cda_deploy.tgz") != 0:
        return 1
    if run(f"{COMPOSE} up -d --build") != 0:
        return 1

    print("==> Ожидание healthz")
    for _ in range(30):
        _, out, _ = cli.exec_command("curl -sf http://127.0.0.1:8080/healthz")
        ok = out.read().decode().strip()
        if ok:
            print(f"OK: /healthz через caddy: {ok}")
            cli.close()
            return 0
        time.sleep(5)
    print("ОШИБКА: /healthz не отвечает", file=sys.stderr)
    run(f"{COMPOSE} logs --tail 50 api db caddy")
    cli.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
