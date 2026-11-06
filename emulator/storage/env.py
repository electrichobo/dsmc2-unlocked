"""
RED DSMC2 Camera Environment (/env) Configuration.
Supplies hardware identity, camera model ID, sensor parameters, and serial numbers.
"""

from typing import Dict, Any


class CameraEnvironment:
    """Manages the persistent /env partition configuration for DSMC2."""

    # Default Reference Model: 0x16 = SCARLET-W 5K
    DEFAULT_CONFIG = {
        "CAMERA_TYPE": "0x16",
        "CAMERA_NAME": "SCARLET-W 5K",
        "CAMERA_PIN": "10042",
        "SERIAL_NUMBER": "016-10042",
        "BOARD_REV": "0x01",
        "SENSOR_TYPE": "0x04",
        "SENSOR_NAME": "RED DRAGON 5K S35",
        "SENSOR_WIDTH": "5120",
        "SENSOR_HEIGHT": "2700",
        "MAX_FPS": "60",
        "MAC_ADDR": "00:1E:A9:10:04:20",
        "BUILD_VERSION": "7.4.1",
        "BOOT_SLOT": "2",
        "HARDWARE_VERSION": "DSMC2-BRAIN-V1",
        "SECURITY_SALT": "27A8124FB3EA",
    }

    def __init__(self, overrides: Dict[str, Any] = None):
        self.config = dict(self.DEFAULT_CONFIG)
        if overrides:
            self.config.update(overrides)

    def get(self, key: str, default: str = "") -> str:
        return self.config.get(key, default)

    def set(self, key: str, value: str):
        self.config[key] = str(value)

    def serialize(self) -> bytes:
        """Format as standard null-terminated key=value pairs for flash /env block."""
        entries = []
        for k, v in sorted(self.config.items()):
            entries.append(f"{k}={v}".encode("utf-8"))
        return b"\x00".join(entries) + b"\x00\x00"

    @classmethod
    def deserialize(cls, data: bytes) -> "CameraEnvironment":
        env = cls()
        for chunk in data.split(b"\x00"):
            if b"=" in chunk:
                try:
                    k, v = chunk.decode("utf-8").split("=", 1)
                    env.set(k.strip(), v.strip())
                except Exception:
                    pass
        return env
