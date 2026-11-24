"""
RED DSMC2 Redpine RS9113 Wi-Fi Host Interface & RFTP (RED File Transfer Protocol) Service.

Models:
1. RedpineSignals RS9113 Wi-Fi SPI/SDIO host controller (`RedpineRs9113Wifi`), supporting
   Infrastructure / Ad-Hoc / Off modes and SPI host command framing.
2. RFTP file transfer server & command dispatcher (`RftpServer`) over `/data` (presets, LUTs,
   CDLs, reticles, logs) and `/media` (recorded `.RDC`/`.R3D`/`.RMD` clips).
"""

import os
import zlib
import shlex
import socket
import threading
import urllib.parse
from typing import Any, Dict, List, Optional


class RedpineRs9113Wifi:
    """Behavioral register/SPI-host model for Redpine RS9113 802.11n Wi-Fi module."""

    MODE_INFRASTRUCTURE = "INFRASTRUCTURE"
    MODE_ADHOC = "ADHOC"
    MODE_OFF = "OFF"

    CMD_BOOTLOADER_INIT = 0x01
    CMD_SCAN = 0x02
    CMD_JOIN = 0x03
    CMD_GET_STATUS = 0x04
    CMD_GET_MAC = 0x05

    def __init__(self):
        self.mode: str = self.MODE_INFRASTRUCTURE
        self.ssid: str = "RED_DSMC2_5G"
        self.ip_address: str = "192.168.1.100"
        self.mac_address: str = "00:23:A7:52:45:44"
        self.firmware_version: str = "1.6.12"
        self.connected: bool = True
        self.channel: int = 36
        self.rssi_dbm: int = -48
        self.command_log: List[Dict[str, Any]] = []

    def set_mode(self, mode: str) -> str:
        """Set operational Wi-Fi mode ('INFRASTRUCTURE', 'ADHOC', or 'OFF')."""
        m = mode.strip().upper().replace("-", "")
        if "OFF" in m:
            self.mode = self.MODE_OFF
            self.connected = False
        elif "ADHOC" in m:
            self.mode = self.MODE_ADHOC
            self.connected = True
            self.ssid = "RED_DSMC2_ADHOC"
            self.ip_address = "169.254.1.10"
        else:
            self.mode = self.MODE_INFRASTRUCTURE
            self.connected = True
            self.ssid = "RED_DSMC2_5G"
            self.ip_address = "192.168.1.100"
        return self.mode

    def spi_transceive_command(self, cmd_id: int, payload: bytes = b"") -> bytes:
        """Process RS9113 host SPI command frame and return response packet."""
        cid = cmd_id & 0xFF
        self.command_log.append({"cmd_id": cid, "len": len(payload)})
        if cid == self.CMD_GET_MAC:
            mac_bytes = bytes(int(p, 16) for p in self.mac_address.split(":"))
            return bytes([0x58, cid, len(mac_bytes), 0x00]) + mac_bytes
        if cid == self.CMD_GET_STATUS:
            status_byte = 0x01 if self.connected else 0x00
            return bytes([0x58, cid, 0x02, 0x00, status_byte, self.channel & 0xFF])
        if cid == self.CMD_SCAN:
            body = self.ssid.encode("ascii", errors="ignore")
            return bytes([0x58, cid, len(body) & 0xFF, (len(body) >> 8) & 0xFF]) + body
        if cid == self.CMD_JOIN and payload:
            self.ssid = payload.decode("ascii", errors="ignore").rstrip("\x00") or self.ssid
            self.connected = True
            return bytes([0x58, cid, 0x01, 0x00, 0x00])
        return bytes([0x58, cid, 0x01, 0x00, 0x00])

    def get_status(self) -> Dict[str, Any]:
        return {
            "chipset": "Redpine Signals RS9113",
            "mode": self.mode,
            "connected": self.connected,
            "ssid": self.ssid,
            "ip_address": self.ip_address,
            "mac_address": self.mac_address,
            "firmware_version": self.firmware_version,
            "channel": self.channel,
            "rssi_dbm": self.rssi_dbm,
        }


