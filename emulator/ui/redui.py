"""
RedUi Graphical Engine and Interactive Menu System for RED DSMC2.
Provides authentic DSMC2 HUD overlays, quick-selection dialogs (FPS, Shutter, ISO, Format),
full system menu, exposure tools (False Color, Zebras, 1:1 Mag), and touch feedback.
"""

import os
import time
from typing import Optional, Tuple, Dict, Any, List
from PIL import Image, ImageDraw, ImageFont
from emulator.rcp.protocol import RcpProtocol

try:
    FONT_TITLE = ImageFont.truetype("arialbd.ttf", 15)
    FONT_MED = ImageFont.truetype("arial.ttf", 13)
    FONT_BOLD = ImageFont.truetype("arialbd.ttf", 13)
    FONT_SM = ImageFont.truetype("arial.ttf", 11)
except Exception:
    FONT_TITLE = ImageFont.load_default()
    FONT_MED = ImageFont.load_default()
    FONT_BOLD = ImageFont.load_default()
    FONT_SM = ImageFont.load_default()


class RedUiEngine:
    """
    Simulates the RED DSMC2 RedUi graphical user interface.
    Handles touch regions, modal dialogs, hierarchical system menu, and display overlays.
    """

    WIDTH = 1024
    HEIGHT = 576

    # Available quick parameters
    FPS_OPTIONS = ["23.98", "24.00", "25.00", "29.97", "48.00", "50.00", "59.94", "60.00", "120.00"]
    SHUTTER_OPTIONS = ["1/24", "1/32", "1/48", "1/50", "1/60", "1/96", "1/120", "1/250", "1/500", "180.0°"]
    ISO_OPTIONS = ["250", "320", "400", "500", "640", "800", "1000", "1280", "1600", "3200"]
    FORMAT_OPTIONS = ["5K 16:9", "5K 2:1", "4.5K 16:9", "4K 16:9", "3K 16:9", "2K 16:9"]
    IRIS_OPTIONS = ["1.4", "1.8", "2.0", "2.8", "4.0", "5.6", "8.0", "11.0", "16.0", "22.0"]

    MENU_TABS = ["SETTINGS", "MEDIA", "AUDIO", "OVERLAYS", "LOOK", "ABOUT"]

    # Official DSMC2 Master Menu Hierarchy
    DSMC2_MENU_TREE: Dict[str, Any] = {
        "IMAGE": {
            "IMAGE PIPELINE": ["IPP2 Mode", "Legacy Mode", "Options", "Output Summary"],
            "WHITE BALANCE": ["3200K", "4000K", "5000K", "5600K", "6500K", "Auto WB"],
            "ISO": ["250", "320", "400", "500", "640", "800", "1000", "1280", "1600", "3200"],
            "COLOR": ["Saturation", "Contrast", "Brightness", "Exposure Comp"],
            "GAIN": ["Red Gain", "Green Gain", "Blue Gain", "User Matrix"],
            "CURVES": ["Luma Curve", "Red Curve", "Green Curve", "Blue Curve", "Reset Curves"],
            "LGG": ["Lift", "Gamma", "Gain"],
            "CDL": ["Slope", "Power", "Offset", "Saturation", "CDLs"],
            "3D LUT": ["RED IPP2 Cinema Standard", "Film Rec709", "Low Contrast Print", "Log3G10 Neutral"],
        },
        "MONITORING": {
            "MONITORS": ["Brain LCD", "EVF", "HD-SDI", "HDMI", "Priority"],
            "TEST SIGNAL": ["Off", "SMPTE Bars", "Chip Chart", "Luma Ramp", "Audio Tone 1kHz"],
            "LOOK AROUND": ["Look Around ON", "Look Around OFF"],
            "ADVANCED": ["Clean Feed", "Overlays Feed", "Touchscreen Lock", "Genlock Offset"],
        },
        "OVERLAYS": {
            "TOOLS": ["False Color", "Magnify 1:1", "RAW View", "Horizon", "Zebras 95%"],
            "STATUS": ["Camera Status", "Lens Status", "Power Status"],
            "GUIDES": ["16:9", "2.39:1", "1.85:1", "Off"],
            "CUSTOM": ["In Camera", "On Media Overlays", "Custom Reticle"],
        },
        "POWER": {
            "Power In": ["DC IN 14.8V", "Low Battery Warning", "Min Voltage Alert"],
            "Power Out": ["Aux 12V", "RS 24V", "USB 5V Power"],
            "Power Save": ["Auto Sleep", "LCD Dimmer", "Low Power Fan Mode"],
        },
        "PLAYBACK": {
            "Playback Screen": ["Browse Recorded Clips", "Shuttle / Play", "In/Out Points", "Clip Metadata"],
        },
        "MEDIA": {
            "Device": ["Format RED MINI-MAG", "Eject Media", "Media Status & Space"],
            "Clips": ["List R3D Clips", "Clip Properties", "Delete Last Clip"],
        },
        "PRESETS": {
            "Camera Presets": ["Save User Preset", "Load User Preset", "Factory Default"],
            "Looks": ["Cinema Standard", "High Dynamic Range", "Vintage Film Look"],
            "Auto Presets": ["Narrative 24fps", "Commercial 60fps", "Slow Motion 120fps"],
        },
        "SETTINGS": {
            "PROJECT": {
                "Frame Rate": ["23.98", "24.00", "25.00", "29.97", "48.00", "50.00", "59.94", "60.00", "120.00"],
                "Exposure": ["1/24", "1/32", "1/48", "1/50", "1/60", "1/96", "1/120", "1/250", "1/500", "180.0°"],
                "Timecode": ["Timecode Display Mode", "RTC Time of Day", "External LTC"],
                "Slate": ["Scene A01", "Camera A", "Take 1", "Auto Slate"],
                "Format": ["5K 16:9", "5K 2:1", "4.5K 16:9", "4K 16:9", "3K 16:9", "2K 16:9"],
                "Sensor": ["Standard Scan", "Flip / Mirror Scan"],
            },
            "RECORDING": {
                "Mode": ["Continuous Record", "Frame Burst Mode", "Storage to SSD"],
                "Codec": ["REDCODE 3:1", "REDCODE 5:1", "REDCODE 8:1", "REDCODE 12:1", "REDCODE 16:1", "Apple ProRes 422 HQ"],
                "Frame Processing": ["Normal Processing", "Multi-Frame Burst"],
                "Pre-Record": ["Pre-Record 4s", "Pre-Record 8s", "Always Trigger"],
                "Indicator": ["Beeps & Tones ON", "EVF Tally Light", "SSD Tally Light"],
            },
            "HDRX": ["HDRx ON", "HDRx OFF", "+1 Stop", "+2 Stops", "+3 Stops", "Track A", "Track X"],
            "AUDIO": {
                "Control": ["Ch1 Gain +18dB", "Ch2 Gain +18dB", "Ch3 Gain 0dB", "Ch4 Gain 0dB", "Headphone Vol 80%"],
                "Mix": ["Stereo Mix L/R", "Monitor Mix"],
            },
            "FOCUS": ["Manual Focus", "Autofocus Single", "Continuous AF", "Rack Focus"],
            "EXPOSURE ASSIST": ["Exposure Priority", "Spot Metering", "Center Weighted", "Histogram Mode"],
            "SETUP": {
                "Keys": ["Key Mapping", "User 1 Action", "User 2 Action", "Soft Keys"],
                "Date/Time": ["Set Date", "Set Local Time", "Time Zone UTC-8"],
                "Communication": ["Camera PIN", "Serial RS-232", "Ethernet IP", "WiFi Setup", "RCP TCP 1111"],
                "GPIO / Sync": ["Genlock Sync", "BRAIN GPIO Out", "GPI Trigger"],
                "Fan Control": ["Adaptive Fan", "Quiet Fan Mode", "Manual Fan Speed"],
                "Lens": ["Lens Info", "Electronic Mount EF", "Run AF Sweep", "Metadata Sync"],
                "Motor Control": ["General Motors", "Focus Motor", "Iris Motor", "Zoom Motor"],
            },
            "MAINTENANCE": {
                "Save Log": ["Save Log to Media", "View System Log"],
                "Upgrade": ["Upgrade Camera Firmware", "Check Media Firmware"],
                "Calibrate": ["Black Shading Sensor (T/E)", "Calibrate Gyro/Acc"],
                "Self Test": ["Sensor Test Pattern", "Touchscreen Test"],
                "Restore System": ["Reset Defaults", "Wipe Camera Config"],
                "Rediscover": ["Rediscover Hardware Modules"],
                "System Status": ["Project Status", "Attached Modules", "RCP Connections", "Camera Info"],
                "OLPF": ["Standard OLPF", "Skin Tone-Highlight", "Low Light"],
            }
        }
    }

    def __init__(self, camera):
        self.camera = camera

        # Modal / Dialog State
        self.active_dialog: Optional[str] = None # "FPS", "SHUTTER", "ISO", "FORMAT", "MEDIA", "AUDIO"
        self.dialog_selected_index: int = 0

        # DSMC2 Main Menu State
        self.is_menu_open: bool = False
        self.menu_path: List[str] = ["Menu"]
        self.menu_active_tab: str = "SETTINGS"
        self.menu_selected_index: int = 0
        self.active_lut: str = "RED IPP2 Cinema Standard"
        self.active_false_color_mode: str = "Focus"

        # Tool States
        self.is_magnify_on: bool = False
        self.is_false_color_on: bool = False
        self.is_zebras_on: bool = False
        self.overlay_mode: str = "Standard" # Standard, Overview, Guides, None
        self.guide_mode: str = "16:9"       # 16:9, 2.39:1, 1.85:1, Off

        # Touch and Feedback State
        self.last_touch_pos: Optional[Tuple[int, int]] = None
        self.last_touch_time: float = 0.0
        self.is_touching: bool = False
        self.focus_point: Optional[Tuple[int, int]] = (512, 288)

        # Build False Color LUT and flat palette
        self._false_color_lut = self._build_false_color_lut()
        self._false_color_palette = self._build_false_color_palette()
        self._zebra_pattern = self._build_zebra_pattern()

    def _build_false_color_lut(self) -> List[Tuple[int, int, int]]:
        """
        Build authentic RED False Color exposure gradient lookup table (0..255 luminance).
        Purple: 0-5 IRE (Clipped blacks)
        Blue: 6-12 IRE
        Teal: 13-25 IRE
        Green: 40-45 IRE (18% Gray exposure calibration)
        Pink: 52-58 IRE (Skin tone highlight)
        Straw/Orange: 78-86 IRE
        Red: 98-100 IRE (98% highlight warning)
        Yellow: 101+ IRE (Blown highlights)
        """
        lut = []
        for i in range(256):
            ire = int((i / 255.0) * 108.0)
            if ire <= 2:
                lut.append((140, 20, 220))   # Purple (Clipped black)
            elif ire <= 10:
                lut.append((20, 60, 220))    # Blue (Deep shadow)
            elif ire <= 20:
                lut.append((20, 180, 200))   # Teal (Shadow)
            elif 40 <= ire <= 45:
                lut.append((0, 220, 0))      # Green (18% Middle Gray)
            elif 52 <= ire <= 58:
                lut.append((240, 110, 170))  # Pink (Caucasian skin highlight)
            elif 78 <= ire <= 86:
                lut.append((250, 170, 0))    # Orange / Straw
            elif 98 <= ire <= 100:
                lut.append((240, 10, 10))    # Red (98% Clipping warning)
            elif ire > 100:
                lut.append((255, 255, 0))    # Yellow (100%+ Blown out)
            else:
                lut.append((i, i, i))        # Neutral monochrome
        return lut

    def _build_false_color_palette(self) -> List[int]:
        """Flat 768-entry palette for C-accelerated PIL palette conversion."""
        palette = []
        for r, g, b in self._false_color_lut:
            palette.extend([r, g, b])
        return palette

    def _build_zebra_pattern(self) -> Image.Image:
        """Pre-rendered diagonal zebra stripe overlay for zero-allocation blitting."""
        pattern = Image.new("RGBA", (self.WIDTH, self.HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(pattern)
        for d in range(-self.HEIGHT, self.WIDTH, 12):
            draw.line([(d, 0), (d + self.HEIGHT, self.HEIGHT)], fill=(255, 255, 255, 220), width=3)
            draw.line([(d + 3, 0), (d + self.HEIGHT + 3, self.HEIGHT)], fill=(220, 20, 20, 220), width=3)
        return pattern

    def toggle_menu(self):
        """Toggle DSMC2 system menu on/off."""
        self.is_menu_open = not self.is_menu_open
        self.active_dialog = None
        if self.is_menu_open:
            self.menu_path = ["Menu"]
        self.camera.log_system(f"[UI] DSMC2 Main Menu {'OPENED' if self.is_menu_open else 'CLOSED'}")

    def toggle_magnify(self):
        """Toggle 1:1 Pixel Magnification mode."""
        self.is_magnify_on = not self.is_magnify_on
        self.camera.log_system(f"[UI] 1:1 Magnify {'ENABLED' if self.is_magnify_on else 'DISABLED'}")

    def toggle_false_color(self):
        """Toggle False Color exposure assist overlay."""
        self.is_false_color_on = not self.is_false_color_on
        self.camera.log_system(f"[UI] False Color Exposure Assist {'ENABLED' if self.is_false_color_on else 'DISABLED'}")

    def toggle_zebras(self):
        """Toggle Zebras highlight assist."""
        self.is_zebras_on = not self.is_zebras_on
        self.camera.log_system(f"[UI] Zebras Highlight Assist {'ENABLED' if self.is_zebras_on else 'DISABLED'}")

    # Touch Event Router
    def handle_touch(self, x: int, y: int, pressed: bool) -> bool:
        """
        Handle touch event from screen at (x, y).
        Returns True if the event interacted with a UI control.
        """
        self.last_touch_pos = (x, y)
        self.last_touch_time = time.time()
        self.is_touching = pressed

        # Only process click triggers on touch down
        if not pressed:
            return True

        # 1. If DSMC2 Main Menu is open
        if self.is_menu_open:
            return self._handle_menu_touch(x, y)

        # 2. If a Quick Parameter Dialog is open
        if self.active_dialog:
            return self._handle_dialog_touch(x, y)

        # 3. Handle Normal HUD Touch Regions
        # Top Bar (0 <= y <= 40)
        if 0 <= y <= 40:
            # STBY / REC Badge (12 <= x <= 82)
            if 12 <= x <= 82:
                self.camera.press_record()
                return True
            # Menu Button / Model Header (88 <= x <= 245)
            if 88 <= x <= 245:
                self.toggle_menu()
                return True
            # FPS Badge (255 <= x <= 365)
            if 255 <= x <= 365:
                self._open_dialog("FPS")
                return True
            # SHUTTER Badge (375 <= x <= 495)
            if 375 <= x <= 495:
                self._open_dialog("SHUTTER")
                return True
            # IRIS Badge (505 <= x <= 615)
            if 505 <= x <= 615:
                self._open_dialog("IRIS")
                return True
            # ISO Badge (625 <= x <= 735)
            if 625 <= x <= 735:
                self._open_dialog("ISO")
                return True
            # FORMAT Badge (745 <= x <= 895)
            if 745 <= x <= 895:
                self._open_dialog("FORMAT")
                return True
            # Authentic Right-side Menu Button (900 <= x <= 1015)
            if 900 <= x <= 1015:
                self.toggle_menu()
                return True

        # Bottom Bar (535 <= y <= 576)
        if 535 <= y <= 576:
            # Timecode (15 <= x <= 240)
            if 15 <= x <= 240:
                self.camera.log_system("[UI] Timecode display mode tapped")
                return True
            # Media Status (250 <= x <= 660)
            if 250 <= x <= 660:
                self._open_dialog("MEDIA")
                return True
            # Audio Status (670 <= x <= 860)
            if 670 <= x <= 860:
                self._open_dialog("AUDIO")
                return True

        # Center area tap: Set spot metering / focus reticle
        if 40 < y < 535:
            self.focus_point = (x, y)
            self.camera.log_system(f"[UI] Spot focus / metering target moved to ({x}, {y})")
            return True

        return False

    def _open_dialog(self, dialog_name: str):
        self.active_dialog = dialog_name
        self.dialog_selected_index = 0
        self.camera.log_system(f"[UI] Opened {dialog_name} quick-selection overlay")

    def _handle_dialog_touch(self, x: int, y: int) -> bool:
        """Handle touches inside an open quick-selection dialog."""
        # Dialog box boundaries: x: 160..864, y: 140..436
        cx, cy = 512, 288
        dx1, dy1, dx2, dy2 = cx - 350, cy - 140, cx + 350, cy + 140

        # Close button at top right of dialog (x: dx2 - 40 .. dx2 - 10, y: dy1 + 10 .. dy1 + 35)
        if (dx2 - 45) <= x <= (dx2 - 5) and (dy1 + 5) <= y <= (dy1 + 35):
            self.active_dialog = None
            return True

        # Click outside dialog closes it
        if not (dx1 <= x <= dx2 and dy1 <= y <= dy2):
            self.active_dialog = None
            return True

        # Handle specific dialog options
        rcp = self.camera.rcp
        if self.active_dialog == "FPS":
            items = self.FPS_OPTIONS
            clicked_idx = self._get_grid_clicked_index(x, y, dx1 + 30, dy1 + 60, len(items), cols=3, item_w=200, item_h=45)
            if clicked_idx is not None and clicked_idx < len(items):
                val = items[clicked_idx]
                rcp.state[RcpProtocol.PARAM_FPS] = val
                self.camera.log_system(f"[UI] Project Frame Rate set to {val} FPS")
                self.active_dialog = None
                return True

        elif self.active_dialog == "SHUTTER":
            items = self.SHUTTER_OPTIONS
            clicked_idx = self._get_grid_clicked_index(x, y, dx1 + 30, dy1 + 60, len(items), cols=5, item_w=120, item_h=45)
            if clicked_idx is not None and clicked_idx < len(items):
                val = items[clicked_idx]
                rcp.state[RcpProtocol.PARAM_SHUTTER] = val
                self.camera.log_system(f"[UI] Shutter Speed set to {val}")
                self.active_dialog = None
                return True

        elif self.active_dialog == "IRIS":
            lm = getattr(self.camera, "lens_mount", None)
            if lm and lm.attached_lens:
                stops = lm.get_available_aperture_stops()
                items = [f"{s:.1f}" for s in stops]
            else:
                items = self.IRIS_OPTIONS
            clicked_idx = self._get_grid_clicked_index(x, y, dx1 + 30, dy1 + 60, len(items), cols=5, item_w=120, item_h=45)
            if clicked_idx is not None and clicked_idx < len(items):
                val_f = float(items[clicked_idx])
                if lm:
                    lm.set_aperture(val_f)
                else:
                    rcp.state["aperture"] = f"f/{val_f:.1f}"
                self.active_dialog = None
                return True

        elif self.active_dialog == "ISO":
            items = self.ISO_OPTIONS
            clicked_idx = self._get_grid_clicked_index(x, y, dx1 + 30, dy1 + 60, len(items), cols=5, item_w=120, item_h=45)
            if clicked_idx is not None and clicked_idx < len(items):
                val = items[clicked_idx]
                rcp.state[RcpProtocol.PARAM_ISO] = val
                self.camera.log_system(f"[UI] Sensor ISO set to {val}")
                self.active_dialog = None
                return True

        elif self.active_dialog == "FORMAT":
            items = self.FORMAT_OPTIONS
            clicked_idx = self._get_grid_clicked_index(x, y, dx1 + 30, dy1 + 60, len(items), cols=3, item_w=200, item_h=45)
            if clicked_idx is not None and clicked_idx < len(items):
                val = items[clicked_idx]
                rcp.state[RcpProtocol.PARAM_FORMAT] = val
                self.camera.log_system(f"[UI] Recording Format set to {val}")
                self.active_dialog = None
                return True

        elif self.active_dialog == "MEDIA":
            # Check Format SSD button: x: cx - 180 .. cx - 20, y: dy2 - 55 .. dy2 - 15
            if (cx - 180) <= x <= (cx - 20) and (dy2 - 55) <= y <= (dy2 - 15):
                if hasattr(self.camera, "recorder"):
                    self.camera.recorder.format_media()
                else:
                    self.camera.log_system("[MEDIA] Formatting RED MINI-MAG 512GB (R3D FAT32/UDF)...")
                self.active_dialog = None
                return True
            # Check Eject Media button: x: cx + 20 .. cx + 180, y: dy2 - 55 .. dy2 - 15
            if (cx + 20) <= x <= (cx + 180) and (dy2 - 55) <= y <= (dy2 - 15):
                if hasattr(self.camera, "recorder"):
                    self.camera.recorder.eject_media()
                else:
                    self.camera.log_system("[MEDIA] RED MINI-MAG unmounted and ejected safely.")
                self.active_dialog = None
                return True

        elif self.active_dialog == "AUDIO":
            # Check Audio toggles or close
            self.active_dialog = None
            return True

        return True

    def _get_grid_clicked_index(self, x: int, y: int, start_x: int, start_y: int, count: int, cols: int, item_w: int, item_h: int) -> Optional[int]:
        """Helper to find which grid item was clicked."""
        pad_x, pad_y = 12, 10
        for i in range(count):
            row = i // cols
            col = i % cols
            bx = start_x + col * (item_w + pad_x)
            by = start_y + row * (item_h + pad_y)
            if bx <= x <= (bx + item_w) and by <= y <= (by + item_h):
                return i
        return None

    def _get_menu_node(self, path: List[str]):
        """Traverse DSMC2_MENU_TREE along path."""
        if not path or path == ["Menu"]:
            return self.DSMC2_MENU_TREE
        curr = self.DSMC2_MENU_TREE
        for seg in path[1:]:
            if isinstance(curr, dict) and seg in curr:
                curr = curr[seg]
            else:
                return None
        return curr

    def _get_current_items(self) -> List[str]:
        """Return list of item strings for current menu path."""
        node = self._get_menu_node(self.menu_path)
        if isinstance(node, dict):
            return list(node.keys())
        elif isinstance(node, list):
            return node
        return []

    def _get_grid_layout(self, count: int):
        """Compute optimal grid dimensions based on item count."""
        if count <= 4:
            return 4, 200, 52, 24, 20, 70, 110
        elif count <= 8:
            return 4, 200, 52, 24, 20, 70, 80
        elif count <= 10:
            return 5, 170, 48, 18, 16, 50, 75
        else:
            return 4, 200, 44, 24, 12, 70, 65

    def _get_grid_clicked_index_custom(self, x: int, y: int, start_x: int, start_y: int, count: int, cols: int, item_w: int, item_h: int, pad_x: int, pad_y: int) -> Optional[int]:
        for i in range(count):
            row = i // cols
            col = i % cols
            bx = start_x + col * (item_w + pad_x)
            by = start_y + row * (item_h + pad_y)
            if bx <= x <= (bx + item_w) and by <= y <= (by + item_h):
                return i
        return None

    def _handle_menu_touch(self, x: int, y: int) -> bool:
        """Handle touches inside the full authentic DSMC2 system menu."""
        # Top breadcrumb bar buttons
        # [ ... ] Home: x: 775..835, y: 5..40
        if 775 <= x <= 835 and 5 <= y <= 40:
            self.menu_path = ["Menu"]
            self.camera.log_system("[UI] Menu reset to root (Home)")
            return True

        # [ Back ]: x: 840..915, y: 5..40
        if 840 <= x <= 915 and 5 <= y <= 40:
            if len(self.menu_path) > 1:
                self.menu_path.pop()
                self.camera.log_system(f"[UI] Menu Back to: {' > '.join(self.menu_path)}")
            else:
                self.is_menu_open = False
                self.camera.log_system("[UI] Menu Closed")
            return True

        # [ Close ]: x: 920..1015, y: 5..40
        if 920 <= x <= 1015 and 5 <= y <= 40:
            self.is_menu_open = False
            self.camera.log_system("[UI] Menu Closed")
            return True

        # Breadcrumbs bar click on left (y: 5..40, x < 770)
        if 5 <= y <= 40 and x < 770:
            if len(self.menu_path) > 1:
                self.menu_path.pop()
            return True

        # Backward compatibility check for tab touch in test_redui.py
        if x <= 200 and 110 <= y <= 350:
            tab_y = 50 + 60
            for tab in self.MENU_TABS:
                if tab_y <= y <= (tab_y + 40):
                    self.menu_active_tab = tab
                    self.menu_path = ["Menu", tab]
                    self.camera.log_system(f"[UI] Navigated to Menu Tab: {tab}")
                    return True
                tab_y += 46

        if self.menu_active_tab == "OVERLAYS" and (300 <= x <= 540 and 110 <= y <= 160):
            self.toggle_false_color()
            return True

        # Special view: MEDIA Device buttons
        if (self.menu_path == ["Menu", "MEDIA", "Device"] or (len(self.menu_path) == 2 and self.menu_path[1] == "MEDIA")) and 390 <= y <= 445:
            cx = self.WIDTH // 2
            if (cx - 180) <= x <= (cx - 20):
                if hasattr(self.camera, "recorder"):
                    self.camera.recorder.format_media()
                else:
                    self.camera.log_system("[MEDIA] RED MINI-MAG 512GB formatted (UDF/FAT32).")
                return True
            if (cx + 20) <= x <= (cx + 180):
                if hasattr(self.camera, "recorder"):
                    self.camera.recorder.eject_media()
                else:
                    self.camera.log_system("[MEDIA] RED MINI-MAG unmounted and ejected safely.")
                return True

        # Handle button grid items
        items = self._get_current_items()
        if not items:
            return True

        cols, item_w, item_h, pad_x, pad_y, start_x, start_y = self._get_grid_layout(len(items))
        clicked_idx = self._get_grid_clicked_index_custom(x, y, start_x, start_y, len(items), cols, item_w, item_h, pad_x, pad_y)

        if clicked_idx is not None and clicked_idx < len(items):
            chosen = items[clicked_idx]
            self._handle_menu_item_selected(chosen)
            return True

        return True

    def _handle_menu_item_selected(self, item: str):
        """Process item click: drill down into submenu or execute terminal option."""
        test_path = self.menu_path + [item]
        node = self._get_menu_node(test_path)
        if node is not None:
            self.menu_path.append(item)
            if item in self.MENU_TABS:
                self.menu_active_tab = item
            self.camera.log_system(f"[UI] Navigated to Menu: {' > '.join(self.menu_path)}")
            return

        rcp = self.camera.rcp
        last_cat = self.menu_path[-1]

        if last_cat == "Frame Rate" or item in self.FPS_OPTIONS:
            val = item.replace(" FPS", "").strip()
            rcp.state[RcpProtocol.PARAM_FPS] = val
            self.camera.log_system(f"[UI] Project Frame Rate set to {val} FPS")

        elif last_cat == "Exposure" or item in self.SHUTTER_OPTIONS:
            rcp.state[RcpProtocol.PARAM_SHUTTER] = item
            self.camera.log_system(f"[UI] Shutter Speed set to {item}")

        elif last_cat == "Format" or item in self.FORMAT_OPTIONS:
            rcp.state[RcpProtocol.PARAM_FORMAT] = item
            self.camera.log_system(f"[UI] Recording Format set to {item}")

        elif last_cat == "ISO" or item in self.ISO_OPTIONS:
            val = item.replace("ISO ", "").strip()
            rcp.state[RcpProtocol.PARAM_ISO] = val
            self.camera.log_system(f"[UI] Sensor ISO set to {val}")

        elif last_cat == "WHITE BALANCE":
            if item == "Auto WB":
                self.camera.log_system("[UI] Executed Auto White Balance calibration (5600K 0 Tint)")
            else:
                rcp.state["color_temperature"] = item
                self.camera.log_system(f"[UI] Color Temperature set to {item}")

        elif last_cat == "3D LUT":
            self.active_lut = item
            self.camera.log_system(f"[UI] 3D LUT changed to: {item}")

        elif last_cat == "GUIDES":
            self.guide_mode = item
            self.camera.log_system(f"[UI] Frame Guides set to {item}")

        elif last_cat == "TOOLS":
            if "False Color" in item:
                self.toggle_false_color()
            elif "Magnify" in item:
                self.toggle_magnify()
            elif "Zebras" in item:
                self.toggle_zebras()
            elif "Horizon" in item:
                self.camera.log_system("[UI] Horizon Level overlay toggled")
            elif "RAW" in item:
                self.camera.log_system("[UI] RAW exposure view toggled")

        elif "Format" in item:
            if hasattr(self.camera, "recorder"):
                self.camera.recorder.format_media()
            else:
                self.camera.log_system("[MEDIA] RED MINI-MAG 512GB formatted (UDF/FAT32).")

        elif "Eject" in item:
            if hasattr(self.camera, "recorder"):
                self.camera.recorder.eject_media()
            else:
                self.camera.log_system("[MEDIA] RED MINI-MAG unmounted and ejected safely.")

        elif "Save Log" in item:
            rec = getattr(self.camera, "recorder", None)
            if rec and rec.is_media_mounted:
                log_p = os.path.join(self.camera.workspace_root, "persistent_store", "media", rec.current_reel_id, "camera_log.txt")
                os.makedirs(os.path.dirname(log_p), exist_ok=True)
                with open(log_p, "w") as f:
                    f.write("\n".join(self.camera.get_serial_logs()))
                self.camera.log_system(f"[MAINTENANCE] Camera logs saved to RED MINI-MAG: {rec.current_reel_id}/camera_log.txt")
            else:
                self.camera.log_system("[MAINTENANCE] System logs exported to flash memory.")

        elif "Calibrate" in item or "Black Shading" in item:
            self.camera.log_system("[CALIBRATION] Sensor Black Shading (T/E) calibrated. Calibration map applied.")

        elif "AF Sweep" in item:
            lm = getattr(self.camera, "lens_mount", None)
            if lm:
                lm.run_autofocus_sweep()

        elif "Reset Defaults" in item:
            rcp.state[RcpProtocol.PARAM_FPS] = "23.98"
            rcp.state[RcpProtocol.PARAM_SHUTTER] = "1/48"
            rcp.state[RcpProtocol.PARAM_ISO] = "800"
            rcp.state[RcpProtocol.PARAM_FORMAT] = "5K 16:9"
            self.camera.log_system("[SYSTEM] Restored DSMC2 settings to factory defaults.")

        else:
            self.camera.log_system(f"[UI] Selected menu option: {item}")

    # Physical Buttons & D-Pad Router
    def handle_button_press(self, button_name: str) -> bool:
        """Handle physical camera button or D-pad press."""
        if button_name == "MENU":
            self.toggle_menu()
            return True
        elif button_name == "1:1 MAG":
            self.toggle_magnify()
            return True
        elif button_name == "USER 1":
            self.toggle_false_color()
            return True
        elif button_name == "USER 2":
            self.toggle_zebras()
            return True

        # D-pad controls for open menu / dialog
        if self.is_menu_open:
            if button_name == "NAV UP":
                curr_idx = self.MENU_TABS.index(self.menu_active_tab) if self.menu_active_tab in self.MENU_TABS else 0
                self.menu_active_tab = self.MENU_TABS[(curr_idx - 1) % len(self.MENU_TABS)]
                self.menu_path = ["Menu", self.menu_active_tab]
                return True
            elif button_name == "NAV DOWN":
                curr_idx = self.MENU_TABS.index(self.menu_active_tab) if self.menu_active_tab in self.MENU_TABS else 0
                self.menu_active_tab = self.MENU_TABS[(curr_idx + 1) % len(self.MENU_TABS)]
                self.menu_path = ["Menu", self.menu_active_tab]
                return True
            elif button_name == "NAV LEFT":
                if len(self.menu_path) > 1:
                    self.menu_path.pop()
                    self.menu_active_tab = self.menu_path[-1] if len(self.menu_path) > 1 else "SETTINGS"
                else:
                    self.is_menu_open = False
                return True
            elif button_name == "NAV ENTER":
                if self.menu_active_tab == "OVERLAYS":
                    self.toggle_false_color()
                return True
                # Toggle current tab tool
                if self.menu_active_tab == "OVERLAYS":
                    self.toggle_false_color()
                return True

        elif self.active_dialog:
            if button_name in ("NAV LEFT", "NAV ENTER"):
                self.active_dialog = None
                return True

        return False

    # Master Rendering Pipeline
    def render(self, base_image: Image.Image) -> Image.Image:
        """
        Compose HUD, exposure tools, dialogs, menu, and touch reticle onto base frame.
        """
        img = base_image.copy()

        # 1. Apply 1:1 Magnify crop if enabled
        if self.is_magnify_on:
            cx, cy = self.WIDTH // 2, self.HEIGHT // 2
            crop_box = (cx - 256, cy - 144, cx + 256, cy + 144)
            cropped = img.crop(crop_box)
            img = cropped.resize((self.WIDTH, self.HEIGHT), Image.Resampling.BILINEAR)

        # 2. Apply False Color exposure overlay if enabled
        if self.is_false_color_on:
            img = self._apply_false_color(img)

        # 3. Apply Zebras highlight overlay if enabled
        if self.is_zebras_on:
            img = self._apply_zebras(img)

        draw = ImageDraw.Draw(img)

        # 4. Draw Frame Guides
        self._draw_guides(draw)

        # 5. Draw Focus Reticle
        if self.focus_point and not self.is_menu_open and not self.active_dialog:
            fx, fy = self.focus_point
            draw.rectangle([(fx - 24, fy - 24), (fx + 24, fy + 24)], outline=(0, 255, 120), width=1)
            draw.line([(fx - 12, fy), (fx + 12, fy)], fill=(0, 255, 120), width=1)
            draw.line([(fx, fy - 12), (fx, fy + 12)], fill=(0, 255, 120), width=1)

        # 6. Draw Quick Selection Dialog (if open)
        if self.active_dialog:
            self._draw_dialog(img, draw)

        # 7. Draw Full DSMC2 Menu (if open)
        if self.is_menu_open:
            self._draw_menu(img, draw)

        # 8. Draw Touch Feedback Reticle (if touch was active in last 450ms)
        now = time.time()
        if self.last_touch_pos and (self.is_touching or (now - self.last_touch_time < 0.45)):
            tx, ty = self.last_touch_pos
            alpha_ratio = 1.0 if self.is_touching else max(0.0, 1.0 - (now - self.last_touch_time) / 0.45)
            self._draw_touch_reticle(draw, tx, ty, alpha_ratio)

        # 9. Active Tool Badges (Magnify / False Color / Zebras)
        self._draw_tool_badges(draw)

        return img

    def _apply_false_color(self, img: Image.Image) -> Image.Image:
        """Apply false color exposure map based on pixel luminance (C-accelerated palette)."""
        gray = img.convert("L")
        gray.putpalette(self._false_color_palette)
        return gray.convert("RGB")

    def _apply_zebras(self, img: Image.Image) -> Image.Image:
        """Draw diagonal zebra stripes on highlights > 95% IRE (luminance > 240) using fast mask composite."""
        gray = img.convert("L")
        mask = gray.point(lambda p: 255 if p > 235 else 0, mode="1")
        res = img.copy()
        res.paste(self._zebra_pattern, (0, 0), mask)
        return res

    def _draw_guides(self, draw: ImageDraw.ImageDraw):
        """Draw authentic cinema frame lines."""
        if self.guide_mode == "2.39:1":
            # 2.39:1 aspect ratio inside 16:9
            guide_h = int(self.WIDTH / 2.39)
            top_y = (self.HEIGHT - guide_h) // 2
            bot_y = top_y + guide_h
            draw.line([(0, top_y), (self.WIDTH, top_y)], fill=(200, 200, 200), width=1)
            draw.line([(0, bot_y), (self.WIDTH, bot_y)], fill=(200, 200, 200), width=1)
            # Corner ticks
            draw.line([(100, top_y - 8), (100, top_y + 8)], fill=(255, 255, 255), width=1)
            draw.line([(self.WIDTH - 100, top_y - 8), (self.WIDTH - 100, top_y + 8)], fill=(255, 255, 255), width=1)
        elif self.guide_mode == "1.85:1":
            guide_h = int(self.WIDTH / 1.85)
            top_y = (self.HEIGHT - guide_h) // 2
            bot_y = top_y + guide_h
            draw.line([(0, top_y), (self.WIDTH, top_y)], fill=(180, 180, 180), width=1)
            draw.line([(0, bot_y), (self.WIDTH, bot_y)], fill=(180, 180, 180), width=1)

    def _draw_tool_badges(self, draw: ImageDraw.ImageDraw):
        """Draw active indicator badges for 1:1 Mag, False Color, or Zebras."""
        bx = 20
        by = 50
        if self.is_magnify_on:
            draw.rectangle([(bx, by), (bx + 110, by + 24)], fill=(220, 180, 0), outline=(255, 255, 255), width=1)
            draw.text((bx + 10, by + 4), "1:1 MAGNIFY", fill=(0, 0, 0))
            by += 30
        if self.is_false_color_on:
            draw.rectangle([(bx, by), (bx + 120, by + 24)], fill=(0, 180, 80), outline=(255, 255, 255), width=1)
            draw.text((bx + 8, by + 4), "FALSE COLOR", fill=(0, 0, 0))
            # Draw False Color IRE scale on right edge
            self._draw_false_color_scale(draw)
            by += 30
        if self.is_zebras_on:
            draw.rectangle([(bx, by), (bx + 100, by + 24)], fill=(200, 30, 30), outline=(255, 255, 255), width=1)
            draw.text((bx + 12, by + 4), "ZEBRAS 95%", fill=(255, 255, 255))

    def _draw_false_color_scale(self, draw: ImageDraw.ImageDraw):
        """Draw authentic vertical exposure calibration gradient on right side."""
        sx = self.WIDTH - 42
        sy = 60
        scale_h = 400
        labels = [
            (100, (255, 255, 0), "100+"),
            (98, (240, 10, 10), "98"),
            (80, (250, 170, 0), "80"),
            (56, (240, 110, 170), "56"),
            (42, (0, 220, 0), "42"),
            (18, (20, 180, 200), "18"),
            (8, (20, 60, 220), "8"),
            (0, (140, 20, 220), "0")
        ]
        block_h = scale_h // len(labels)
        for idx, (ire, color, txt) in enumerate(labels):
            y1 = sy + idx * block_h
            y2 = y1 + block_h - 2
            draw.rectangle([(sx, y1), (sx + 18, y2)], fill=color, outline=(0, 0, 0))
            draw.text((sx + 22, y1 + 2), txt, fill=(240, 240, 240))

    def _draw_touch_reticle(self, draw: ImageDraw.ImageDraw, x: int, y: int, alpha: float):
        """Draw high-visibility touch feedback reticle and coordinate readout."""
        # Outer ring
        draw.ellipse([(x - 22, y - 22), (x + 22, y + 22)], outline=(255, 60, 60), width=2)
        # Inner ring
        draw.ellipse([(x - 14, y - 14), (x + 14, y + 14)], outline=(255, 255, 255), width=2)
        # Center dot
        draw.ellipse([(x - 3, y - 3), (x + 3, y + 3)], fill=(255, 255, 255))
        # Crosshair ticks
        draw.line([(x - 30, y), (x - 24, y)], fill=(255, 60, 60), width=2)
        draw.line([(x + 24, y), (x + 30, y)], fill=(255, 60, 60), width=2)
        draw.line([(x, y - 30), (x, y - 24)], fill=(255, 60, 60), width=2)
        draw.line([(x, y + 24), (x, y + 30)], fill=(255, 60, 60), width=2)
        # Touch tooltip
        tooltip_x = min(self.WIDTH - 130, max(10, x + 26))
        tooltip_y = max(10, y - 16)
        draw.rectangle([(tooltip_x, tooltip_y), (tooltip_x + 115, tooltip_y + 22)], fill=(20, 20, 20), outline=(255, 60, 60), width=1)
        draw.text((tooltip_x + 6, tooltip_y + 4), f"TOUCH ({x}, {y})", fill=(255, 255, 255))

    def _draw_dialog(self, img: Image.Image, draw: ImageDraw.ImageDraw):
        """Render quick-selection popup dialog."""
        cx, cy = self.WIDTH // 2, self.HEIGHT // 2
        dx1, dy1, dx2, dy2 = cx - 350, cy - 140, cx + 350, cy + 140

        # Translucent backdrop
        draw.rectangle([(dx1, dy1), (dx2, dy2)], fill=(18, 18, 18), outline=(220, 20, 20), width=2)
        # Header bar
        draw.rectangle([(dx1, dy1), (dx2, dy1 + 40)], fill=(32, 32, 32))
        draw.text((dx1 + 20, dy1 + 12), f"RED DSMC2  |  SELECT {self.active_dialog}", fill=(240, 240, 240))
        # Close button [ X ]
        draw.rectangle([(dx2 - 40, dy1 + 8), (dx2 - 10, dy1 + 32)], fill=(60, 20, 20), outline=(200, 40, 40), width=1)
        draw.text((dx2 - 30, dy1 + 11), "X", fill=(255, 255, 255))

        rcp = self.camera.rcp

        if self.active_dialog == "FPS":
            items = self.FPS_OPTIONS
            curr_val = rcp.state.get(RcpProtocol.PARAM_FPS, "24.00")
            self._draw_grid_buttons(draw, dx1 + 30, dy1 + 60, items, curr_val, cols=3, item_w=200, item_h=45, unit="FPS")

        elif self.active_dialog == "SHUTTER":
            items = self.SHUTTER_OPTIONS
            curr_val = rcp.state.get(RcpProtocol.PARAM_SHUTTER, "1/48")
            self._draw_grid_buttons(draw, dx1 + 30, dy1 + 60, items, curr_val, cols=5, item_w=120, item_h=45, unit="")

        elif self.active_dialog == "IRIS":
            lm = getattr(self.camera, "lens_mount", None)
            if lm and lm.attached_lens:
                stops = lm.get_available_aperture_stops()
                items = [f"{s:.1f}" for s in stops]
                curr_val = f"{lm.current_aperture:.1f}"
                lens_desc = f"{lm.attached_lens.model} ({lm.mount_type})"
            else:
                items = self.IRIS_OPTIONS
                curr_val = "2.8"
                lens_desc = "Standard Lens Scale"
            draw.text((dx1 + 30, dy1 + 42), f"LENS: {lens_desc}", fill=(180, 180, 180))
            self._draw_grid_buttons(draw, dx1 + 30, dy1 + 60, items, curr_val, cols=5, item_w=120, item_h=45, unit="f/")

        elif self.active_dialog == "ISO":
            items = self.ISO_OPTIONS
            curr_val = rcp.state.get(RcpProtocol.PARAM_ISO, "800")
            self._draw_grid_buttons(draw, dx1 + 30, dy1 + 60, items, curr_val, cols=5, item_w=120, item_h=45, unit="ISO")

        elif self.active_dialog == "FORMAT":
            items = self.FORMAT_OPTIONS
            curr_val = rcp.state.get(RcpProtocol.PARAM_FORMAT, "5K 16:9")
            self._draw_grid_buttons(draw, dx1 + 30, dy1 + 60, items, curr_val, cols=3, item_w=200, item_h=45, unit="")

        elif self.active_dialog == "MEDIA":
            rec = getattr(self.camera, "recorder", None)
            reel_str = rec.current_reel_id if rec else "A001"
            status_str = "Mounted (Read/Write)" if (not rec or rec.is_media_mounted) else "Unmounted / Ejected"
            used_gb = (rec.used_space_mb / 1024.0) if rec else 112.0
            free_gb = (rec.free_space_mb / 1024.0) if rec else 400.0
            rem_pct = rec.media_remaining_percent if rec else 78
            clip_cnt = len(rec.list_clips()) if rec else 14

            draw.text((dx1 + 30, dy1 + 55), "MEDIA DEVICE: RED MINI-MAG 512GB", fill=(255, 255, 255))
            draw.text((dx1 + 30, dy1 + 80), f"STATUS: {status_str}  |  FORMAT: FAT32 / UDF v2.50", fill=(180, 180, 180))
            draw.text((dx1 + 30, dy1 + 105), f"CAPACITY: 512 GB  |  USED: {used_gb:.1f} GB ({100-rem_pct}%)  |  FREE: {free_gb:.1f} GB ({rem_pct}%)", fill=(180, 180, 180))
            draw.text((dx1 + 30, dy1 + 130), f"REEL: {reel_str}  |  CLIPS: {clip_cnt} clips recorded", fill=(180, 180, 180))

            # Capacity progress bar (Crash-Proof)
            bx1, by1, bx2, by2 = dx1 + 30, dy1 + 160, dx2 - 30, dy1 + 185
            draw.rectangle([(bx1, by1), (bx2, by2)], outline=(80, 80, 80), width=1)
            used_ratio = (rec.used_space_mb / rec.total_capacity_mb) if rec else 0.22
            bar_w = bx2 - bx1 - 2
            used_w = int(bar_w * max(0.0, min(1.0, used_ratio)))
            if used_w > 0:
                draw.rectangle([(bx1 + 1, by1 + 1), (bx1 + 1 + used_w, by2 - 1)], fill=(0, 180, 220))
            if used_w < bar_w:
                draw.rectangle([(bx1 + 1 + used_w, by1 + 1), (bx2 - 1, by2 - 1)], fill=(0, 200, 80))

            # Action buttons
            # [ FORMAT MEDIA ]
            draw.rectangle([(cx - 180, dy2 - 55), (cx - 20, dy2 - 15)], fill=(120, 20, 20), outline=(220, 40, 40), width=1)
            draw.text((cx - 150, dy2 - 42), "FORMAT MEDIA", fill=(255, 255, 255))
            # [ EJECT MEDIA ]
            draw.rectangle([(cx + 20, dy2 - 55), (cx + 180, dy2 - 15)], fill=(40, 40, 40), outline=(100, 100, 100), width=1)
            draw.text((cx + 55, dy2 - 42), "EJECT MEDIA", fill=(220, 220, 220))

        elif self.active_dialog == "AUDIO":
            draw.text((dx1 + 30, dy1 + 55), "AUDIO SYSTEM STATUS: 2 Channels 24-bit 48kHz PCM", fill=(255, 255, 255))
            draw.text((dx1 + 30, dy1 + 85), "CH 1: Camera Mic Left   |  GAIN: +18dB  |  PEAK: -18 dBFS", fill=(180, 180, 180))
            draw.text((dx1 + 30, dy1 + 115), "CH 2: Camera Mic Right  |  GAIN: +18dB  |  PEAK: -18 dBFS", fill=(180, 180, 180))
            draw.text((dx1 + 30, dy1 + 145), "HEADPHONE VOLUME: 80%   |  PHANTOM +48V: OFF", fill=(180, 180, 180))
            # Dismiss button
            draw.rectangle([(cx - 70, dy2 - 55), (cx + 70, dy2 - 15)], fill=(40, 40, 40), outline=(100, 100, 100), width=1)
            draw.text((cx - 25, dy2 - 42), "CLOSE", fill=(255, 255, 255))

    def _draw_grid_buttons(self, draw: ImageDraw.ImageDraw, start_x: int, start_y: int, items: List[str], current: str, cols: int, item_w: int, item_h: int, unit: str):
        """Draw interactive grid of selectable parameter buttons."""
        pad_x, pad_y = 12, 10
        for i, val in enumerate(items):
            row = i // cols
            col = i % cols
            bx = start_x + col * (item_w + pad_x)
            by = start_y + row * (item_h + pad_y)

            is_selected = (val == current)
            bg_color = (180, 25, 25) if is_selected else (35, 35, 35)
            border_color = (255, 60, 60) if is_selected else (70, 70, 70)
            text_color = (255, 255, 255) if is_selected else (200, 200, 200)

            draw.rectangle([(bx, by), (bx + item_w, by + item_h)], fill=bg_color, outline=border_color, width=2 if is_selected else 1)

            display_txt = f"{val} {unit}".strip()
            draw.text((bx + 16, by + 13), display_txt, fill=text_color)
            if is_selected:
                draw.text((bx + item_w - 24, by + 13), "●", fill=(255, 255, 255))

    def _draw_menu(self, img: Image.Image, draw: ImageDraw.ImageDraw):
        """Render genuine full DSMC2 system menu on the monitor."""
        # Dark translucent overlay over frame (middle area y: 44..520, keeping bottom status bar visible!)
        draw.rectangle([(0, 44), (self.WIDTH, 520)], fill=(14, 14, 14))

        # Top Breadcrumb Bar (y: 0..44)
        draw.rectangle([(0, 0), (self.WIDTH, 44)], fill=(40, 40, 40))
        draw.line([(0, 44), (self.WIDTH, 44)], fill=(65, 65, 65), width=1)
        # Red left accent
        draw.rectangle([(0, 0), (4, 44)], fill=(220, 20, 20))

        # Breadcrumbs text
        b_txt = " > ".join(self.menu_path) + " >"
        draw.text((18, 13), b_txt, fill=(245, 245, 245), font=FONT_TITLE)

        # Right buttons: [ ... ] [ Back ] [ Close ]
        # [ ... ]
        draw.rectangle([(775, 8), (832, 36)], fill=(55, 55, 55), outline=(100, 100, 100), width=1)
        draw.text((796, 13), "...", fill=(220, 220, 220), font=FONT_BOLD)

        # [ Back ]
        draw.rectangle([(840, 8), (912, 36)], fill=(55, 55, 55), outline=(100, 100, 100), width=1)
        draw.text((858, 13), "Back", fill=(220, 220, 220), font=FONT_BOLD)

        # [ Close ]
        draw.rectangle([(920, 8), (1005, 36)], fill=(75, 20, 20), outline=(220, 40, 40), width=1)
        draw.text((945, 13), "Close", fill=(255, 255, 255), font=FONT_BOLD)

        # Content area (y: 45..520)
        # Check special screen views:
        if self.menu_path == ["Menu", "MEDIA", "Device"] or (len(self.menu_path) == 2 and self.menu_path[1] == "MEDIA"):
            self._draw_media_device_screen(draw)
            return

        if self.menu_path == ["Menu", "OVERLAYS", "TOOLS"]:
            self._draw_tools_screen(draw)
            return

        items = self._get_current_items()
        if not items:
            draw.text((100, 100), f"No items in {self.menu_path[-1]}", fill=(160, 160, 160), font=FONT_MED)
            return

        cols, item_w, item_h, pad_x, pad_y, start_x, start_y = self._get_grid_layout(len(items))

        rcp = self.camera.rcp
        curr_fps = rcp.state.get(RcpProtocol.PARAM_FPS, "23.98")
        curr_shutter = rcp.state.get(RcpProtocol.PARAM_SHUTTER, "1/48")
        curr_iso = rcp.state.get(RcpProtocol.PARAM_ISO, "800")
        curr_fmt = rcp.state.get(RcpProtocol.PARAM_FORMAT, "5K 16:9")

        for i, val in enumerate(items):
            row = i // cols
            col = i % cols
            bx = start_x + col * (item_w + pad_x)
            by = start_y + row * (item_h + pad_y)

            # Check if this item is selected / active
            is_active = False
            if val == curr_fps or val == f"{curr_fps} FPS":
                is_active = True
            elif val == curr_shutter:
                is_active = True
            elif val == curr_iso or val == f"ISO {curr_iso}":
                is_active = True
            elif val == curr_fmt:
                is_active = True
            elif val == self.active_lut:
                is_active = True
            elif val == self.guide_mode:
                is_active = True
            elif val == "False Color" and self.is_false_color_on:
                is_active = True
            elif val == "Magnify 1:1" and self.is_magnify_on:
                is_active = True
            elif val == "Zebras 95%" and self.is_zebras_on:
                is_active = True

            # Has children submenus?
            child_node = self._get_menu_node(self.menu_path + [val])
            has_children = (child_node is not None)

            bg_color = (185, 20, 20) if is_active else (38, 38, 38)
            border_color = (255, 60, 60) if is_active else (75, 75, 75)
            text_color = (255, 255, 255) if is_active else (225, 225, 225)

            draw.rectangle([(bx, by), (bx + item_w, by + item_h)], fill=bg_color, outline=border_color, width=2 if is_active else 1)
            draw.text((bx + 14, by + 14), val, fill=text_color, font=FONT_MED)

            if has_children:
                draw.text((bx + item_w - 20, by + 13), ">", fill=(160, 160, 160), font=FONT_MED)
            elif is_active:
                draw.text((bx + item_w - 22, by + 13), "●", fill=(255, 255, 255), font=FONT_MED)

    def _draw_media_device_screen(self, draw: ImageDraw.ImageDraw):
        """Draw authentic RED MINI-MAG Device Screen."""
        rec = getattr(self.camera, "recorder", None)
        reel_str = rec.current_reel_id if rec else "A001"
        status_str = "Mounted (Read/Write)" if (not rec or rec.is_media_mounted) else "Unmounted / Ejected"
        used_gb = (rec.used_space_mb / 1024.0) if rec else 0.0
        free_gb = (rec.free_space_mb / 1024.0) if rec else 512.0
        rem_pct = rec.media_remaining_percent if rec else 100
        clip_cnt = len(rec.list_clips()) if rec else 0

        draw.text((80, 80), "MEDIA DEVICE: RED MINI-MAG 512GB (Port 1)", fill=(255, 255, 255), font=FONT_TITLE)
        draw.text((80, 120), f"STATUS: {status_str}   |   FILE SYSTEM: FAT32 / RED UDF v2.50", fill=(190, 190, 190), font=FONT_MED)
        draw.text((80, 155), f"CAPACITY: 512.0 GB   |   USED: {used_gb:.2f} GB ({100-rem_pct}%)   |   FREE: {free_gb:.2f} GB ({rem_pct}%)", fill=(190, 190, 190), font=FONT_MED)
        draw.text((80, 190), f"ACTIVE REEL: {reel_str}   |   CLIPS RECORDED: {clip_cnt} clips", fill=(190, 190, 190), font=FONT_MED)

        # Capacity Progress Bar (CRASH-PROOF!)
        bx1, by1, bx2, by2 = 80, 240, 944, 275
        draw.rectangle([(bx1, by1), (bx2, by2)], outline=(90, 90, 90), width=1)
        used_ratio = (rec.used_space_mb / rec.total_capacity_mb) if rec else 0.0
        bar_w = bx2 - bx1 - 2
        used_w = int(bar_w * max(0.0, min(1.0, used_ratio)))
        if used_w > 0:
            draw.rectangle([(bx1 + 1, by1 + 1), (bx1 + 1 + used_w, by2 - 1)], fill=(0, 180, 220))
        if used_w < bar_w:
            draw.rectangle([(bx1 + 1 + used_w, by1 + 1), (bx2 - 1, by2 - 1)], fill=(0, 200, 80))

        draw.text((80, 290), f"Storage Health: 100%   •   Wear Level: 0.1%   •   Transfer Rate: Up to 300 MB/s", fill=(140, 140, 140), font=FONT_SM)

        # Action Buttons
        cx = self.WIDTH // 2
        # [ FORMAT MEDIA ]
        draw.rectangle([(cx - 180, 390), (cx - 20, 445)], fill=(140, 20, 20), outline=(220, 50, 50), width=2)
        draw.text((cx - 150, 408), "FORMAT MEDIA", fill=(255, 255, 255), font=FONT_BOLD)

        # [ EJECT MEDIA ]
        draw.rectangle([(cx + 20, 390), (cx + 180, 445)], fill=(45, 45, 45), outline=(100, 100, 100), width=1)
        draw.text((cx + 55, 408), "EJECT MEDIA", fill=(220, 220, 220), font=FONT_BOLD)

    def _draw_tools_screen(self, draw: ImageDraw.ImageDraw):
        """Draw authentic DSMC2 Overlays > Tools Toolbar & Options."""
        draw.text((70, 75), "OVERLAY TOOLS & EXPOSURE ASSIST", fill=(255, 255, 255), font=FONT_TITLE)

        tools = [
            ("False Color", self.is_false_color_on),
            ("Magnify 1:1", self.is_magnify_on),
            ("Zebras 95%", self.is_zebras_on),
            ("Horizon", False),
            ("RAW View", False)
        ]
        start_x = 70
        start_y = 120
        item_w = 160
        item_h = 48
        pad_x = 20

        for i, (tool_name, active) in enumerate(tools):
            bx = start_x + i * (item_w + pad_x)
            by = start_y
            bg = (180, 20, 20) if active else (40, 40, 40)
            border = (255, 60, 60) if active else (80, 80, 80)
            draw.rectangle([(bx, by), (bx + item_w, by + item_h)], fill=bg, outline=border, width=2 if active else 1)
            draw.text((bx + 16, by + 14), tool_name, fill=(255, 255, 255), font=FONT_MED)
            if active:
                draw.text((bx + item_w - 22, by + 14), "●", fill=(255, 255, 255), font=FONT_MED)

        # False Color Mode Selection
        draw.text((70, 220), "FALSE COLOR EXPOSURE MODES:", fill=(200, 200, 200), font=FONT_BOLD)
        modes = ["Off", "Exposure", "Video", "Focus", "Edge", "Gio Scope"]
        for j, mode in enumerate(modes):
            my = 260 + j * 38
            is_cur = (self.is_false_color_on and getattr(self, "active_false_color_mode", "Focus") == mode) or (not self.is_false_color_on and mode == "Off")
            mbg = (170, 20, 20) if is_cur else (30, 30, 30)
            draw.rectangle([(70, my), (260, my + 32)], fill=mbg, outline=(255, 60, 60) if is_cur else (60, 60, 60))
            if is_cur:
                draw.text((80, my + 8), f"◄ {mode} ►", fill=(255, 255, 255), font=FONT_BOLD)
            else:
                draw.text((80, my + 8), mode, fill=(200, 200, 200), font=FONT_MED)
