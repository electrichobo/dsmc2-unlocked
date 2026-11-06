"""
RED Command Protocol (RCP) Parser and Serializer for DSMC2.
Implements parameter query, control commands, and camera state reflection.
"""

from typing import Dict, Any, Tuple, Optional


class RcpProtocol:
    """Handles RED Command Protocol framing, parsing, and dispatching."""

    # Standard DSMC2 Parameter IDs
    PARAM_RECORD_STATE = "record_state"
    PARAM_FPS = "frame_rate"
    PARAM_FRAME_RATE = "frame_rate"
    PARAM_ISO = "iso"
    PARAM_COLOR_TEMP = "color_temperature"
    PARAM_SHUTTER = "shutter_angle"
    PARAM_SHUTTER_ANGLE = "shutter_angle"
    PARAM_RESOLUTION = "resolution"
    PARAM_FORMAT = "record_format"
    PARAM_BATTERY_PERCENT = "battery_percent"
    PARAM_MEDIA_REMAINING = "media_remaining_percent"
    PARAM_CAMERA_NAME = "camera_name"
    PARAM_CAMERA_PIN = "camera_pin"

    def __init__(self, camera_state: Optional[Dict[str, Any]] = None):
        self.state = {
            self.PARAM_RECORD_STATE: "0",        # 0 = idle, 1 = pre-record, 2 = recording
            self.PARAM_FPS: "23.976",
            self.PARAM_ISO: "800",
            self.PARAM_COLOR_TEMP: "5600",
            self.PARAM_SHUTTER: "180.0",
            self.PARAM_RESOLUTION: "5120x2700",  # 5K
            self.PARAM_FORMAT: "R3D",
            self.PARAM_BATTERY_PERCENT: "95",
            self.PARAM_MEDIA_REMAINING: "82",
            self.PARAM_CAMERA_NAME: "SCARLET-W",
            self.PARAM_CAMERA_PIN: "10042",
        }
        if camera_state:
            self.state.update(camera_state)

    def process_command(self, cmd_line: str) -> str:
        """
        Process an RCP command line.
        Formats:
          GET <param>
          SET <param> <value>
          RECORD START
          RECORD STOP
        """
        line = cmd_line.strip()
        if not line:
            return ""

        parts = line.split(maxsplit=2)
        verb = parts[0].upper()

        if verb == "GET":
            if len(parts) < 2:
                return "ERR INVALID_SYNTAX\n"
            param = parts[1].lower()
            val = self.state.get(param)
            if val is not None:
                return f"RED {param} {val}\n"
            return f"ERR UNKNOWN_PARAM {param}\n"

        elif verb == "SET":
            if len(parts) < 3:
                return "ERR INVALID_SYNTAX\n"
            param = parts[1].lower()
            val = parts[2]
            self.state[param] = val
            return f"RED {param} {val}\n"

        elif verb == "RECORD":
            if len(parts) >= 2:
                action = parts[1].upper()
                if action == "START":
                    self.state[self.PARAM_RECORD_STATE] = "2"
                    return f"RED {self.PARAM_RECORD_STATE} 2\n"
                elif action == "STOP":
                    self.state[self.PARAM_RECORD_STATE] = "0"
                    return f"RED {self.PARAM_RECORD_STATE} 0\n"

        elif verb == "STATUS":
            items = " ".join(f"{k}={v}" for k, v in sorted(self.state.items()))
            return f"STATUS {items}\n"

        return f"ERR UNKNOWN_COMMAND {verb}\n"
