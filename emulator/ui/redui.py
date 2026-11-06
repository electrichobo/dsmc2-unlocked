"""
RedUi Graphical Engine and Interactive Menu System for RED DSMC2.
Provides authentic DSMC2 HUD overlays, quick-selection dialogs (FPS, Shutter, ISO, Format),
full system menu, exposure tools (False Color, Zebras, 1:1 Mag), and touch feedback.
"""

import time
from typing import Optional, Tuple, Dict, Any, List
from PIL import Image, ImageDraw, ImageFont
from emulator.rcp.protocol import RcpProtocol


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

    MENU_TABS = ["SETTINGS", "MEDIA", "AUDIO", "OVERLAYS", "LOOK", "ABOUT"]

    def __init__(self, camera):
        self.camera = camera

        # Modal / Dialog State
        self.active_dialog: Optional[str] = None # "FPS", "SHUTTER", "ISO", "FORMAT", "MEDIA", "AUDIO"
        self.dialog_selected_index: int = 0

        # DSMC2 Main Menu State
        self.is_menu_open: bool = False
        self.menu_active_tab: str = "SETTINGS"
        self.menu_selected_index: int = 0

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

        # Build False Color LUT
        self._false_color_lut = self._build_false_color_lut()

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

    def toggle_menu(self):
        """Toggle DSMC2 system menu on/off."""
        self.is_menu_open = not self.is_menu_open
        self.active_dialog = None
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
            # STBY / REC Badge (15 <= x <= 75)
            if 15 <= x <= 85:
                self.camera.press_record()
                return True
            # Menu Button / Model Header (88 <= x <= 290)
            if 88 <= x <= 290:
                self.toggle_menu()
                return True
            # FPS Badge (310 <= x <= 430)
            if 310 <= x <= 430:
                self._open_dialog("FPS")
                return True
            # SHUTTER Badge (440 <= x <= 600)
            if 440 <= x <= 600:
                self._open_dialog("SHUTTER")
                return True
            # ISO Badge (610 <= x <= 730)
            if 610 <= x <= 730:
                self._open_dialog("ISO")
                return True
            # FORMAT Badge (740 <= x <= 910)
            if 740 <= x <= 910:
                self._open_dialog("FORMAT")
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
                self.camera.log_system("[MEDIA] Formatting RED MINI-MAG 512GB (R3D FAT32/UDF)...")
                self.camera.log_system("[MEDIA] Format completed successfully. Reel A001 initialized.")
                self.active_dialog = None
                return True
            # Check Eject Media button: x: cx + 20 .. cx + 180, y: dy2 - 55 .. dy2 - 15
            if (cx + 20) <= x <= (cx + 180) and (dy2 - 55) <= y <= (dy2 - 15):
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

    def _handle_menu_touch(self, x: int, y: int) -> bool:
        """Handle touches inside the full DSMC2 system menu."""
        # Menu frame: x: 80..944, y: 50..526
        mx1, my1, mx2, my2 = 80, 50, 944, 526

        # Close button at top right (x: mx2 - 40 .. mx2 - 10, y: my1 + 10 .. my1 + 35)
        if (mx2 - 45) <= x <= (mx2 - 5) and (my1 + 5) <= y <= (my1 + 35):
            self.is_menu_open = False
            return True

        # Click outside menu closes it
        if not (mx1 <= x <= mx2 and my1 <= y <= my2):
            self.is_menu_open = False
            return True

        # Tab navigation column on left (x: mx1 + 10 .. mx1 + 200)
        if (mx1 + 10) <= x <= (mx1 + 200):
            tab_y = my1 + 60
            for idx, tab in enumerate(self.MENU_TABS):
                if tab_y <= y <= (tab_y + 40):
                    self.menu_active_tab = tab
                    self.menu_selected_index = 0
                    self.camera.log_system(f"[UI] Navigated to Menu Tab: {tab}")
                    return True
                tab_y += 46

        # Submenu contents on right (x: mx1 + 220 .. mx2 - 20)
        rx1 = mx1 + 220
        ry = my1 + 70

        if self.menu_active_tab == "OVERLAYS":
            # Toggle False Color button (x: rx1 .. rx1 + 220, y: ry .. ry + 40)
            if rx1 <= x <= (rx1 + 240) and ry <= y <= (ry + 40):
                self.toggle_false_color()
                return True
            # Toggle Zebras button (x: rx1 .. rx1 + 220, y: ry + 50 .. ry + 90)
            if rx1 <= x <= (rx1 + 240) and (ry + 50) <= y <= (ry + 90):
                self.toggle_zebras()
                return True
            # Cycle Guides (x: rx1 .. rx1 + 240, y: ry + 100 .. ry + 140)
            if rx1 <= x <= (rx1 + 240) and (ry + 100) <= y <= (ry + 140):
                guides = ["16:9", "2.39:1", "1.85:1", "Off"]
                curr_idx = guides.index(self.guide_mode) if self.guide_mode in guides else 0
                self.guide_mode = guides[(curr_idx + 1) % len(guides)]
                self.camera.log_system(f"[UI] Frame Guides set to {self.guide_mode}")
                return True

        elif self.menu_active_tab == "LOOK":
            # Cycle LUTs (x: rx1 .. rx1 + 280, y: ry .. ry + 40)
            if rx1 <= x <= (rx1 + 280) and ry <= y <= (ry + 40):
                luts = ["RED IPP2 Cinema Standard", "Film Rec709", "Low Contrast Print"]
                curr = getattr(self, "active_lut", luts[0])
                idx = luts.index(curr) if curr in luts else 0
                self.active_lut = luts[(idx + 1) % len(luts)]
                self.camera.log_system(f"[UI] 3D LUT changed to: {self.active_lut}")
                return True

        elif self.menu_active_tab == "SETTINGS":
            # Quick format toggle
            if rx1 <= x <= (rx1 + 280) and ry <= y <= (ry + 40):
                self._open_dialog("FORMAT")
                self.is_menu_open = False
                return True

        return True

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
                curr_idx = self.MENU_TABS.index(self.menu_active_tab)
                self.menu_active_tab = self.MENU_TABS[(curr_idx - 1) % len(self.MENU_TABS)]
                return True
            elif button_name == "NAV DOWN":
                curr_idx = self.MENU_TABS.index(self.menu_active_tab)
                self.menu_active_tab = self.MENU_TABS[(curr_idx + 1) % len(self.MENU_TABS)]
                return True
            elif button_name == "NAV LEFT":
                self.is_menu_open = False
                return True
            elif button_name == "NAV ENTER":
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
        """Apply false color exposure map based on pixel luminance."""
        gray = img.convert("L")
        pixels = list(gray.getdata())
        fc_pixels = [self._false_color_lut[p] for p in pixels]
        fc_img = Image.new("RGB", img.size)
        fc_img.putdata(fc_pixels)
        return fc_img

    def _apply_zebras(self, img: Image.Image) -> Image.Image:
        """Draw diagonal zebra stripes on highlights > 95% IRE (luminance > 240)."""
        gray = img.convert("L")
        pixels = gray.load()
        draw = ImageDraw.Draw(img)
        w, h = img.size
        # Draw diagonal hatching across hot pixels
        step = 8
        for y in range(0, h, step):
            for x in range(0, w, step):
                if pixels[x, y] > 235:
                    draw.line([(x, y), (min(w - 1, x + 6), min(h - 1, y + 6))], fill=(255, 0, 0), width=2)
                    draw.line([(min(w - 1, x + 3), y), (min(w - 1, x + 9), min(h - 1, y + 6))], fill=(255, 255, 255), width=1)
        return img

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

        elif self.active_dialog == "ISO":
            items = self.ISO_OPTIONS
            curr_val = rcp.state.get(RcpProtocol.PARAM_ISO, "800")
            self._draw_grid_buttons(draw, dx1 + 30, dy1 + 60, items, curr_val, cols=5, item_w=120, item_h=45, unit="ISO")

        elif self.active_dialog == "FORMAT":
            items = self.FORMAT_OPTIONS
            curr_val = rcp.state.get(RcpProtocol.PARAM_FORMAT, "5K 16:9")
            self._draw_grid_buttons(draw, dx1 + 30, dy1 + 60, items, curr_val, cols=3, item_w=200, item_h=45, unit="")

        elif self.active_dialog == "MEDIA":
            # Media info details
            draw.text((dx1 + 30, dy1 + 55), "MEDIA DEVICE: RED MINI-MAG 512GB", fill=(255, 255, 255))
            draw.text((dx1 + 30, dy1 + 80), "STATUS: Mounted (Read/Write)  |  FORMAT: FAT32 / UDF", fill=(180, 180, 180))
            draw.text((dx1 + 30, dy1 + 105), "CAPACITY: 512 GB  |  USED: 112 GB (22%)  |  FREE: 400 GB (78%)", fill=(180, 180, 180))
            draw.text((dx1 + 30, dy1 + 130), "REEL: A001  |  CLIPS: 14 clips recorded", fill=(180, 180, 180))

            # Capacity progress bar
            bx1, by1, bx2, by2 = dx1 + 30, dy1 + 160, dx2 - 30, dy1 + 185
            draw.rectangle([(bx1, by1), (bx2, by2)], outline=(80, 80, 80), width=1)
            used_w = int((bx2 - bx1) * 0.22)
            draw.rectangle([(bx1 + 1, by1 + 1), (bx1 + used_w, by2 - 1)], fill=(0, 180, 220))
            draw.rectangle([(bx1 + used_w, by1 + 1), (bx2 - 1, by2 - 1)], fill=(0, 200, 80))

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
        """Render genuine full DSMC2 system menu."""
        mx1, my1, mx2, my2 = 80, 50, 944, 526

        # Main background container
        draw.rectangle([(mx1, my1), (mx2, my2)], fill=(16, 16, 16), outline=(60, 60, 60), width=2)

        # Header title
        draw.rectangle([(mx1, my1), (mx2, my1 + 45)], fill=(28, 28, 28))
        draw.rectangle([(mx1, my1), (mx1 + 6, my1 + 45)], fill=(220, 20, 20)) # Red left accent
        model = self.camera.vfs.env.get("CAMERA_NAME", "SCARLET-W 5K")
        draw.text((mx1 + 20, my1 + 14), f"RED DIGITAL CINEMA  |  {model}  SYSTEM MENU", fill=(240, 240, 240))

        # Close button [ X ]
        draw.rectangle([(mx2 - 40, my1 + 10), (mx2 - 10, my1 + 35)], fill=(60, 20, 20), outline=(200, 40, 40), width=1)
        draw.text((mx2 - 28, my1 + 13), "X", fill=(255, 255, 255))

        # Left Tab Navigation Column
        tab_y = my1 + 60
        for tab in self.MENU_TABS:
            is_active = (tab == self.menu_active_tab)
            t_bg = (40, 40, 40) if is_active else (22, 22, 22)
            t_border = (220, 30, 30) if is_active else (45, 45, 45)
            t_text = (255, 255, 255) if is_active else (160, 160, 160)

            draw.rectangle([(mx1 + 12, tab_y), (mx1 + 190, tab_y + 38)], fill=t_bg, outline=t_border, width=2 if is_active else 1)
            if is_active:
                draw.rectangle([(mx1 + 12, tab_y), (mx1 + 18, tab_y + 38)], fill=(220, 20, 20))
            draw.text((mx1 + 30, tab_y + 11), tab, fill=t_text)
            tab_y += 46

        # Vertical Divider
        draw.line([(mx1 + 205, my1 + 50), (mx1 + 205, my2 - 10)], fill=(50, 50, 50), width=1)

        # Right Tab Content Pane
        rx1 = mx1 + 225
        ry = my1 + 65

        if self.menu_active_tab == "SETTINGS":
            draw.text((rx1, ry), "CAMERA SETTINGS & CONFIGURATION", fill=(240, 240, 240))
            ry += 35
            rcp = self.camera.rcp
            fps = rcp.state.get(RcpProtocol.PARAM_FPS, "24.00")
            shutter = rcp.state.get(RcpProtocol.PARAM_SHUTTER, "1/48")
            iso = rcp.state.get(RcpProtocol.PARAM_ISO, "800")
            fmt = rcp.state.get(RcpProtocol.PARAM_FORMAT, "5K 16:9")

            draw.text((rx1, ry), f"• Project Frame Rate: {fps} fps  (Tap to change)", fill=(200, 200, 200))
            ry += 30
            draw.text((rx1, ry), f"• Shutter Speed / Angle: {shutter}  (Tap to change)", fill=(200, 200, 200))
            ry += 30
            draw.text((rx1, ry), f"• Sensor Sensitivity: {iso} ISO  (Tap to change)", fill=(200, 200, 200))
            ry += 30
            draw.text((rx1, ry), f"• Recording Format: {fmt}  (Tap to change)", fill=(200, 200, 200))
            ry += 30
            draw.text((rx1, ry), "• Fan Mode: Adaptive (Target 40°C)", fill=(160, 160, 160))
            ry += 30
            draw.text((rx1, ry), "• Sensor Calibrated: Black Shading OK (0x80838180)", fill=(80, 220, 80))

        elif self.menu_active_tab == "MEDIA":
            draw.text((rx1, ry), "MEDIA MANAGEMENT", fill=(240, 240, 240))
            ry += 35
            draw.text((rx1, ry), "Attached Device: RED MINI-MAG 512GB (Port 1)", fill=(200, 200, 200))
            ry += 30
            draw.text((rx1, ry), "File System: FAT32 / RED UDF v2.50", fill=(180, 180, 180))
            ry += 30
            draw.text((rx1, ry), "Space: 400 GB Free of 512 GB (78%)", fill=(0, 220, 100))
            ry += 40
            # Buttons
            draw.rectangle([(rx1, ry), (rx1 + 180, ry + 40)], fill=(120, 20, 20), outline=(220, 40, 40), width=1)
            draw.text((rx1 + 30, ry + 12), "FORMAT MEDIA", fill=(255, 255, 255))
            draw.rectangle([(rx1 + 200, ry), (rx1 + 360, ry + 40)], fill=(40, 40, 40), outline=(80, 80, 80), width=1)
            draw.text((rx1 + 235, ry + 12), "EJECT MEDIA", fill=(200, 200, 200))

        elif self.menu_active_tab == "AUDIO":
            draw.text((rx1, ry), "AUDIO CHANNELS & LEVELS", fill=(240, 240, 240))
            ry += 35
            draw.text((rx1, ry), "• Channel 1: Internal Mic L (+18dB)  [Meter: -18dB]", fill=(200, 200, 200))
            ry += 30
            draw.text((rx1, ry), "• Channel 2: Internal Mic R (+18dB)  [Meter: -18dB]", fill=(200, 200, 200))
            ry += 30
            draw.text((rx1, ry), "• Headphone Monitor Output: 80%", fill=(180, 180, 180))
            ry += 30
            draw.text((rx1, ry), "• Limiter: ON  |  Filter: 40Hz High Pass", fill=(160, 160, 160))

        elif self.menu_active_tab == "OVERLAYS":
            draw.text((rx1, ry), "DISPLAY TOOLS & OVERLAYS", fill=(240, 240, 240))
            ry += 35

            # False Color Toggle Button
            fc_bg = (0, 160, 60) if self.is_false_color_on else (40, 40, 40)
            draw.rectangle([(rx1, ry), (rx1 + 240, ry + 38)], fill=fc_bg, outline=(255, 255, 255), width=1)
            draw.text((rx1 + 20, ry + 11), f"False Color: {'[ ON ]' if self.is_false_color_on else '[ OFF ]'}", fill=(255, 255, 255))
            ry += 48

            # Zebras Toggle Button
            zb_bg = (180, 30, 30) if self.is_zebras_on else (40, 40, 40)
            draw.rectangle([(rx1, ry), (rx1 + 240, ry + 38)], fill=zb_bg, outline=(255, 255, 255), width=1)
            draw.text((rx1 + 20, ry + 11), f"Zebra Stripes 95%: {'[ ON ]' if self.is_zebras_on else '[ OFF ]'}", fill=(255, 255, 255))
            ry += 48

            # Guides Button
            draw.rectangle([(rx1, ry), (rx1 + 240, ry + 38)], fill=(40, 40, 40), outline=(100, 100, 100), width=1)
            draw.text((rx1 + 20, ry + 11), f"Frame Guides: [ {self.guide_mode} ]", fill=(220, 220, 220))
            ry += 48

            draw.text((rx1, ry), "Tip: You can also toggle False Color and Zebras via", fill=(140, 140, 140))
            draw.text((rx1, ry + 20), "physical buttons USER 1 and USER 2 below the screen.", fill=(140, 140, 140))

        elif self.menu_active_tab == "LOOK":
            draw.text((rx1, ry), "COLOR PIPELINE & 3D LOOK UP TABLES (IPP2)", fill=(240, 240, 240))
            ry += 35
            draw.text((rx1, ry), "• Color Space: REDWideGamutRGB", fill=(200, 200, 200))
            ry += 30
            draw.text((rx1, ry), "• Gamma Curve: Log3G10", fill=(200, 200, 200))
            ry += 30
            lut_name = getattr(self, "active_lut", "RED IPP2 Cinema Standard")
            draw.text((rx1, ry), f"• Active 3D LUT: {lut_name}", fill=(0, 220, 180))
            ry += 40
            # LUT Cycle Button
            draw.rectangle([(rx1, ry), (rx1 + 280, ry + 38)], fill=(40, 40, 40), outline=(0, 200, 180), width=1)
            draw.text((rx1 + 20, ry + 11), "CYCLE 3D LUT PRESETS", fill=(255, 255, 255))

        elif self.menu_active_tab == "ABOUT":
            draw.text((rx1, ry), "CAMERA INFORMATION & DIAGNOSTICS", fill=(240, 240, 240))
            ry += 35
            model = self.camera.vfs.env.get("CAMERA_NAME", "SCARLET-W 5K")
            pin = self.camera.vfs.env.get("CAMERA_PIN", "001-3829-E")
            draw.text((rx1, ry), f"• Camera Model: RED {model} S35", fill=(200, 200, 200))
            ry += 26
            draw.text((rx1, ry), "• Firmware Version: v7.4.1 (64-bit ARMv7-A DM3725 / OMAP3630)", fill=(200, 200, 200))
            ry += 26
            draw.text((rx1, ry), f"• Brain Serial PIN: {pin}", fill=(200, 200, 200))
            ry += 26
            draw.text((rx1, ry), "• RTOS Kernel: ThreadX RTOS v5.7 (Express Logic)", fill=(200, 200, 200))
            ry += 26
            draw.text((rx1, ry), "• Display: RED Touch 7.0 LCD (1024x576 @ 60Hz)", fill=(200, 200, 200))
            ry += 26
            draw.text((rx1, ry), "• Touch Controller: Atmel maXTouch mXT (I2C3 0x4B)", fill=(200, 200, 200))
            ry += 26
            draw.text((rx1, ry), "• Sensor: RED DRAGON 5120x2700 CMOS (16.5+ stops dynamic range)", fill=(200, 200, 200))
            ry += 26
            draw.text((rx1, ry), f"• Virtual CPU Instructions: {self.camera.instruction_count / 1_000_000:.1f}M", fill=(0, 255, 120))
