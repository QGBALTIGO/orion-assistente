"""Run pytest with all non-loopback network connections and DNS blocked."""
import ipaddress
import socket
import sys


def main(args=None):
    import pytest
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_dns = socket.getaddrinfo
    def local(address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, bytes): host = host.decode("ascii")
        if host == "localhost": return
        try: allowed = ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError): allowed = False
        if not allowed:
            raise AssertionError("External network blocked in isolated Orion plan tests")
    def connect(sock, address):
        local(address)
        return original_connect(sock, address)
    def connect_ex(sock, address):
        local(address)
        return original_connect_ex(sock, address)
    def dns(host, *values, **kwargs):
        if host is not None: local((host, 0))
        return original_dns(host, *values, **kwargs)
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, dns
    return pytest.main(sys.argv[1:] if args is None else args)


if __name__ == "__main__":
    raise SystemExit(main())
