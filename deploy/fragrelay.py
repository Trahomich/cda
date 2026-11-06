#!/usr/bin/env python3
"""Фрагментирующий SOCKS5-релей для cloudflared (обход DPI, задача #37).

Проблема: DPI на периметре режет TLS-соединения cloudflared к edge Cloudflare
по сигнатуре ClientHello (см. обсуждение задачи #37, посты #111–114, #148).

Решение: cloudflared подключается к этому релею (--edge fragrelay:7844),
релей поднимает соединение до edge через SOCKS5-прокси (аплинк которого
проходит мимо матчера), пересылая первые N байта потока (ClientHello)
по одному байту — DPI не может собрать сигнатуру.

Переменные окружения:
  LISTEN_PORT — порт релея (по умолчанию 7844)
  SOCKS_HOST / SOCKS_PORT — SOCKS5-прокси (192.168.99.45 / 1080)
  EDGE_HOST / EDGE_PORT — адрес edge Cloudflare (region1.v2.argotunnel.com / 7844)
  FRAG_BYTES — сколько первых байт дробить по 1 байту (2048 > ClientHello)
"""
import os
import socket
import threading

LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "7844"))
SOCKS_HOST = os.environ.get("SOCKS_HOST", "192.168.99.45")
SOCKS_PORT = int(os.environ.get("SOCKS_PORT", "1080"))
EDGE_HOST = os.environ.get("EDGE_HOST", "region1.v2.argotunnel.com")
EDGE_PORT = int(os.environ.get("EDGE_PORT", "7844"))
FRAG_BYTES = int(os.environ.get("FRAG_BYTES", "2048"))
PROXY = (SOCKS_HOST, SOCKS_PORT)
DST = (EDGE_HOST, EDGE_PORT)


def socks5_connect(dst_host: str, dst_port: int) -> socket.socket:
    s = socket.create_connection(PROXY, timeout=15)
    s.sendall(b"\x05\x01\x00")  # приветствие: без авторизации
    resp = s.recv(2)
    if resp != b"\x05\x00":
        raise RuntimeError(f"socks auth fail: {resp!r}")
    addr = dst_host.encode()
    req = b"\x05\x01\x00\x03" + bytes([len(addr)]) + addr + dst_port.to_bytes(2, "big")
    s.sendall(req)
    head = s.recv(4)
    if len(head) < 4 or head[1] != 0:
        raise RuntimeError(f"socks connect fail: {head!r}")
    atyp = head[3]
    if atyp == 1:
        s.recv(4 + 2)
    elif atyp == 3:
        ln = s.recv(1)[0]
        s.recv(ln + 2)
    elif atyp == 4:
        s.recv(16 + 2)
    return s


def pipe(src: socket.socket, dst: socket.socket, frag: bool) -> None:
    """Копирование потока; при frag=True первые FRAG_BYTES байт — по 1 байту."""
    try:
        sent = 0
        while True:
            n = 1 if frag and sent < FRAG_BYTES else 65536
            data = src.recv(n)
            if not data:
                break
            dst.sendall(data)
            sent += len(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle(client: socket.socket) -> None:
    try:
        upstream = socks5_connect(*DST)
    except Exception as e:  # noqa: BLE001 — лог и разрыв соединения
        print(f"socks connect error: {e}", flush=True)
        client.close()
        return
    t1 = threading.Thread(target=pipe, args=(client, upstream, True), daemon=True)
    t2 = threading.Thread(target=pipe, args=(upstream, client, False), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    client.close()
    upstream.close()


def main() -> None:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", LISTEN_PORT))
    srv.listen(16)
    print(
        f"fragrelay: 0.0.0.0:{LISTEN_PORT} -> socks5://{SOCKS_HOST}:{SOCKS_PORT} "
        f"-> {EDGE_HOST}:{EDGE_PORT}, frag={FRAG_BYTES}B",
        flush=True,
    )
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
