#!/usr/bin/env python3
"""sshx.py — запуск команды на сервере ЦДА через paramiko (ssh-клиента в системе нет).
Использование: python3 sshx.py "команда"  |  python3 sshx.py put <локальный> <удалённый>
"""
import sys
import paramiko

import os
HOST, USER = "192.168.99.201", "cda"
KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "id_ed25519")


def client() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, key_filename=KEY, timeout=15,
              allow_agent=False, look_for_keys=False)
    return c


if __name__ == "__main__":
    c = client()
    try:
        if sys.argv[1] == "put":
            sftp = c.open_sftp()
            sftp.put(sys.argv[2], sys.argv[3])
            print(f"put {sys.argv[2]} -> {sys.argv[3]}")
        else:
            _, out, err = c.exec_command(sys.argv[1], timeout=600)
            o, e = out.read().decode(), err.read().decode()
            if o:
                print(o, end="")
            if e:
                print(e, end="", file=sys.stderr)
            sys.exit(out.channel.recv_exit_status())
    finally:
        c.close()
