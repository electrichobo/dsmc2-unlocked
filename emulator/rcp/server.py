"""
RCP (RED Command Protocol) TCP Server for external camera control applications.
Standard default port: 1111.
"""

import socket
import threading
from typing import Optional
from emulator.rcp.protocol import RcpProtocol


class RcpServer:
    """Provides a networked RCP bridge for third-party control apps."""

    def __init__(self, protocol: RcpProtocol, host: str = "127.0.0.1", port: int = 1111):
        self.protocol = protocol
        self.host = host
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.clients = []

    def start(self):
        if self.running:
            return
        try:
            self.running = True
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)
            self.server_socket.settimeout(0.5)

            self.thread = threading.Thread(target=self._accept_loop, daemon=True, name="RcpServer")
            self.thread.start()
        except Exception as e:
            self.running = False
            self.server_socket = None

    def stop(self):
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
            self.server_socket = None
        for client in self.clients:
            try:
                client.close()
            except Exception:
                pass
        self.clients.clear()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.6)
            self.thread = None

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, addr = self.server_socket.accept()
                self.clients.append(client_sock)
                t = threading.Thread(target=self._client_handler, args=(client_sock,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except Exception:
                break

    def _client_handler(self, sock: socket.socket):
        buffer = ""
        while self.running:
            try:
                data = sock.recv(1024)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    resp = self.protocol.process_command(line)
                    if resp:
                        sock.sendall(resp.encode("utf-8"))
            except Exception:
                break
        try:
            sock.close()
            if sock in self.clients:
                self.clients.remove(sock)
        except Exception:
            pass