class RftpServer:
    """
    RED File Transfer Protocol (RFTP) service for clips (`/media`) and config (`/data`).

    Supports direct command dispatch (`execute_command`) as well as TCP line-protocol
    connections when started.
    """

    def __init__(self, camera: Any, host: str = "127.0.0.1", port: int = 0):
        self.camera = camera
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _resolve_rftp_path(self, virtual_path: str) -> Optional[str]:
        norm = virtual_path.replace("\\", "/").strip()
        if not norm.startswith("/"):
            norm = "/" + norm
        rec = getattr(self.camera, "recorder", None)
        if norm == "/media" or norm.startswith("/media/"):
            media_root = rec.media_dir if rec else os.path.join(self.camera.persistent_dir, "media")
            sub = norm[len("/media"):].lstrip("/")
            if not sub:
                return media_root
            candidate = os.path.abspath(os.path.join(media_root, sub))
            if not candidate.startswith(os.path.abspath(media_root)):
                return None
            if os.path.exists(candidate) or "/" in sub or os.path.dirname(candidate) != os.path.abspath(media_root):
                return candidate
            # Allow flat `/media/<clip>.R3D` or `/media/<clip>.RMD` shorthand
            if rec:
                for clip in rec.list_clips():
                    if os.path.basename(clip["r3d_path"]) == sub:
                        return clip["r3d_path"]
                    if os.path.basename(clip["rmd_path"]) == sub:
                        return clip["rmd_path"]
            return candidate
        vfs = getattr(self.camera, "vfs", None)
        if vfs:
            if not (norm.startswith("/data/") or norm == "/data" or norm.startswith("/log/") or norm.startswith("/pdata/")):
                norm = "/data/" + norm.lstrip("/")
            return vfs.resolve_path(norm)
        return None

    def execute_command(self, line: str, payload: Optional[bytes] = None) -> Dict[str, Any]:
        """Execute an RFTP verb (`LIST`, `GET`, `PUT`, `STAT`, `DELETE`)."""
        parts = shlex.split(line.strip()) if line.strip() else []
        if not parts:
            return {"status": "ERROR", "error": "Empty command"}
        cmd = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else "/media"

        if cmd == "LIST":
            norm = arg.replace("\\", "/").strip() or "/media"
            if not norm.startswith("/"):
                norm = "/" + norm
            entries: List[Dict[str, Any]] = []
            if norm.startswith("/media"):
                rec = getattr(self.camera, "recorder", None)
                if rec:
                    for c in rec.list_clips():
                        entries.append({
                            "name": os.path.basename(c["r3d_path"]),
                            "path": f"/media/{os.path.basename(c['r3d_path'])}",
                            "size": os.path.getsize(c["r3d_path"]) if os.path.exists(c["r3d_path"]) else 0,
                        })
                        if os.path.exists(c["rmd_path"]):
                            entries.append({
                                "name": os.path.basename(c["rmd_path"]),
                                "path": f"/media/{os.path.basename(c['rmd_path'])}",
                                "size": os.path.getsize(c["rmd_path"]),
                            })
            else:
                target_dir = self._resolve_rftp_path(norm)
                if target_dir and os.path.isdir(target_dir):
                    for root, _, files in os.walk(target_dir):
                        for fn in sorted(files):
                            full = os.path.join(root, fn)
                            rel = os.path.relpath(full, target_dir).replace("\\", "/")
                            entries.append({
                                "name": fn,
                                "path": norm.rstrip("/") + "/" + rel,
                                "size": os.path.getsize(full),
                            })
            return {"status": "OK", "path": norm, "entries": entries}

        host_path = self._resolve_rftp_path(arg)
        if host_path is None:
            return {"status": "ERROR", "error": f"Invalid path: {arg}"}

        if cmd == "STAT":
            exists = os.path.isfile(host_path)
            size = os.path.getsize(host_path) if exists else 0
            crc32 = 0
            if exists:
                with open(host_path, "rb") as f:
                    crc32 = zlib.crc32(f.read()) & 0xFFFFFFFF
            return {"status": "OK", "exists": exists, "size": size, "crc32": crc32}

        if cmd == "GET":
            if not os.path.isfile(host_path):
                return {"status": "ERROR", "error": f"File not found: {arg}"}
            with open(host_path, "rb") as f:
                data = f.read()
            return {"status": "OK", "size": len(data), "data": data, "crc32": zlib.crc32(data) & 0xFFFFFFFF}

        if cmd == "PUT":
            raw = payload if payload is not None else b""
            os.makedirs(os.path.dirname(host_path), exist_ok=True)
            with open(host_path, "wb") as f:
                f.write(raw)
            return {"status": "OK", "size": len(raw), "path": arg, "crc32": zlib.crc32(raw) & 0xFFFFFFFF}

        if cmd == "DELETE":
            if os.path.isfile(host_path):
                os.remove(host_path)
                return {"status": "OK", "deleted": True, "path": arg}
            return {"status": "ERROR", "error": f"File not found: {arg}"}

        return {"status": "ERROR", "error": f"Unsupported RFTP command: {cmd}"}

    def start(self, port: Optional[int] = None) -> int:
        """Start lightweight RFTP TCP listener; returns bound port."""
        if self._running and self._sock:
            return self.port
        if port is not None:
            self.port = port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(4)
        sock.settimeout(0.2)
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._serve_loop, daemon=True, name="RftpServer")
        self._thread.start()
        return self.port

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _serve_loop(self):
        import base64
        import json

        while self._running and self._sock:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            with conn:
                conn.settimeout(2.0)
                buf = b""
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    if b"\n" in buf:
                        break
                line = buf.decode("utf-8", errors="ignore").splitlines()[0] if buf else ""
                parts = line.split(" ")
                payload = None
                if parts and parts[0].upper() == "PUT" and len(parts) >= 3:
                    # Line format over TCP: PUT <path> <base64_payload>
                    try:
                        payload = base64.b64decode(parts[2].encode("ascii"))
                        line = f"PUT {parts[1]}"
                    except Exception:
                        payload = b""
                res = self.execute_command(line, payload=payload)
                if "data" in res and isinstance(res["data"], (bytes, bytearray)):
                    res = dict(res)
                    res["data_b64"] = base64.b64encode(res.pop("data")).decode("ascii")
                conn.sendall((json.dumps(res) + "\n").encode("utf-8"))
