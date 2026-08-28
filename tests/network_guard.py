"""Process-wide socket guard used only by the canonical security test runner."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any


_INSTALLED = False
_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CONNECT_EX = socket.socket.connect_ex
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def _loopback(host: Any) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if not isinstance(host, str):
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _blocked(host: Any) -> OSError:
    return OSError(f"Stage 11 test safety blocked non-loopback network host: {host!r}")


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) and address else None
        if not _loopback(host):
            raise _blocked(host)
        return _ORIGINAL_CONNECT(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        host = address[0] if isinstance(address, tuple) and address else None
        if not _loopback(host):
            raise _blocked(host)
        return _ORIGINAL_CONNECT_EX(sock, address)

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        host = address[0] if isinstance(address, tuple) and address else None
        if not _loopback(host):
            raise _blocked(host)
        return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and not _loopback(host):
            raise _blocked(host)
        return _ORIGINAL_GETADDRINFO(host, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.create_connection = guarded_create_connection
    socket.getaddrinfo = guarded_getaddrinfo
    _INSTALLED = True


install()
