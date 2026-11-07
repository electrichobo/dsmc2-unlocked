"""
RED DSMC2 Web-Based Virtual Camera Studio.
Provides a modern, freeze-proof browser interface running on a dedicated local port (default: 8765).
Includes:
- Authentic 1024x576 RED Touch 7.0 LCD frame streaming (MJPEG)
- Direct touch coordinate mapping to Atmel maXTouch controller & RedUi menu system
- Physical tactile controls (Record, Power, Menu, 1:1 Mag, User keys, D-Pad)
- Real-time Developer Workbench (UART4 console, RCP inspector, ThreadX RTOS monitor, Snapshots, Faults)
"""

import os
import io
import json
import time
import socket
import zlib
import urllib.parse
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional, Tuple, Dict, Any, List
from PIL import Image, ImageDraw, ImageFont

from emulator.camera import Dsmc2Camera
from emulator.ui.redui import RedUiEngine
from emulator.rcp.protocol import RcpProtocol

try:
    HUD_FONT_LG = ImageFont.truetype("arialbd.ttf", 16)
    HUD_FONT_MED = ImageFont.truetype("arialbd.ttf", 13)
    HUD_FONT_SM = ImageFont.truetype("arial.ttf", 10)
    HUD_FONT_BOLD_SM = ImageFont.truetype("arialbd.ttf", 10)
except Exception:
    HUD_FONT_LG = ImageFont.load_default()
    HUD_FONT_MED = ImageFont.load_default()
    HUD_FONT_SM = ImageFont.load_default()
    HUD_FONT_BOLD_SM = ImageFont.load_default()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server allowing concurrent MJPEG streaming and API requests."""
    daemon_threads = True
    allow_reuse_address = True


class ScreenCompositor:
    """
    Decoupled graphics compositor for RED DSMC2.
    Produces authentic 1024x576 LCD frames with genuine HUD overlays, splash screen, and RedUi system.
    """

    NATIVE_WIDTH = 1024
    NATIVE_HEIGHT = 576

    def __init__(self, camera: Dsmc2Camera):
        self.camera = camera
        self.redui = RedUiEngine(camera)
        self.splash_image: Optional[Image.Image] = self._load_splash_screen()
        self.sensor_feed_image: Optional[Image.Image] = self._load_sensor_feed_image()
        self._zero_fb_buf = b"\x00" * (self.NATIVE_WIDTH * self.NATIVE_HEIGHT * 4)

    def _load_splash_screen(self) -> Optional[Image.Image]:
        splash_path = os.path.join(
            self.camera.workspace_root,
            "firmware_store", "unpacked", "scarlet_w_7_4_1", "slot", "res", "splash_screen.bin"
        )
        if not os.path.exists(splash_path):
            return None
        try:
            with open(splash_path, "rb") as f:
                data = f.read()
            if data.startswith(b"REDZLIB0"):
                decomp = zlib.decompress(data[12:])
                pixels = decomp[8:8 + (1024 * 576 * 3)]
                return Image.frombytes("RGB", (1024, 576), pixels)
        except Exception as e:
            print(f"[Compositor] Failed to load splash screen: {e}")
            return None
        return None

    def _load_sensor_feed_image(self) -> Optional[Image.Image]:
        """Load authentic cinema sensor background in place of the gaussian swirl."""
        sensor_path = os.path.join(self.camera.workspace_root, "res", "sensor_feed_cinema.jpg")
        if not os.path.exists(sensor_path):
            return None
        try:
            im = Image.open(sensor_path)
            if im.size != (self.NATIVE_WIDTH, self.NATIVE_HEIGHT):
                im = im.resize((self.NATIVE_WIDTH, self.NATIVE_HEIGHT), Image.Resampling.LANCZOS)
            return im.convert("RGB")
        except Exception as e:
            print(f"[Compositor] Failed to load cinema sensor feed: {e}")
            return None

    def compose_frame(self) -> Image.Image:
        """Compose raw LCD frame with overlays."""
        state = self.camera.power_state

        if state == self.camera.POWER_STANDBY:
            img = self._render_standby_screen()
        elif state == self.camera.POWER_BOOTING:
            img = self._render_booting_screen()
        else: # POWER_ON
            fb_bytes = self.camera.get_frame()
            is_blank = (fb_bytes == self._zero_fb_buf)

            if not is_blank and len(fb_bytes) >= self.NATIVE_WIDTH * self.NATIVE_HEIGHT * 4:
                img = Image.frombytes("RGBA", (self.NATIVE_WIDTH, self.NATIVE_HEIGHT), fb_bytes)
            elif self.sensor_feed_image:
                img = self.sensor_feed_image.copy()
            elif self.splash_image:
                img = self.splash_image.copy()
            else:
                img = Image.new("RGB", (self.NATIVE_WIDTH, self.NATIVE_HEIGHT), (15, 15, 15))

            # Draw RED HUD
            try:
                self._draw_hud(img)
            except Exception as e:
                print(f"[Compositor] Error drawing HUD: {e}")

            # Draw RedUi layer (Dialogs, Menu, Tools, Reticle)
            try:
                img = self.redui.render(img)
            except Exception as e:
                print(f"[Compositor] Error drawing RedUi: {e}")

        return img

    def get_jpeg_bytes(self, quality: int = 85) -> bytes:
        """Render current frame to JPEG byte buffer."""
        img = self.compose_frame()
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def _render_standby_screen(self) -> Image.Image:
        img = Image.new("RGB", (self.NATIVE_WIDTH, self.NATIVE_HEIGHT), (8, 8, 8))
        draw = ImageDraw.Draw(img)
        draw.rectangle([(10, 10), (1014, 566)], outline=(25, 25, 25), width=2)
        cx, cy = 512, 288
        draw.rectangle([(cx - 260, cy - 90), (cx + 260, cy + 90)], fill=(16, 16, 16), outline=(40, 40, 40), width=2)
        model_name = self.camera.vfs.env.get("CAMERA_NAME", "SCARLET-W 5K")
        draw.text((cx - 150, cy - 70), f"RED DIGITAL CINEMA  |  {model_name}", fill=(200, 200, 200))
        draw.text((cx - 100, cy - 35), "[ SYSTEM IN STANDBY ]", fill=(255, 170, 0))
        draw.text((cx - 120, cy + 0), "DC IN 14.8V  •  Hardware Ready", fill=(100, 100, 100))
        draw.text((cx - 165, cy + 40), ">>> CLICK [POWER ON] BUTTON BELOW TO BOOT <<<", fill=(0, 255, 120))
        return img

    def _render_booting_screen(self) -> Image.Image:
        if self.splash_image:
            img = self.splash_image.copy()
        else:
            img = Image.new("RGB", (self.NATIVE_WIDTH, self.NATIVE_HEIGHT), (15, 15, 15))
        draw = ImageDraw.Draw(img)
        cx, cy = 512, 288
        draw.rectangle([(cx - 240, cy - 50), (cx + 240, cy + 50)], fill=(0, 0, 0), outline=(0, 200, 100), width=2)
        pct = int(self.camera.boot_progress * 100)
        draw.text((cx - 120, cy - 35), f"BOOTING RED DSMC2... {pct}%", fill=(255, 255, 255))
        bar_x1, bar_y1 = cx - 200, cy - 5
        bar_x2, bar_y2 = cx + 200, cy + 15
        draw.rectangle([(bar_x1, bar_y1), (bar_x2, bar_y2)], outline=(80, 80, 80), width=1)
        fill_width = int(400 * max(0.0, min(1.0, self.camera.boot_progress)))
        if fill_width > 0:
            draw.rectangle([(bar_x1 + 1, bar_y1 + 1), (bar_x1 + fill_width, bar_y2 - 1)], fill=(0, 220, 100))
        draw.text((cx - 140, cy + 25), "Bringing up Cortex-A8, ThreadX & OMAP3...", fill=(160, 160, 160))
        return img

    def _draw_hud(self, img: Image.Image):
        draw = ImageDraw.Draw(img)
        rcp = self.camera.rcp
        fps = rcp.state.get(rcp.PARAM_FPS, "23.98")
        shutter = rcp.state.get(rcp.PARAM_SHUTTER, "1/48")
        iso = rcp.state.get(rcp.PARAM_ISO, "800")
        format_name = rcp.state.get(rcp.PARAM_FORMAT, "5K 16:9")
        record_state = rcp.state.get(rcp.PARAM_RECORD_STATE, "0")
        is_recording = (record_state == "2")
        lm = getattr(self.camera, "lens_mount", None)
        iris_str = f"f/{lm.current_aperture:.1f}" if (lm and lm.attached_lens) else rcp.state.get("aperture", "f/2.8")

        # 1. Top HUD Bar (Only drawn when full DSMC2 menu is not active)
        if not self.redui.is_menu_open:
            draw.rectangle([(0, 0), (self.NATIVE_WIDTH, 42)], fill=(0, 0, 0, 210))
            draw.line([(0, 42), (self.NATIVE_WIDTH, 42)], fill=(40, 40, 40), width=1)

            # Left STBY / REC / Model Badge (x: 12..82)
            if is_recording:
                draw.rectangle([(12, 6), (82, 34)], fill=(200, 20, 20), outline=(255, 60, 60), width=1)
                draw.text((22, 11), "• REC", fill=(255, 255, 255), font=HUD_FONT_MED)
            else:
                draw.rectangle([(12, 6), (82, 34)], fill=(28, 28, 28), outline=(60, 60, 60), width=1)
                draw.text((24, 11), "STBY", fill=(180, 180, 180), font=HUD_FONT_MED)

            # Model Menu header (x: 88..245)
            draw.rectangle([(88, 6), (245, 34)], fill=(28, 28, 28), outline=(60, 60, 60), width=1)
            model_name = self.camera.vfs.env.get("CAMERA_NAME", "SCARLET-W 5K")
            draw.text((96, 11), f"RED {model_name} [MENU]", fill=(220, 220, 220), font=HUD_FONT_SM)

            # FPS (x: 255..365)
            fps_active = (self.redui.active_dialog == "FPS")
            draw.rectangle([(255, 6), (365, 34)], fill=(28, 28, 28), outline=(220, 20, 20) if fps_active else (60, 60, 60), width=1)
            draw.text((265, 11), f"FPS: {fps}", fill=(255, 255, 255), font=HUD_FONT_MED)
            if fps_active:
                draw.line([(255, 35), (365, 35)], fill=(255, 30, 30), width=2)

            # SHUTTER (x: 375..495)
            shutter_active = (self.redui.active_dialog == "SHUTTER")
            draw.rectangle([(375, 6), (495, 34)], fill=(28, 28, 28), outline=(220, 20, 20) if shutter_active else (60, 60, 60), width=1)
            draw.text((385, 11), f"SHTR: {shutter}", fill=(255, 255, 255), font=HUD_FONT_MED)
            if shutter_active:
                draw.line([(375, 35), (495, 35)], fill=(255, 30, 30), width=2)

            # IRIS (x: 505..615)
            iris_active = (self.redui.active_dialog == "IRIS")
            draw.rectangle([(505, 6), (615, 34)], fill=(28, 28, 28), outline=(220, 20, 20) if iris_active else (60, 60, 60), width=1)
            draw.text((515, 11), f"IRIS: {iris_str}", fill=(255, 255, 255), font=HUD_FONT_MED)
            if iris_active:
                draw.line([(505, 35), (615, 35)], fill=(255, 30, 30), width=2)

            # ISO (x: 625..735)
            iso_active = (self.redui.active_dialog == "ISO")
            draw.rectangle([(625, 6), (735, 34)], fill=(28, 28, 28), outline=(220, 20, 20) if iso_active else (60, 60, 60), width=1)
            draw.text((635, 11), f"ISO: {iso}", fill=(255, 255, 255), font=HUD_FONT_MED)
            if iso_active:
                draw.line([(625, 35), (735, 35)], fill=(255, 30, 30), width=2)

            # FORMAT (x: 745..895)
            fmt_active = (self.redui.active_dialog == "FORMAT")
            draw.rectangle([(745, 6), (895, 34)], fill=(28, 28, 28), outline=(220, 20, 20) if fmt_active else (60, 60, 60), width=1)
            draw.text((755, 11), f"FORMAT: {format_name}", fill=(255, 255, 255), font=HUD_FONT_MED)
            if fmt_active:
                draw.line([(745, 35), (895, 35)], fill=(255, 30, 30), width=2)

            # Authentic Right-side Menu Button (x: 905..1014)
            draw.rectangle([(905, 6), (1014, 34)], fill=(32, 32, 32), outline=(220, 30, 30), width=1)
            draw.text((936, 11), "Menu", fill=(255, 255, 255), font=HUD_FONT_MED)

            # 2. Inside Frame Metadata
            rec = getattr(self.camera, "recorder", None)
            clip_name = rec.current_clip_name if (rec and rec.current_clip_name) else f"{rec.current_reel_id if rec else 'A001'}_C{rec.clip_number if rec else 1:03d}"
            draw.text((24, 492), f"{clip_name}", fill=(240, 240, 240), font=HUD_FONT_MED)

            timecode = "01:00:00:00" if not is_recording else f"01:00:{int(self.camera.instruction_count/1000000)%60:02d}:12"
            tc_color = (255, 50, 50) if is_recording else (220, 220, 220)
            draw.text((865, 492), timecode, fill=tc_color, font=HUD_FONT_MED)

            # Subtle frame lines
            draw.line([(20, 48), (20, 512)], fill=(80, 20, 20), width=1)
            draw.line([(1004, 48), (1004, 512)], fill=(80, 20, 20), width=1)

        # 3. Authentic Bottom Status Bar (Always visible at y: 520..576)
        draw.rectangle([(0, 520), (self.NATIVE_WIDTH, 576)], fill=(10, 10, 10))
        draw.line([(0, 520), (self.NATIVE_WIDTH, 520)], fill=(45, 45, 45), width=1)

        # Camera Icon Outline & FPS below
        cam_fill = (220, 20, 20) if is_recording else (30, 30, 30)
        draw.rectangle([(12, 528), (38, 548)], fill=cam_fill, outline=(220, 220, 220) if not is_recording else (255, 60, 60), width=1)
        draw.polygon([(38, 532), (46, 528), (46, 548), (38, 544)], fill=cam_fill, outline=(220, 220, 220) if not is_recording else (255, 60, 60))
        draw.text((10, 554), f"{fps}FPS", fill=(220, 220, 220), font=HUD_FONT_BOLD_SM)

        # Mini RGB Histogram Box (x: 55..98, y: 526..568)
        draw.rectangle([(55, 526), (98, 568)], fill=(18, 18, 18), outline=(50, 50, 50), width=1)
        # Exposure Curves
        draw.line([(57, 566), (65, 555), (75, 540), (85, 548), (96, 566)], fill=(240, 50, 50), width=1)
        draw.line([(57, 566), (67, 548), (77, 535), (87, 552), (96, 566)], fill=(50, 220, 50), width=1)
        draw.line([(57, 566), (63, 560), (73, 542), (83, 538), (96, 566)], fill=(60, 120, 255), width=1)

        # 3 Calibration Dots (x: 108)
        draw.ellipse([(108, 529), (114, 535)], fill=(0, 220, 80)) # Temp
        draw.ellipse([(108, 541), (114, 547)], fill=(0, 220, 80)) # Black Shading T/E
        draw.ellipse([(108, 553), (114, 559)], fill=(0, 220, 80)) # Fan/Electronics

        # Status text (x: 122)
        draw.text((122, 526), "37°/54°", fill=(220, 220, 220), font=HUD_FONT_BOLD_SM)
        draw.text((122, 540), "Cal: ", fill=(180, 180, 180), font=HUD_FONT_BOLD_SM)
        draw.text((148, 540), "T/E", fill=(0, 230, 90), font=HUD_FONT_BOLD_SM)
        draw.text((122, 554), "Drop: 0", fill=(180, 180, 180), font=HUD_FONT_BOLD_SM)

        # Badges cluster (x: 195..340)
        draw.text((195, 526), "HDRx", fill=(120, 120, 120), font=HUD_FONT_BOLD_SM)
        draw.text((195, 540), "OFF", fill=(160, 160, 160), font=HUD_FONT_BOLD_SM)
        draw.text((195, 554), "1:1", fill=(120, 120, 120), font=HUD_FONT_BOLD_SM)
        draw.text((215, 554), "ON" if self.redui.is_magnify_on else "OFF", fill=(0, 230, 90) if self.redui.is_magnify_on else (160, 160, 160), font=HUD_FONT_BOLD_SM)

        draw.text((250, 526), "TC", fill=(0, 230, 90), font=HUD_FONT_BOLD_SM)
        draw.text((275, 526), "GEN", fill=(120, 120, 120), font=HUD_FONT_BOLD_SM)
        draw.text((305, 526), "RIG", fill=(120, 120, 120), font=HUD_FONT_BOLD_SM)
        draw.text((250, 544), "SYNC", fill=(120, 120, 120), font=HUD_FONT_BOLD_SM)
        draw.text((285, 544), "WIFI", fill=(120, 120, 120), font=HUD_FONT_BOLD_SM)

        # Media Status (x: 355..470)
        rec = getattr(self.camera, "recorder", None)
        rem_pct = rec.media_remaining_percent if rec else 100
        media_active = (self.redui.active_dialog == "MEDIA")
        draw.rectangle([(355, 524), (470, 570)], fill=(20, 20, 20), outline=(220, 20, 20) if media_active else (45, 45, 45))
        draw.text((365, 528), "S-SSD", fill=(180, 180, 180), font=HUD_FONT_BOLD_SM)
        draw.text((365, 540), f"{rem_pct}%", fill=(0, 230, 90) if rem_pct > 15 else (240, 50, 50), font=HUD_FONT_MED)
        draw.rectangle([(425, 532), (462, 548)], fill=(40, 40, 40), outline=(70, 70, 70))
        draw.text((430, 535), "R3D", fill=(255, 255, 255), font=HUD_FONT_BOLD_SM)

        # Power Status (x: 480..575)
        draw.rectangle([(480, 524), (575, 570)], fill=(20, 20, 20), outline=(45, 45, 45))
        draw.text((490, 528), "DC", fill=(180, 180, 180), font=HUD_FONT_BOLD_SM)
        draw.text((490, 542), "15.2V", fill=(80, 240, 80), font=HUD_FONT_MED)

        # 4-Channel Audio Peak Meters (x: 585..770)
        audio_active = (self.redui.active_dialog == "AUDIO")
        draw.rectangle([(585, 524), (770, 570)], fill=(18, 18, 18), outline=(220, 20, 20) if audio_active else (45, 45, 45))
        # Ch 1
        draw.text((592, 527), "Ch1", fill=(0, 220, 80), font=HUD_FONT_BOLD_SM)
        draw.rectangle([(620, 529), (755, 535)], outline=(50, 50, 50))
        draw.rectangle([(621, 530), (715, 534)], fill=(0, 220, 80)) # -18 dBFS level
        # Ch 2
        draw.text((592, 538), "Ch2", fill=(0, 220, 80), font=HUD_FONT_BOLD_SM)
        draw.rectangle([(620, 540), (755, 546)], outline=(50, 50, 50))
        draw.rectangle([(621, 541), (710, 545)], fill=(0, 220, 80))
        # Ch 3 & 4
        draw.text((592, 549), "Ch3", fill=(100, 100, 100), font=HUD_FONT_BOLD_SM)
        draw.rectangle([(620, 551), (755, 557)], outline=(40, 40, 40))
        draw.text((592, 559), "Ch4", fill=(100, 100, 100), font=HUD_FONT_BOLD_SM)
        draw.rectangle([(620, 561), (755, 567)], outline=(40, 40, 40))

        # Virtual CPU Instructions (x: 870..1014)
        instr_m = self.camera.instruction_count / 1_000_000
        draw.rectangle([(870, 544), (1014, 572)], fill=(22, 22, 22), outline=(50, 50, 50), width=1)
        draw.text((880, 550), f"INSTR: {instr_m:.1f}M", fill=(140, 140, 140), font=HUD_FONT_BOLD_SM)


class WebStudio:
    """Manages virtual camera execution, HTTP server, and client dispatch."""

    def __init__(self, workspace_root: str, host: str = "127.0.0.1", port: int = 8765):
        self.workspace_root = os.path.abspath(workspace_root)
        self.host = host
        self.port = port
        self.camera = Dsmc2Camera(self.workspace_root)
        self.compositor = ScreenCompositor(self.camera)

        self.running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.http_server: Optional[ThreadedHTTPServer] = None

        # Lock for thread-safe camera dispatch
        self.lock = threading.Lock()

        # Firmware Staging & Baseline Configuration
        self.confirmed_baseline_name = "SCARLET-W 5K (v7.4.1)"
        self.confirmed_baseline_file = "dsmc_program.mem"
        self.active_firmware_name = "SCARLET-W 5K (v7.4.1)"
        self.active_firmware_file = "dsmc_program.mem"
        self.active_firmware_size = 18972672
        self.active_firmware_is_custom = False
        self.custom_firmware_path: Optional[str] = None

    def start_camera(self):
        """Boot camera and launch CPU stepping thread."""
        if not self.camera.is_booted:
            self.camera.power_on()
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="Dsmc2CpuWorker")
        self.worker_thread.start()

    def _worker_loop(self):
        """Continuously steps CPU instructions when powered on."""
        while self.running:
            if self.camera.power_state == self.camera.POWER_ON and self.camera.is_booted:
                with self.lock:
                    self.camera.step_slice(25_000)
            time.sleep(0.015)

    def handle_touch(self, x: int, y: int, pressed: bool, action: str = "down"):
        with self.lock:
            self.camera.touch(x, y, pressed=pressed)
            self.compositor.redui.handle_touch(x, y, pressed=pressed, action=action)
            if action == "down":
                self.camera.log_system(f"[TOUCH] Touch down at ({x}, {y}) -> maXTouch (0x4B)")
            elif action == "up":
                self.camera.log_system(f"[TOUCH] Touch release at ({x}, {y}) -> maXTouch (0x4B)")

    def handle_button(self, btn_name: str):
        with self.lock:
            pin_map = {
                "MENU": 10, "1:1 MAG": 11, "USER 1": 14, "USER 2": 15,
                "NAV UP": 20, "NAV LEFT": 21, "NAV ENTER": 22, "NAV RIGHT": 23, "NAV DOWN": 24
            }
            pin = pin_map.get(btn_name)
            if pin is not None:
                self.camera.log_system(f"[KEY] Physical button [{btn_name}] pressed (GPIO pin {pin})")
                if 1 in self.camera.gpio:
                    self.camera.gpio[1].set_input_pin(pin, True)
                    self.camera.gpio[1].set_input_pin(pin, False)

            self.compositor.redui.handle_button_press(btn_name)

    def handle_record(self):
        with self.lock:
            self.camera.press_record()
            is_rec = (self.camera.rcp.state.get(RcpProtocol.PARAM_RECORD_STATE) == "2")
            if is_rec:
                self.camera.log_system("[REC] Recording STARTED -> RED MINI-MAG (R3D 5K 24fps)")
            else:
                self.camera.log_system("[REC] Recording STOPPED -> File saved safely")

    def handle_power(self):
        with self.lock:
            self.camera.toggle_power()

    def stop(self):
        self.running = False
        if self.http_server:
            try:
                self.http_server.shutdown()
                self.http_server.server_close()
            except Exception:
                pass
        self.camera.stop()


def create_handler(studio: WebStudio):
    """Factory creating HTTP request handler bound to the studio instance."""

    class StudioHttpHandler(BaseHTTPRequestHandler):

        def log_message(self, format, *args):
            # Suppress normal HTTP logging to keep terminal output clean for camera logs
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/" or path == "/index.html":
                self._serve_studio_html()
            elif path == "/api/frame":
                self._serve_single_frame()
            elif path == "/api/stream":
                self._serve_mjpeg_stream()
            elif path == "/api/status":
                self._serve_status_json()
            elif path == "/api/logs":
                self._serve_logs_json(parsed.query)
            elif path == "/api/lens":
                with studio.lock:
                    lm = studio.camera.lens_mount
                    catalog = [l.to_dict() for l in lm.lens_database]
                    self._send_json({"status": lm.get_status(), "catalog": catalog})
            elif path == "/api/media/clips":
                with studio.lock:
                    clips = studio.camera.recorder.list_clips()
                    self._send_json({"clips": clips})
            elif path == "/api/firmware/status":
                with studio.lock:
                    info = {
                        "status": "ok",
                        "active_name": studio.active_firmware_name,
                        "active_file": studio.active_firmware_file,
                        "active_size": studio.active_firmware_size,
                        "is_custom": studio.active_firmware_is_custom,
                        "baseline_name": studio.confirmed_baseline_name,
                        "slot_mounted": studio.camera.recorder.is_media_mounted if hasattr(studio.camera, "recorder") else True
                    }
                self._send_json(info)
            elif path == "/api/camera/models":
                with studio.lock:
                    models = studio.camera.env.list_profiles()
                    current = studio.camera.env.get("CAMERA_TYPE", "0x16")
                    self._send_json({"status": "ok", "current": current, "models": models})
            else:
                self.send_error(404, "Not Found")

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(length) if length > 0 else b"{}"

            try:
                data = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            except Exception:
                data = {}

            if path == "/api/touch":
                x = int(data.get("x", 0))
                y = int(data.get("y", 0))
                pressed = bool(data.get("pressed", True))
                action = str(data.get("action", "down" if pressed else "up"))
                studio.handle_touch(x, y, pressed, action=action)
                self._send_json({"status": "ok", "x": x, "y": y, "pressed": pressed, "action": action})

            elif path == "/api/camera/select":
                model_id = str(data.get("model", "0x16"))
                with studio.lock:
                    success = studio.camera.select_camera_model(model_id)
                    current_name = studio.camera.env.get("CAMERA_NAME", "SCARLET-W 5K")
                    studio.active_firmware_name = f"{current_name} (v7.4.1)"
                    studio.confirmed_baseline_name = f"{current_name} (v7.4.1)"
                self._send_json({"status": "ok" if success else "error", "model": model_id, "camera_name": current_name})

            elif path == "/api/button":
                btn = str(data.get("button", ""))
                if btn == "REC":
                    studio.handle_record()
                elif btn == "POWER":
                    studio.handle_power()
                else:
                    studio.handle_button(btn)
                self._send_json({"status": "ok", "button": btn})

            elif path == "/api/rcp":
                cmd = str(data.get("command", "")).strip()
                if cmd:
                    with studio.lock:
                        resp = studio.camera.rcp.process_command(cmd)
                else:
                    resp = "ERROR"
                self._send_json({"status": "ok", "command": cmd, "response": resp})

            elif path == "/api/power":
                studio.handle_power()
                self._send_json({"status": "ok", "power_state": studio.camera.power_state})

            elif path == "/api/snapshot/save":
                name = str(data.get("name", f"snapshot_{int(time.time())}.dsmc2"))
                snap_dir = os.path.join(studio.workspace_root, "snapshots")
                os.makedirs(snap_dir, exist_ok=True)
                snap_path = os.path.join(snap_dir, name)
                with studio.lock:
                    from emulator.core.snapshot import SnapshotManager
                    SnapshotManager.save(studio.camera, snap_path, desc=f"Web snapshot {name}")
                self._send_json({"status": "ok", "file": snap_path})

            elif path == "/api/snapshot/restore":
                file_name = str(data.get("name", ""))
                snap_path = os.path.join(studio.workspace_root, "snapshots", file_name)
                success = False
                if os.path.exists(snap_path):
                    with studio.lock:
                        from emulator.core.snapshot import SnapshotManager
                        success = SnapshotManager.restore(studio.camera, snap_path)
                self._send_json({"status": "ok" if success else "error", "restored": success})

            elif path == "/api/fault":
                fault_type = str(data.get("type", "INTC_IRQ_LOCKUP"))
                with studio.lock:
                    from emulator.core.faults import HardwareFaultInjector
                    inj = HardwareFaultInjector(studio.camera)
                    res = inj.inject_fault(fault_type)
                self._send_json({"status": "ok", "result": res})

            elif path == "/api/lens/attach":
                name = str(data.get("name", ""))
                idx = data.get("index")
                with studio.lock:
                    lm = studio.camera.lens_mount
                    target = None
                    if idx is not None and 0 <= int(idx) < len(lm.lens_database):
                        target = lm.lens_database[int(idx)]
                    elif name:
                        target = next((l for l in lm.lens_database if l.model.lower() == name.lower()), None)
                    if target:
                        lm.attach_lens(target)
                self._send_json({"status": "ok", "attached": target.model if target else None})

            elif path == "/api/lens/mount":
                mount_name = str(data.get("mount", "Canon EF"))
                with studio.lock:
                    studio.camera.lens_mount.set_mount_type(mount_name)
                self._send_json({"status": "ok", "mount": mount_name})

            elif path == "/api/lens/aperture":
                val = data.get("aperture")
                step = data.get("step")
                with studio.lock:
                    lm = studio.camera.lens_mount
                    if step is not None:
                        lm.step_aperture(int(step))
                    elif val is not None:
                        lm.set_aperture(float(val))
                self._send_json({"status": "ok", "aperture": studio.camera.lens_mount.current_aperture})

            elif path == "/api/lens/focal":
                f_mm = float(data.get("focal_length", 50.0))
                with studio.lock:
                    studio.camera.lens_mount.set_focal_length(f_mm)
                self._send_json({"status": "ok", "focal_length": studio.camera.lens_mount.current_focal_length})

            elif path == "/api/lens/focus":
                dist = float(data.get("distance", 2.5))
                with studio.lock:
                    studio.camera.lens_mount.set_focus_distance(dist)
                self._send_json({"status": "ok", "focus_distance": studio.camera.lens_mount.current_focus_distance})

            elif path == "/api/lens/af":
                with studio.lock:
                    res = studio.camera.lens_mount.trigger_autofocus()
                self._send_json({"status": "ok", "result": res, "distance": studio.camera.lens_mount.current_focus_distance})

            elif path == "/api/media/format":
                with studio.lock:
                    res = studio.camera.recorder.format_media()
                self._send_json({"status": "ok", "formatted": res})

            elif path == "/api/media/eject":
                with studio.lock:
                    res = studio.camera.recorder.eject_media()
                self._send_json({"status": "ok", "ejected": res})

            elif path == "/api/media/mount":
                with studio.lock:
                    res = studio.camera.recorder.mount_media()
                self._send_json({"status": "ok", "mounted": res})

            elif path == "/api/firmware/upload":
                import base64
                filename = str(data.get("filename", "custom_firmware.bin"))
                filename = os.path.basename(filename)
                b64_data = data.get("data", "")
                if "," in b64_data:
                    b64_data = b64_data.split(",", 1)[1]
                file_bytes = base64.b64decode(b64_data)

                custom_dir = os.path.join(studio.workspace_root, "firmware_store", "custom")
                os.makedirs(custom_dir, exist_ok=True)
                dest_path = os.path.join(custom_dir, filename)
                with open(dest_path, "wb") as f:
                    f.write(file_bytes)

                with studio.lock:
                    studio.active_firmware_name = f"Custom: {filename}"
                    studio.active_firmware_file = filename
                    studio.active_firmware_size = len(file_bytes)
                    studio.active_firmware_is_custom = True
                    studio.custom_firmware_path = dest_path
                    studio.camera.log_system(f"[MEDIA-BAY] RED MINI-MAG: Custom firmware image loaded: {filename} ({len(file_bytes)} bytes)")
                    studio.camera.log_system("[FIRMWARE-LOADER] Target ready for DSMC2 firmware verification & staging.")

                self._send_json({
                    "status": "ok",
                    "active_name": studio.active_firmware_name,
                    "filename": filename,
                    "size": len(file_bytes)
                })

            elif path == "/api/firmware/reset":
                with studio.lock:
                    studio.active_firmware_name = studio.confirmed_baseline_name
                    studio.active_firmware_file = studio.confirmed_baseline_file
                    studio.active_firmware_size = 18972672
                    studio.active_firmware_is_custom = False
                    studio.custom_firmware_path = None
                    studio.camera.log_system(f"[MEDIA-BAY] RED MINI-MAG slot reset to confirmed working baseline: {studio.confirmed_baseline_name} ({studio.confirmed_baseline_file})")
                self._send_json({
                    "status": "ok",
                    "active_name": studio.active_firmware_name,
                    "baseline_name": studio.confirmed_baseline_name
                })

            else:
                self.send_error(404, "Unknown API Route")

        def _send_json(self, payload: dict, status: int = 200):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def _serve_single_frame(self):
            with studio.lock:
                jpeg_data = studio.compositor.get_jpeg_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg_data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(jpeg_data)

        def _serve_mjpeg_stream(self):
            """Stream multipart MJPEG video directly to browser at ~25 FPS."""
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=--frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()

            try:
                while studio.running:
                    with studio.lock:
                        frame_bytes = studio.compositor.get_jpeg_bytes(quality=80)
                    self.wfile.write(b"----frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame_bytes)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(frame_bytes)
                    self.wfile.write(b"\r\n")
                    time.sleep(0.04) # ~25 FPS
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _serve_status_json(self):
            with studio.lock:
                cam = studio.camera
                is_rec = (cam.rcp.state.get(RcpProtocol.PARAM_RECORD_STATE) == "2")
                lm_status = cam.lens_mount.get_status() if hasattr(cam, "lens_mount") else None
                rec_status = {
                    "is_recording": cam.recorder.is_recording,
                    "is_mounted": cam.recorder.is_media_mounted,
                    "reel_id": cam.recorder.current_reel_id,
                    "clip_number": cam.recorder.clip_number,
                    "capacity_gb": cam.recorder.MEDIA_CAPACITY_GB,
                    "used_mb": round(cam.recorder.used_space_mb, 1),
                    "free_mb": round(cam.recorder.free_space_mb, 1),
                    "remaining_percent": cam.recorder.media_remaining_percent,
                    "current_clip": cam.recorder.current_clip_name,
                    "clips_count": len(cam.recorder.list_clips())
                } if hasattr(cam, "recorder") else None

                status = {
                    "power_state": cam.power_state,
                    "boot_progress": cam.boot_progress,
                    "instruction_count": cam.instruction_count,
                    "camera_name": cam.env.get("CAMERA_NAME", "SCARLET-W 5K"),
                    "camera_type": cam.env.get("CAMERA_TYPE", "0x16"),
                    "is_recording": is_rec,
                    "rcp_clients_count": cam.rcp_server.get_client_count() if hasattr(cam, "rcp_server") else 0,
                    "lens": lm_status,
                    "media": rec_status,
                    "active_dialog": studio.compositor.redui.active_dialog,
                    "is_menu_open": studio.compositor.redui.is_menu_open,
                    "tools": {
                        "magnify": studio.compositor.redui.is_magnify_on,
                        "false_color": studio.compositor.redui.is_false_color_on,
                        "zebras": studio.compositor.redui.is_zebras_on,
                        "guide": studio.compositor.redui.guide_mode
                    },
                    "rcp": dict(cam.rcp.state),
                    "firmware": {
                        "active_name": studio.active_firmware_name,
                        "active_file": studio.active_firmware_file,
                        "active_size": studio.active_firmware_size,
                        "is_custom": studio.active_firmware_is_custom,
                        "baseline_name": studio.confirmed_baseline_name
                    },
                    "threads": cam.get_threads(),
                    "unknown_mmio_count": len(cam.bus.unknown_mmio)
                }
            self._send_json(status)

        def _serve_logs_json(self, query_str: str):
            since = 0
            if query_str:
                params = urllib.parse.parse_qs(query_str)
                since = int(params.get("since", [0])[0])

            with studio.lock:
                all_logs = studio.camera.get_serial_logs()
                new_logs = all_logs[since:]

            self._send_json({"logs": new_logs, "total": len(all_logs)})

        def _serve_studio_html(self):
            html = STUDIO_HTML.replace("{{HOST}}", studio.host).replace("{{PORT}}", str(studio.port))
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return StudioHttpHandler


# ---------------------------------------------------------------------------
# Authentic RED DSMC2 Virtual Camera Studio Web Frontend
# Single-page application with zero external dependencies (pure HTML5/CSS3/JS)
# ---------------------------------------------------------------------------
STUDIO_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RED DSMC2 Virtual Camera Studio — Scarlet-W 5K</title>
<style>
  :root {
    --bg-main: #121212;
    --bg-panel: #1b1b1b;
    --bg-card: #222222;
    --border: #333333;
    --red-brand: #d11212;
    --red-bright: #ff2222;
    --green-led: #00e676;
    --amber-led: #ffb300;
    --text-primary: #f0f0f0;
    --text-muted: #888888;
    --accent: #007acc;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; user-select: none; }
  body {
    background: var(--bg-main);
    color: var(--text-primary);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    overflow-x: hidden;
  }

  /* Header */
  header {
    background: #0a0a0a;
    border-bottom: 1px solid var(--border);
    padding: 10px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .red-logo {
    background: var(--red-brand);
    color: #fff;
    font-weight: 900;
    letter-spacing: 2px;
    padding: 3px 10px;
    border-radius: 2px;
    font-size: 14px;
  }
  .brand-title {
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 1px;
    color: #ddd;
  }
  .brand-sub {
    font-size: 11px;
    color: var(--text-muted);
    font-family: monospace;
  }
  .status-cluster {
    display: flex;
    align-items: center;
    gap: 16px;
    font-family: monospace;
    font-size: 12px;
  }
  .led-badge {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 4px;
    background: #181818;
    border: 1px solid #333;
  }
  .led-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--amber-led);
    box-shadow: 0 0 6px var(--amber-led);
  }
  .led-dot.online { background: var(--green-led); box-shadow: 0 0 8px var(--green-led); }
  .led-dot.recording { background: var(--red-bright); box-shadow: 0 0 10px var(--red-bright); animation: pulse 1s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

  /* Main Studio Layout */
  .studio-grid {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    padding: 14px 20px 40px 20px;
    flex: 1;
    max-width: 1350px;
    margin: 0 auto;
    width: 100%;
    box-sizing: border-box;
  }

  /* Camera Unit Column */
  .camera-unit {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    width: 100%;
  }

  /* Authentic RED Touch 7.0 Aluminum Bezel */
  .monitor-bezel {
    background: linear-gradient(145deg, #242424, #151515);
    border: 3px solid #383838;
    border-radius: 12px;
    padding: 12px 16px 14px 16px;
    box-shadow: 0 16px 40px rgba(0,0,0,0.85), inset 0 1px 0 rgba(255,255,255,0.1);
    position: relative;
    width: 100%;
    box-sizing: border-box;
  }
  .bezel-screws {
    display: flex;
    justify-content: space-between;
    margin-bottom: 6px;
    padding: 0 4px;
  }
  .screw {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #2a2a2a;
    border: 1px solid #444;
    box-shadow: inset 0 1px 1px #000;
  }
  .bezel-header {
    display: flex;
    justify-content: space-between;
    font-family: monospace;
    font-size: 11px;
    color: #888;
    margin-bottom: 6px;
    letter-spacing: 1px;
  }

  /* Screen & MINI-MAG Flex Container */
  .screen-and-mag-container {
    display: flex;
    flex-direction: row;
    gap: 14px;
    width: 100%;
    align-items: stretch;
    justify-content: center;
  }

  /* RED MINI-MAG Vertical Slot Bay */
  .minimag-slot-bay {
    width: 175px;
    min-width: 175px;
    flex-shrink: 0;
    background: linear-gradient(180deg, #1c1c1c 0%, #121212 100%);
    border: 2px solid #333333;
    border-radius: 6px;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    box-shadow: inset 0 2px 4px rgba(0,0,0,0.8), 0 4px 10px rgba(0,0,0,0.5);
    box-sizing: border-box;
  }
  .mag-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid #2a2a2a;
    padding-bottom: 6px;
  }
  .mag-brand {
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .mag-logo-red {
    background: var(--red-brand);
    color: #fff;
    font-weight: 900;
    font-size: 10px;
    padding: 1px 4px;
    border-radius: 2px;
    letter-spacing: 1px;
  }
  .mag-label {
    font-size: 10px;
    font-weight: 700;
    color: #ddd;
    letter-spacing: 0.5px;
  }
  .mag-slot-tag {
    font-family: monospace;
    font-size: 9px;
    color: #777;
  }

  /* Physical Cartridge Visual */
  .mag-cartridge {
    background: linear-gradient(135deg, #a81010 0%, #680000 100%);
    border: 1px solid #ff3333;
    border-radius: 4px;
    padding: 8px 10px;
    box-shadow: inset 0 1px 2px rgba(255,255,255,0.3), 0 4px 8px rgba(0,0,0,0.6);
    position: relative;
    overflow: hidden;
  }
  .mag-grip {
    position: absolute;
    right: 4px;
    top: 4px;
    bottom: 4px;
    width: 6px;
    background: repeating-linear-gradient(180deg, #440000, #440000 2px, #881111 2px, #881111 4px);
    border-radius: 1px;
  }
  .mag-cartridge-body {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .mag-cartridge-title {
    font-size: 11px;
    font-weight: 900;
    color: #ffffff;
    letter-spacing: 1px;
  }
  .mag-cartridge-cap {
    font-size: 15px;
    font-weight: 900;
    color: #ffffff;
    font-family: monospace;
  }
  .mag-cartridge-sub {
    font-size: 8.5px;
    color: #ffcccc;
    font-family: monospace;
    letter-spacing: 0.5px;
  }
  .mag-led-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 6px;
    padding-top: 4px;
    border-top: 1px solid rgba(255,255,255,0.15);
  }
  .mag-led {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--green-led);
    box-shadow: 0 0 6px var(--green-led);
  }
  .mag-led.custom {
    background: #00b0ff;
    box-shadow: 0 0 8px #00b0ff;
  }
  .mag-led.ejected {
    background: #555;
    box-shadow: none;
  }
  .mag-led-text {
    font-family: monospace;
    font-size: 9.5px;
    font-weight: bold;
    color: #eee;
  }

  /* Firmware Card */
  .mag-fw-card {
    background: #181818;
    border: 1px solid #2e2e2e;
    border-radius: 4px;
    padding: 6px 8px;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .fw-card-label {
    font-size: 9px;
    font-weight: 700;
    color: #777;
    letter-spacing: 0.5px;
  }
  .fw-card-name {
    font-size: 10.5px;
    font-weight: bold;
    color: #00e676;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .fw-card-status {
    font-size: 9.5px;
    color: #aaa;
  }
  .fw-card-details {
    font-family: monospace;
    font-size: 9px;
    color: #666;
  }

  /* Dropzone */
  .mag-dropzone {
    border: 1.5px dashed #444;
    border-radius: 4px;
    padding: 10px 6px;
    text-align: center;
    cursor: pointer;
    background: #141414;
    transition: all 0.15s ease;
  }
  .mag-dropzone:hover, .mag-dropzone.dragover {
    border-color: var(--red-brand);
    background: #1e1414;
  }
  .dropzone-icon {
    font-size: 16px;
    color: #888;
    margin-bottom: 2px;
  }
  .dropzone-text {
    font-size: 10px;
    color: #ccc;
    line-height: 1.3;
  }
  .dropzone-hint {
    font-size: 8.5px;
    color: #777;
    margin-top: 3px;
  }

  /* Controls */
  .mag-controls {
    display: flex;
    gap: 6px;
    margin-top: auto;
  }
  .mag-btn-reset {
    padding: 5px 8px;
    font-size: 10px;
    flex: 1;
    background: #252525;
    border: 1px solid #444;
    color: #aaa;
  }
  .mag-btn-reset:hover {
    color: #fff;
    border-color: #777;
  }

  @media (max-width: 860px) {
    .screen-and-mag-container {
      flex-direction: column;
    }
    .minimag-slot-bay {
      width: 100%;
      min-width: 0;
    }
  }

  /* Screen Frame Stream Canvas */
  .screen-viewport {
    position: relative;
    width: 100%;
    flex: 1;
    min-width: 0;
    aspect-ratio: 16 / 9;
    max-height: 62vh;
    background: #000;
    border-radius: 4px;
    overflow: hidden;
    cursor: crosshair;
    box-shadow: inset 0 0 16px rgba(0,0,0,0.95);
  }
  .screen-viewport img {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    object-fit: fill;
    display: block;
    pointer-events: none;
  }
  /* Overlay canvas for responsive touch feedback */
  .touch-overlay {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    pointer-events: auto;
  }

  /* Physical Button Control Deck */
  .tactile-deck {
    background: #1a1a1a;
    border: 2px solid #2f2f2f;
    border-radius: 8px;
    padding: 12px 18px;
    display: flex;
    align-items: center;
    gap: 16px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.5);
    width: 100%;
    box-sizing: border-box;
    justify-content: space-between;
    flex-wrap: wrap;
  }
  .btn-group {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  /* Authentic Buttons */
  button.cam-btn {
    background: linear-gradient(180deg, #333333, #222222);
    color: #e0e0e0;
    border: 1px solid #444;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    padding: 8px 14px;
    cursor: pointer;
    box-shadow: 0 2px 4px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.1);
    transition: all 0.08s ease;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  button.cam-btn:hover {
    background: linear-gradient(180deg, #3e3e3e, #2a2a2a);
    border-color: #666;
  }
  button.cam-btn:active {
    background: #181818;
    transform: translateY(1px);
    box-shadow: inset 0 2px 4px rgba(0,0,0,0.8);
  }

  /* Primary RED Record Button */
  button.rec-btn {
    background: linear-gradient(180deg, #b01010, #7a0000);
    color: #ffffff;
    border: 2px solid #ff4444;
    border-radius: 50%;
    width: 58px;
    height: 58px;
    font-size: 11px;
    font-weight: 900;
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(180, 0, 0, 0.4), inset 0 2px 2px rgba(255,255,255,0.3);
    transition: all 0.1s;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
  }
  button.rec-btn:hover {
    background: linear-gradient(180deg, #cf1515, #940505);
    transform: scale(1.04);
  }
  button.rec-btn.recording {
    background: #ff1111;
    border-color: #ffffff;
    box-shadow: 0 0 16px rgba(255, 30, 30, 0.9);
    animation: recPulse 1.2s infinite;
  }
  @keyframes recPulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.03); } }

  /* Power Button */
  button.power-btn {
    background: linear-gradient(180deg, #1e3a1e, #132413);
    color: #66ff88;
    border: 1px solid #2e602e;
    font-family: monospace;
  }

  /* D-Pad Container */
  .dpad {
    display: grid;
    grid-template-columns: repeat(3, 28px);
    grid-template-rows: repeat(3, 28px);
    gap: 3px;
  }
  .dpad button {
    background: #2a2a2a;
    border: 1px solid #444;
    color: #ccc;
    border-radius: 3px;
    cursor: pointer;
    font-size: 10px;
    font-weight: bold;
    display: flex;
    justify-content: center;
    align-items: center;
  }
  .dpad button:hover { background: #383838; }
  .dpad button:active { background: #111; }

  /* Engineering Workbench: Full Width Below Screen */
  .workbench {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    width: 100%;
    min-height: 480px;
    max-height: 650px;
    box-sizing: border-box;
  }
  .tab-bar {
    display: flex;
    background: #141414;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
  }
  .tab-btn {
    padding: 10px 18px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-muted);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.15s;
  }
  .tab-btn:hover { color: #fff; }
  .tab-btn.active {
    color: #fff;
    border-bottom: 2px solid var(--red-brand);
    background: #1d1d1d;
  }
  .tab-content {
    flex: 1;
    overflow: auto;
    padding: 14px;
    display: none;
  }
  .tab-content.active { display: flex; flex-direction: column; }

  /* Terminal Console */
  .console-box {
    background: #080808;
    border: 1px solid #2a2a2a;
    border-radius: 4px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11.5px;
    color: #00ff66;
    padding: 12px;
    flex: 1;
    overflow-y: auto;
    white-space: pre-wrap;
    line-height: 1.5;
  }
  .console-tools {
    display: flex;
    justify-content: space-between;
    margin-bottom: 8px;
    align-items: center;
    font-size: 12px;
  }

  /* RCP Parameters Grid */
  table.data-table {
    width: 100%;
    border-collapse: collapse;
    font-family: monospace;
    font-size: 12px;
  }
  table.data-table th, table.data-table td {
    padding: 8px 10px;
    border-bottom: 1px solid #2c2c2c;
    text-align: left;
  }
  table.data-table th { background: #181818; color: #888; font-weight: 600; }
  table.data-table tr:hover { background: #222; }

  /* Command Prompt Bar */
  .cmd-bar {
    display: flex;
    gap: 8px;
    margin-top: 10px;
  }
  .cmd-input {
    flex: 1;
    background: #0d0d0d;
    border: 1px solid #444;
    color: #fff;
    padding: 8px 12px;
    border-radius: 4px;
    font-family: monospace;
    font-size: 13px;
  }
  .cmd-input:focus { outline: none; border-color: var(--accent); }

  /* Tip / Hotkeys */
  .hotkey-badge {
    background: #252525;
    border: 1px solid #444;
    padding: 2px 5px;
    border-radius: 3px;
    font-size: 10px;
    color: #aaa;
    font-family: monospace;
  }

  /* Camera Model Modal */
  .modal-backdrop {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.88);
    backdrop-filter: blur(6px);
    z-index: 10000;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }
  .modal-box {
    background: #181818;
    border: 2px solid #383838;
    border-radius: 10px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.9), inset 0 1px 0 rgba(255,255,255,0.1);
    max-width: 920px;
    width: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .modal-top {
    background: #111;
    border-bottom: 1px solid #2e2e2e;
    padding: 14px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .dsmc2-badge {
    background: var(--red-brand);
    color: #fff;
    font-size: 11px;
    font-weight: 800;
    padding: 2px 8px;
    border-radius: 2px;
    letter-spacing: 1px;
    margin-right: 10px;
  }
  .modal-x-btn {
    background: transparent;
    border: none;
    color: #aaa;
    font-size: 20px;
    cursor: pointer;
    padding: 4px 8px;
  }
  .modal-x-btn:hover { color: #fff; }
  .modal-intro {
    padding: 14px 20px 4px 20px;
    font-size: 13px;
    color: #aaa;
    line-height: 1.4;
  }
  .camera-model-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 14px;
    padding: 16px 20px;
    max-height: 65vh;
    overflow-y: auto;
  }
  .cam-card {
    background: #202020;
    border: 1px solid #3a3a3a;
    border-radius: 8px;
    padding: 14px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 10px;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  .cam-card:hover {
    border-color: #ff3333;
    background: #262626;
    transform: translateY(-2px);
  }
  .cam-card.active {
    border-color: var(--red-bright);
    background: #2a1515;
    box-shadow: 0 0 12px rgba(220, 20, 20, 0.4);
  }
  .cam-card-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .cam-card-name {
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 0.5px;
    color: #fff;
  }
  .cam-card-id {
    font-size: 11px;
    font-family: monospace;
    background: #111;
    padding: 2px 6px;
    border-radius: 3px;
    color: #888;
  }
  .cam-card-sensor {
    font-size: 12px;
    color: #00e676;
    font-family: monospace;
    margin-top: 4px;
  }
  .cam-card-desc {
    font-size: 12px;
    color: #bbb;
    line-height: 1.35;
  }
  .cam-card-specs {
    display: flex;
    gap: 6px;
    font-size: 10px;
    font-family: monospace;
    flex-wrap: wrap;
  }
  .spec-pill {
    background: #151515;
    padding: 3px 6px;
    border-radius: 3px;
    color: #ddd;
    border: 1px solid #333;
  }
  .cam-card-btn {
    background: #333;
    color: #fff;
    border: 1px solid #555;
    padding: 7px 12px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
    text-align: center;
    transition: background 0.15s;
    margin-top: 8px;
  }
  .cam-card.active .cam-card-btn {
    background: var(--red-brand);
    border-color: #ff4444;
  }
  .cam-card:hover .cam-card-btn {
    background: var(--red-brand);
  }
  .modal-bottom {
    background: #111;
    border-top: 1px solid #2e2e2e;
    padding: 12px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .modal-foot-note {
    font-size: 11px;
    color: #777;
    font-family: monospace;
  }
  .model-select-btn {
    background: #242424;
    border: 1px solid #444;
    color: #fff;
    font-size: 12px;
    padding: 5px 12px;
    border-radius: 4px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
    transition: all 0.15s ease;
  }
  .model-select-btn:hover {
    border-color: #ff3333;
    background: #2c1a1a;
  }
  .model-select-btn .bolt { color: #ffb300; font-size: 13px; }
  .model-select-btn .caret { font-size: 10px; color: #888; }
</style>
</head>
<body>

<header>
  <div class="brand">
    <div class="red-logo">RED</div>
    <div>
      <div class="brand-title">DSMC2 VIRTUAL CAMERA STUDIO</div>
      <div class="brand-sub"><span id="hdr-sub-name">SCARLET-W 5K</span> • FIRMWARE v7.4.1 • CORTEX-A8 @ 1.0 GHz</div>
    </div>
  </div>
  <div class="status-cluster">
    <button class="model-select-btn" onclick="openModelModal()" id="hdr-model-btn" title="Choose DSMC2 Camera Model">
      <span class="bolt">⚡</span> BRAIN: <strong id="hdr-model-name">SCARLET-W 5K</strong> <span class="caret">▾</span>
    </button>
    <div class="led-badge">
      <div id="led-dot" class="led-dot"></div>
      <span id="pwr-text">STANDBY</span>
    </div>
    <div class="led-badge">
      <span>RCP: <strong id="rcp-clients">0 clients</strong> (Port 1111)</span>
    </div>
    <div class="led-badge">
      <span>HOST: <strong>{{HOST}}:{{PORT}}</strong></span>
    </div>
    <div class="led-badge">
      <span id="instr-text">0.0M INSTR</span>
    </div>
  </div>
</header>

<div class="studio-grid">

  <!-- Left: Virtual Camera Zone -->
  <div class="camera-unit">
    <div class="monitor-bezel">
      <div class="bezel-screws">
        <div class="screw"></div><div class="screw"></div>
      </div>
      <div class="bezel-header">
        <span>RED TOUCH 7.0 LCD MONITOR</span>
        <span>1024 x 576 NATIVE (16:9)</span>
      </div>

      <!-- Container: Vertical RED MINI-MAG Slot (Left) + Screen Viewport (Right) -->
      <div class="screen-and-mag-container">
        <!-- RED MINI-MAG Slot Bay -->
        <div class="minimag-slot-bay" id="minimag-bay">
          <div class="mag-header">
            <div class="mag-brand">
              <span class="mag-logo-red">RED</span>
              <span class="mag-label">MINI-MAG&reg;</span>
            </div>
            <div class="mag-slot-tag">SLOT 1 &bull; SATA0</div>
          </div>

          <!-- Physical Cartridge Graphic / Insert Slot -->
          <div class="mag-cartridge" id="mag-cartridge">
            <div class="mag-grip"></div>
            <div class="mag-cartridge-body">
              <div class="mag-cartridge-title">RED MINI-MAG</div>
              <div class="mag-cartridge-cap" id="mag-cartridge-cap">512 GB</div>
              <div class="mag-cartridge-sub">TURBO / R3D RAW</div>
            </div>
            <div class="mag-led-row">
              <div class="mag-led" id="mag-led"></div>
              <span class="mag-led-text" id="mag-led-text">MEDIA READY</span>
            </div>
          </div>

          <!-- Active Firmware Status Box -->
          <div class="mag-fw-card">
            <div class="fw-card-label">ACTIVE FIRMWARE</div>
            <div class="fw-card-name" id="fw-active-name">SCARLET-W 5K (v7.4.1)</div>
            <div class="fw-card-status" id="fw-active-status">Confirmed Working Baseline</div>
            <div class="fw-card-details" id="fw-active-details">dsmc_program.mem &bull; 18.1 MB</div>
          </div>

          <!-- Firmware Upload / Drag-and-Drop Area -->
          <div class="mag-dropzone" id="mag-dropzone" onclick="document.getElementById('fw-file-input').click()" title="Drag &amp; drop custom firmware file (.bin / .mem / .pkg) or click to browse">
            <input type="file" id="fw-file-input" style="display: none;" accept=".bin,.mem,.pkg,.zip,.tar,.gz,.dsmc2" onchange="handleFwFileSelect(this.files)" />
            <div class="dropzone-icon">&uArr;</div>
            <div class="dropzone-text"><strong>Drop Custom Firmware</strong><br>or click to load (.bin, .mem, .pkg)</div>
            <div class="dropzone-hint">Test custom firmware mods</div>
          </div>

          <!-- Slot Action Controls -->
          <div class="mag-controls">
            <button class="cam-btn mag-btn-reset" id="btn-revert-fw" onclick="revertFirmwareBaseline()" title="Reset to confirmed working v7.4.1 firmware">
              &circlearrowleft; Revert to v7.4.1
            </button>
            <button class="cam-btn" style="padding: 5px 8px; font-size: 10px; flex: 1;" onclick="document.getElementById('fw-file-input').click()">
              Load Image...
            </button>
          </div>
        </div>

        <!-- Screen Viewport (Right of Media Slot) -->
        <div class="screen-viewport" id="viewport">
          <!-- Live Video Stream -->
          <img id="stream-img" src="/api/stream" alt="RED DSMC2 LCD Stream" />
          <!-- Interactive Touch Layer -->
          <canvas class="touch-overlay" id="touchCanvas" width="1024" height="576"></canvas>
        </div>
      </div>

      <div class="bezel-screws" style="margin-top: 8px;">
        <div class="screw"></div><div class="screw"></div>
      </div>
    </div>

    <!-- Tactile Deck: Physical Camera Hardware Buttons -->
    <div class="tactile-deck">
      <!-- REC & POWER -->
      <div class="btn-group">
        <button id="rec-btn" class="rec-btn" title="Toggle Recording [R]">
          <span>●</span>
          <span>REC</span>
        </button>
        <button id="pwr-btn" class="cam-btn power-btn" title="Toggle Power / Standby">
          POWER ON
        </button>
      </div>

      <!-- FUNCTION KEYS -->
      <div class="btn-group">
        <button class="cam-btn" onclick="sendBtn('MENU')" title="Toggle Menu [M]">
          MENU <span class="hotkey-badge">M</span>
        </button>
        <button class="cam-btn" onclick="sendBtn('1:1 MAG')" title="Toggle 1:1 Magnify [1]">
          1:1 MAG <span class="hotkey-badge">1</span>
        </button>
        <button class="cam-btn" onclick="sendBtn('USER 1')" title="Toggle False Color [U]">
          USER 1 (FC) <span class="hotkey-badge">U</span>
        </button>
        <button class="cam-btn" onclick="sendBtn('USER 2')" title="Toggle Zebras [Z]">
          USER 2 (ZB) <span class="hotkey-badge">Z</span>
        </button>
      </div>

      <!-- D-PAD -->
      <div class="dpad">
        <div></div>
        <button onclick="sendBtn('NAV UP')">▲</button>
        <div></div>
        <button onclick="sendBtn('NAV LEFT')">◀</button>
        <button onclick="sendBtn('NAV ENTER')">●</button>
        <button onclick="sendBtn('NAV RIGHT')">▶</button>
        <div></div>
        <button onclick="sendBtn('NAV DOWN')">▼</button>
        <div></div>
      </div>
    </div>
  </div>

  <!-- Right: Engineering Workbench -->
  <div class="workbench">
    <div class="tab-bar">
      <button class="tab-btn active" onclick="switchTab('serial', this)">Console / Serial</button>
      <button class="tab-btn" onclick="switchTab('rcp', this)">RCP & Parameters</button>
      <button class="tab-btn" onclick="switchTab('lens', this)">Lens & Optics</button>
      <button class="tab-btn" onclick="switchTab('media', this)">Media & Clips</button>
      <button class="tab-btn" onclick="switchTab('threads', this)">ThreadX RTOS</button>
      <button class="tab-btn" onclick="switchTab('snapshots', this)">Snapshots</button>
      <button class="tab-btn" onclick="switchTab('faults', this)">Fault Injection</button>
    </div>

    <!-- Tab 1: Serial Console -->
    <div id="tab-serial" class="tab-content active">
      <div class="console-tools">
        <div>
          <span>UART4 Serial Stream</span>
          <span id="log-count" style="color: #666; margin-left: 8px;">(0 lines)</span>
        </div>
        <div style="display: flex; gap: 12px; align-items: center;">
          <label style="cursor: pointer;">
            <input type="checkbox" id="auto-scroll" checked /> Auto-scroll
          </label>
          <button class="cam-btn" style="padding: 4px 10px;" onclick="clearConsole()">Clear Log</button>
        </div>
      </div>
      <div id="console" class="console-box"></div>
    </div>

    <!-- Tab 2: RCP Parameters -->
    <div id="tab-rcp" class="tab-content">
      <div style="margin-bottom: 8px; font-size: 12px; color: #aaa;">
        Live RED Command Protocol (RCP) Parameter Cache
      </div>
      <div style="flex: 1; overflow-y: auto;">
        <table class="data-table">
          <thead>
            <tr><th>Parameter</th><th>Value</th><th>Action</th></tr>
          </thead>
          <tbody id="rcp-table-body"></tbody>
        </table>
      </div>
      <div class="cmd-bar">
        <input id="rcp-cmd-input" class="cmd-input" type="text" placeholder="Send RCP command (e.g. GET iso, SET iso 1600)..." onkeydown="if(event.key==='Enter') sendRcp()" />
        <button class="cam-btn" onclick="sendRcp()">Send</button>
      </div>
    </div>

    <!-- Tab: Lens & Optics -->
    <div id="tab-lens" class="tab-content">
      <div style="margin-bottom: 12px; font-size: 12px; color: #aaa;">
        Electronic Lens Mount Interface & Authentic Firmware Lens Database
      </div>
      
      <!-- Attached Lens Card -->
      <div style="background: #181818; border: 1px solid #333; border-radius: 4px; padding: 12px; margin-bottom: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <span style="font-size: 14px; font-weight: bold; color: #00e676;" id="lens-model-txt">Canon EF 24-70mm f/2.8L II USM</span>
          <span style="font-family: monospace; font-size: 11px; color: #888;" id="lens-mount-txt">Canon EF</span>
        </div>
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; font-size: 12px; font-family: monospace; color: #bbb;">
          <div>Brand: <strong id="lens-brand-txt" style="color: #fff;">Canon</strong></div>
          <div>Serial: <strong id="lens-serial-txt" style="color: #fff;">CN-748921</strong></div>
          <div>Focal Range: <strong id="lens-range-txt" style="color: #fff;">24-70mm</strong></div>
          <div>Current Focal: <strong id="lens-focal-txt" style="color: #ffaa00;">50mm</strong></div>
          <div>Current Iris: <strong id="lens-iris-txt" style="color: #00ff66;">f/2.8</strong></div>
          <div>Focus Dist: <strong id="lens-dist-txt" style="color: #66b3ff;">2.50m</strong></div>
        </div>
      </div>

      <!-- Controls -->
      <div style="background: #181818; border: 1px solid #333; border-radius: 4px; padding: 12px; margin-bottom: 14px;">
        <div style="font-weight: 600; font-size: 12px; color: #ddd; margin-bottom: 8px;">Electronic Iris / Aperture Stepping (1/3 EV)</div>
        <div id="lens-stops-container" style="display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px;"></div>
        <div style="display: flex; gap: 8px;">
          <button class="cam-btn" onclick="stepAperture(-1)">◄ Open Up (-1/3 Stop)</button>
          <button class="cam-btn" onclick="stepAperture(1)">Close Down (+1/3 Stop) ►</button>
        </div>
      </div>

      <div style="background: #181818; border: 1px solid #333; border-radius: 4px; padding: 12px; margin-bottom: 14px;">
        <div style="font-weight: 600; font-size: 12px; color: #ddd; margin-bottom: 8px;">Zoom & Focus Motors</div>
        <div style="display: flex; flex-direction: column; gap: 10px;">
          <div style="display: flex; align-items: center; gap: 10px;">
            <label style="font-size: 12px; width: 90px; color: #aaa;">Focal Length:</label>
            <input id="focal-slider" type="range" min="24" max="70" value="50" style="flex: 1;" oninput="onFocalSlider(this.value)" />
            <span id="focal-val-lbl" style="font-family: monospace; font-size: 12px; width: 50px;">50mm</span>
          </div>
          <div style="display: flex; align-items: center; gap: 10px;">
            <label style="font-size: 12px; width: 90px; color: #aaa;">Focus Dist:</label>
            <input id="focus-slider" type="range" min="0.3" max="50.0" step="0.1" value="2.5" style="flex: 1;" oninput="onFocusSlider(this.value)" />
            <span id="focus-val-lbl" style="font-family: monospace; font-size: 12px; width: 50px;">2.5m</span>
            <button class="cam-btn" style="padding: 4px 10px;" onclick="triggerAf()">Trigger AF</button>
          </div>
        </div>
      </div>

      <!-- Mount Swapper & Catalog -->
      <div style="background: #181818; border: 1px solid #333; border-radius: 4px; padding: 12px;">
        <div style="font-weight: 600; font-size: 12px; color: #ddd; margin-bottom: 8px;">Mount Hardware & Authentic Lens Catalog (230+ Models)</div>
        <div style="display: flex; gap: 8px; margin-bottom: 10px;">
          <select id="mount-select" class="cmd-input" style="flex: 1;" onchange="onMountChange(this.value)">
            <option value="Canon EF">Canon EF Smart Mount</option>
            <option value="Nikon F">Nikon F Electronic Mount</option>
            <option value="PL Mount (/i)">PL Intelligent Mount (/i)</option>
          </select>
        </div>
        <div style="display: flex; gap: 8px;">
          <select id="catalog-select" class="cmd-input" style="flex: 1;"></select>
          <button class="cam-btn" onclick="attachSelectedLens()">Attach Lens</button>
        </div>
      </div>
    </div>

    <!-- Tab: Media & Clips -->
    <div id="tab-media" class="tab-content">
      <div style="margin-bottom: 12px; font-size: 12px; color: #aaa;">
        RED MINI-MAG High-Speed Solid State Media & Authentic .R3D / .RMD Clips
      </div>

      <!-- Media Card -->
      <div style="background: #181818; border: 1px solid #333; border-radius: 4px; padding: 12px; margin-bottom: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <span style="font-size: 14px; font-weight: bold; color: #fff;">RED MINI-MAG 512GB (Port SATA0)</span>
          <span id="media-status-badge" style="font-family: monospace; font-size: 11px; padding: 2px 6px; border-radius: 3px; background: #1b3d1b; color: #00ff66;">MOUNTED</span>
        </div>
        <div style="font-size: 12px; font-family: monospace; color: #bbb; margin-bottom: 8px;">
          Reel: <strong id="media-reel-txt" style="color: #fff;">A001</strong> | Filesystem: <strong style="color: #fff;">FAT32 / RED UDF v2.50</strong>
        </div>
        <!-- Progress bar -->
        <div style="height: 14px; background: #252525; border-radius: 3px; overflow: hidden; margin-bottom: 6px; border: 1px solid #444;">
          <div id="media-used-bar" style="height: 100%; width: 22%; background: #00b0ff; transition: width 0.3s;"></div>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 11px; font-family: monospace; color: #888;">
          <span id="media-used-txt">Used: 112.0 GB</span>
          <span id="media-free-txt" style="color: #00e676;">Free: 400.0 GB (78%)</span>
        </div>
        <div style="display: flex; gap: 8px; margin-top: 10px;">
          <button class="cam-btn" onclick="formatMedia()" style="background: #4a1515; border-color: #aa2222; color: #fff;">Format Media</button>
          <button id="media-eject-btn" class="cam-btn" onclick="toggleMediaMount()">Eject Media</button>
        </div>
      </div>

      <!-- Clips Table -->
      <div style="flex: 1; overflow-y: auto; background: #141414; border: 1px solid #2a2a2a; border-radius: 4px;">
        <table class="data-table">
          <thead>
            <tr><th>Clip Container (.RDC)</th><th>Size</th><th>R3D Format</th><th>Action</th></tr>
          </thead>
          <tbody id="clips-table-body"></tbody>
        </table>
      </div>

      <!-- RMD Viewer Box -->
      <div id="rmd-viewer-container" style="display: none; margin-top: 10px; background: #0a0a0a; border: 1px solid #444; border-radius: 4px; padding: 10px; max-height: 180px; overflow-y: auto;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
          <span style="font-size: 11px; color: #aaa; font-weight: bold;">RED Metadata XML Sidecar (.RMD)</span>
          <button class="cam-btn" style="padding: 1px 6px; font-size: 10px;" onclick="closeRmdViewer()">Close</button>
        </div>
        <pre id="rmd-viewer-content" style="font-family: monospace; font-size: 11px; color: #ffaa00; white-space: pre-wrap; margin: 0;"></pre>
      </div>
    </div>

    <!-- Tab 3: ThreadX RTOS Inspector -->
    <div id="tab-threads" class="tab-content">
      <div style="margin-bottom: 8px; font-size: 12px; color: #aaa;">
        Detected ThreadX RTOS Threads & Schedular State
      </div>
      <div style="flex: 1; overflow-y: auto;">
        <table class="data-table">
          <thead>
            <tr><th>Address</th><th>Thread Name</th><th>State</th><th>Priority</th><th>SP</th><th>Stack Range</th></tr>
          </thead>
          <tbody id="threads-table-body"></tbody>
        </table>
      </div>
    </div>

    <!-- Tab 4: Snapshots -->
    <div id="tab-snapshots" class="tab-content">
      <div style="margin-bottom: 12px; font-size: 12px; color: #aaa;">
        Full-system state snapshot save and instantaneous restoration.
      </div>
      <div style="display: flex; gap: 10px; margin-bottom: 16px;">
        <input id="snap-name-input" class="cmd-input" type="text" placeholder="snapshot_01.dsmc2" />
        <button class="cam-btn" onclick="saveSnapshot()">Save Snapshot</button>
      </div>
      <div id="snap-result" style="font-family: monospace; font-size: 12px; color: #00ff66;"></div>
    </div>

    <!-- Tab 5: Hardware Faults -->
    <div id="tab-faults" class="tab-content">
      <div style="margin-bottom: 12px; font-size: 12px; color: #aaa;">
        Inject hardware faults to verify firmware resilience and exception handlers.
      </div>
      <div style="display: flex; flex-direction: column; gap: 10px;">
        <button class="cam-btn" onclick="injectFault('INTC_IRQ_LOCKUP')">Inject INTC IRQ Lockup (IRQ 58 Touch Storm)</button>
        <button class="cam-btn" onclick="injectFault('DDR_ECC_CORRUPT')">Inject DDR ECC Single-Bit Memory Corruption</button>
        <button class="cam-btn" onclick="injectFault('GPTIMER_DRIFT')">Inject GPTimer System Clock Drift (+15%)</button>
      </div>
      <div id="fault-result" style="font-family: monospace; font-size: 12px; color: #ffaa00; margin-top: 14px;"></div>
    </div>

  </div>

</div>

<script>
  let lastLogIndex = 0;
  let isTouching = false;
  const canvas = document.getElementById('touchCanvas');
  const ctx = canvas.getContext('2d');
  const viewport = document.getElementById('viewport');

  // Map mouse/touch pointer to native 1024x576 coordinates
  function getNativeCoords(e) {
    const rect = canvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    const x = Math.round((clientX - rect.left) * (1024 / rect.width));
    const y = Math.round((clientY - rect.top) * (576 / rect.height));
    return {
      x: Math.max(0, Math.min(1023, x)),
      y: Math.max(0, Math.min(575, y))
    };
  }

  // Draw local high-responsiveness touch reticle
  function drawReticle(x, y) {
    ctx.clearRect(0, 0, 1024, 576);
    ctx.strokeStyle = '#ff3333';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 22, 0, Math.PI * 2);
    ctx.stroke();

    ctx.strokeStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(x, y, 14, 0, Math.PI * 2);
    ctx.stroke();

    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  }

  function clearReticle() {
    ctx.clearRect(0, 0, 1024, 576);
  }

  // Touch Event Listeners
  canvas.addEventListener('mousedown', (e) => {
    isTouching = true;
    const {x, y} = getNativeCoords(e);
    drawReticle(x, y);
    sendTouch(x, y, true, 'down');
  });

  window.addEventListener('mousemove', (e) => {
    if (!isTouching) return;
    const {x, y} = getNativeCoords(e);
    drawReticle(x, y);
    sendTouch(x, y, true, 'move');
  });

  window.addEventListener('mouseup', (e) => {
    if (!isTouching) return;
    isTouching = false;
    const {x, y} = getNativeCoords(e);
    sendTouch(x, y, false, 'up');
    setTimeout(clearReticle, 200);
  });

  // Mobile / Touchscreen Support
  canvas.addEventListener('touchstart', (e) => {
    e.preventDefault();
    isTouching = true;
    const {x, y} = getNativeCoords(e);
    drawReticle(x, y);
    sendTouch(x, y, true, 'down');
  }, {passive: false});

  canvas.addEventListener('touchmove', (e) => {
    e.preventDefault();
    if (!isTouching) return;
    const {x, y} = getNativeCoords(e);
    drawReticle(x, y);
    sendTouch(x, y, true, 'move');
  }, {passive: false});

  canvas.addEventListener('touchend', (e) => {
    e.preventDefault();
    isTouching = false;
    sendTouch(0, 0, false, 'up');
    setTimeout(clearReticle, 200);
  });

  // API Dispatchers
  function sendTouch(x, y, pressed, action = 'down') {
    fetch('/api/touch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({x, y, pressed, action})
    }).catch(() => {});
  }

  function sendBtn(name) {
    fetch('/api/button', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({button: name})
    }).catch(() => {});
  }

  document.getElementById('rec-btn').addEventListener('click', () => sendBtn('REC'));
  document.getElementById('pwr-btn').addEventListener('click', () => {
    fetch('/api/power', {method: 'POST'}).catch(() => {});
  });

  // Keyboard Shortcuts
  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT') return;
    const key = e.key.toUpperCase();
    if (key === 'M') sendBtn('MENU');
    else if (key === 'R') sendBtn('REC');
    else if (key === '1') sendBtn('1:1 MAG');
    else if (key === 'U') sendBtn('USER 1');
    else if (key === 'Z') sendBtn('USER 2');
    else if (e.key === 'ArrowUp') sendBtn('NAV UP');
    else if (e.key === 'ArrowDown') sendBtn('NAV DOWN');
    else if (e.key === 'ArrowLeft') sendBtn('NAV LEFT');
    else if (e.key === 'ArrowRight') sendBtn('NAV RIGHT');
    else if (e.key === 'Enter') sendBtn('NAV ENTER');
  });

  // Tab switching
  function switchTab(name, btn) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${name}`).classList.add('active');
  }

  function clearConsole() {
    document.getElementById('console').textContent = '';
  }

  // Periodic Polling Loop for Logs & Camera Telemetry
  async function pollStatus() {
    try {
      // 1. Status telemetry
      const res = await fetch('/api/status');
      const data = await res.json();

      // Power state
      const pwrText = document.getElementById('pwr-text');
      const pwrBtn = document.getElementById('pwr-btn');
      const ledDot = document.getElementById('led-dot');
      const recBtn = document.getElementById('rec-btn');

      if (data.power_state === 'STANDBY') {
        pwrText.textContent = 'STANDBY';
        pwrBtn.textContent = 'POWER ON';
        pwrBtn.style.color = '#66ff88';
        ledDot.className = 'led-dot';
      } else if (data.power_state === 'BOOTING') {
        const pct = Math.round(data.boot_progress * 100);
        pwrText.textContent = `BOOTING ${pct}%`;
        pwrBtn.textContent = 'BOOTING...';
        ledDot.className = 'led-dot';
      } else {
        pwrText.textContent = 'ONLINE';
        pwrBtn.textContent = 'POWER OFF';
        pwrBtn.style.color = '#ccc';
        ledDot.className = 'led-dot online';
      }

      // Camera Model Name update
      if (data.camera_name) {
        const hdrName = document.getElementById('hdr-model-name');
        if (hdrName) hdrName.textContent = data.camera_name;
        const subName = document.getElementById('hdr-sub-name');
        if (subName) subName.textContent = data.camera_name;
      }

      // Recording badge & button state
      if (data.is_recording) {
        recBtn.classList.add('recording');
        recBtn.innerHTML = '<span>■</span><span>STOP</span>';
        ledDot.className = 'led-dot recording';
      } else {
        recBtn.classList.remove('recording');
        recBtn.innerHTML = '<span>●</span><span>REC</span>';
      }

      // Instruction counter
      const instrM = (data.instruction_count / 1000000).toFixed(1);
      document.getElementById('instr-text').textContent = `${instrM}M INSTR`;

      // RCP Connected Clients
      if (document.getElementById('rcp-clients')) {
        const cCount = data.rcp_clients_count || 0;
        document.getElementById('rcp-clients').textContent = `${cCount} client${cCount === 1 ? '' : 's'}`;
      }

      // Render RCP table
      renderRcp(data.rcp);

      // Render Threads table
      renderThreads(data.threads);

      // Render Lens Telemetry
      if (data.lens) {
        renderLens(data.lens);
      }

      // Render Media Telemetry
      if (data.media) {
        renderMedia(data.media);
      }

      // Render Firmware Telemetry
      if (data.firmware) {
        updateFirmwareDisplay(data.firmware);
      }

    } catch (e) {}

    // 2. Serial logs
    try {
      const res = await fetch(`/api/logs?since=${lastLogIndex}`);
      const data = await res.json();
      if (data.logs && data.logs.length > 0) {
        const box = document.getElementById('console');
        box.textContent += data.logs.join('\n') + '\n';
        lastLogIndex = data.total;
        document.getElementById('log-count').textContent = `(${data.total} lines)`;
        if (document.getElementById('auto-scroll').checked) {
          box.scrollTop = box.scrollHeight;
        }
      }
    } catch (e) {}

    setTimeout(pollStatus, 250);
  }

  function renderRcp(rcp) {
    const tbody = document.getElementById('rcp-table-body');
    if (!tbody || !rcp) return;
    let html = '';
    for (const [key, val] of Object.entries(rcp)) {
      html += `<tr>
        <td style="color: #66b3ff;">${key}</td>
        <td style="color: #fff; font-weight: bold;">${val}</td>
        <td><button class="cam-btn" style="padding: 2px 6px; font-size: 10px;" onclick="promptSetRcp('${key}', '${val}')">Edit</button></td>
      </tr>`;
    }
    tbody.innerHTML = html;
  }

  function promptSetRcp(param, curr) {
    const newVal = prompt(`Enter new value for ${param}:`, curr);
    if (newVal !== null) {
      fetch('/api/rcp', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({command: `SET ${param} ${newVal}`})
      });
    }
  }

  function renderThreads(threads) {
    const tbody = document.getElementById('threads-table-body');
    if (!tbody || !threads) return;
    let html = '';
    for (const t of threads) {
      const stateColor = (t.state === 'READY') ? '#00e676' : '#ffb300';
      html += `<tr>
        <td>${t.address}</td>
        <td style="color: #fff; font-weight: bold;">${t.name}</td>
        <td style="color: ${stateColor}; font-weight: bold;">${t.state}</td>
        <td>${t.priority}</td>
        <td>${t.sp}</td>
        <td style="color: #888;">${t.stack_range}</td>
      </tr>`;
    }
    tbody.innerHTML = html;
  }

  function sendRcp() {
    const input = document.getElementById('rcp-cmd-input');
    const cmd = input.value.trim();
    if (!cmd) return;
    fetch('/api/rcp', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({command: cmd})
    }).then(r => r.json()).then(res => {
      input.value = '';
    });
  }

  function saveSnapshot() {
    const name = document.getElementById('snap-name-input').value.trim() || undefined;
    fetch('/api/snapshot/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name})
    }).then(r => r.json()).then(res => {
      document.getElementById('snap-result').textContent = `Snapshot saved: ${res.file}`;
    });
  }

  function injectFault(type) {
    fetch('/api/fault', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({type})
    }).then(r => r.json()).then(res => {
      document.getElementById('fault-result').textContent = `Fault injected [${type}]: ${JSON.stringify(res.result)}`;
    });
  }

  function renderLens(lens) {
    if (!document.getElementById('lens-model-txt')) return;
    document.getElementById('lens-model-txt').textContent = lens.attached ? lens.lens.model : 'NO LENS DETECTED';
    document.getElementById('lens-mount-txt').textContent = lens.mount_type;
    document.getElementById('lens-brand-txt').textContent = lens.attached ? lens.lens.brand : 'None';
    document.getElementById('lens-serial-txt').textContent = lens.attached ? lens.lens.serial : 'N/A';
    document.getElementById('lens-range-txt').textContent = lens.attached ? lens.lens.focal_display : '--';
    document.getElementById('lens-focal-txt').textContent = lens.focal_display;
    document.getElementById('lens-iris-txt').textContent = lens.aperture_display;
    document.getElementById('lens-dist-txt').textContent = lens.focus_display;

    // Render aperture stops buttons if changed
    const stopsBox = document.getElementById('lens-stops-container');
    if (stopsBox && lens.available_stops && stopsBox.dataset.lastCount != lens.available_stops.length) {
      stopsBox.dataset.lastCount = lens.available_stops.length;
      let html = '';
      for (const s of lens.available_stops) {
        const isCur = Math.abs(s - lens.current_aperture) < 0.05;
        const bg = isCur ? '#b01010' : '#2a2a2a';
        const col = isCur ? '#fff' : '#ccc';
        html += `<button class="cam-btn" style="padding: 3px 8px; font-size: 11px; background: ${bg}; color: ${col};" onclick="setAperture(${s})">f/${s.toFixed(1)}</button>`;
      }
      stopsBox.innerHTML = html;
    }

    // Sync sliders if not focused
    const fSlider = document.getElementById('focal-slider');
    if (fSlider && document.activeElement !== fSlider && lens.attached) {
      fSlider.min = lens.lens.focal_min;
      fSlider.max = lens.lens.focal_max;
      fSlider.value = lens.current_focal_length;
      document.getElementById('focal-val-lbl').textContent = `${Math.round(lens.current_focal_length)}mm`;
    }
  }

  function renderMedia(media) {
    if (!document.getElementById('media-reel-txt')) return;
    document.getElementById('media-reel-txt').textContent = media.reel_id;
    document.getElementById('media-used-bar').style.width = `${100 - media.remaining_percent}%`;
    document.getElementById('media-used-txt').textContent = `Used: ${(media.used_mb / 1024).toFixed(1)} GB`;
    document.getElementById('media-free-txt').textContent = `Free: ${(media.free_mb / 1024).toFixed(1)} GB (${media.remaining_percent}%)`;
    
    const badge = document.getElementById('media-status-badge');
    const ejectBtn = document.getElementById('media-eject-btn');
    if (media.is_mounted) {
      badge.textContent = 'MOUNTED';
      badge.style.background = '#1b3d1b';
      badge.style.color = '#00ff66';
      ejectBtn.textContent = 'Eject Media';
    } else {
      badge.textContent = 'EJECTED';
      badge.style.background = '#4a1515';
      badge.style.color = '#ff6666';
      ejectBtn.textContent = 'Mount Media';
    }
  }

  function setAperture(val) {
    fetch('/api/lens/aperture', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({aperture: val})
    });
  }

  function stepAperture(step) {
    fetch('/api/lens/aperture', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({step})
    });
  }

  function onFocalSlider(val) {
    document.getElementById('focal-val-lbl').textContent = `${val}mm`;
    fetch('/api/lens/focal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({focal_length: parseFloat(val)})
    });
  }

  function onFocusSlider(val) {
    document.getElementById('focus-val-lbl').textContent = `${parseFloat(val).toFixed(1)}m`;
    fetch('/api/lens/focus', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({distance: parseFloat(val)})
    });
  }

  function triggerAf() {
    fetch('/api/lens/af', {method: 'POST'});
  }

  function onMountChange(mount) {
    fetch('/api/lens/mount', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mount})
    }).then(() => loadLensCatalog());
  }

  function attachSelectedLens() {
    const sel = document.getElementById('catalog-select');
    if (!sel || sel.selectedIndex < 0) return;
    const name = sel.value;
    fetch('/api/lens/attach', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name})
    });
  }

  let catalogLoaded = false;
  function loadLensCatalog() {
    fetch('/api/lens').then(r => r.json()).then(res => {
      const sel = document.getElementById('catalog-select');
      if (!sel || !res.catalog) return;
      sel.innerHTML = '';
      const currMount = document.getElementById('mount-select').value;
      const filtered = res.catalog.filter(l => l.mount === currMount || currMount === 'All');
      filtered.forEach((l, idx) => {
        const opt = document.createElement('option');
        opt.value = l.model;
        opt.textContent = `${l.model} (${l.brand})`;
        sel.appendChild(opt);
      });
      catalogLoaded = true;
    });
  }

  function formatMedia() {
    if (!confirm('Format RED MINI-MAG 512GB? All clips on the current reel will be cleared.')) return;
    fetch('/api/media/format', {method: 'POST'}).then(() => loadClips());
  }

  function toggleMediaMount() {
    const badge = document.getElementById('media-status-badge');
    const isMounted = badge.textContent === 'MOUNTED';
    fetch(isMounted ? '/api/media/eject' : '/api/media/mount', {method: 'POST'}).then(() => loadClips());
  }

  function loadClips() {
    fetch('/api/media/clips').then(r => r.json()).then(res => {
      const tbody = document.getElementById('clips-table-body');
      if (!tbody || !res.clips) return;
      if (res.clips.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="color: #666; text-align: center; padding: 20px;">No R3D clips recorded on current reel.</td></tr>';
        return;
      }
      let html = '';
      window._cachedClips = res.clips;
      res.clips.forEach((c, idx) => {
        html += `<tr>
          <td style="color: #fff; font-weight: bold;">${c.clip_name}.RDC</td>
          <td style="color: #ffaa00;">${c.size_mb} MB</td>
          <td style="color: #66b3ff;">REDCODE RAW 5K</td>
          <td><button class="cam-btn" style="padding: 2px 6px; font-size: 10px;" onclick="viewRmd(${idx})">View RMD</button></td>
        </tr>`;
      });
      tbody.innerHTML = html;
    });
  }

  function viewRmd(idx) {
    if (!window._cachedClips || !window._cachedClips[idx]) return;
    const clip = window._cachedClips[idx];
    document.getElementById('rmd-viewer-content').textContent = clip.rmd_metadata || 'No metadata available.';
    document.getElementById('rmd-viewer-container').style.display = 'block';
  }

  function closeRmdViewer() {
    document.getElementById('rmd-viewer-container').style.display = 'none';
  }

  // RED MINI-MAG Firmware Slot Drag & Drop and Upload
  const dropzone = document.getElementById('mag-dropzone');
  if (dropzone) {
    ['dragenter', 'dragover'].forEach(name => {
      dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
      });
    });
    ['dragleave', 'drop'].forEach(name => {
      dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
      });
    });
    dropzone.addEventListener('drop', (e) => {
      if (e.dataTransfer && e.dataTransfer.files.length > 0) {
        handleFwFileSelect(e.dataTransfer.files);
      }
    });
  }

  function handleFwFileSelect(files) {
    if (!files || files.length === 0) return;
    const file = files[0];
    const reader = new FileReader();
    reader.onload = function(e) {
      const b64Data = e.target.result;
      fetch('/api/firmware/upload', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({filename: file.name, data: b64Data})
      }).then(r => r.json()).then(res => {
        if (res.status === 'ok') {
          updateFirmwareDisplay({
            active_name: res.active_name,
            active_file: res.filename,
            active_size: res.size,
            is_custom: true
          });
        }
      });
    };
    reader.readAsDataURL(file);
  }

  function revertFirmwareBaseline() {
    fetch('/api/firmware/reset', {method: 'POST'}).then(r => r.json()).then(res => {
      if (res.status === 'ok') {
        updateFirmwareDisplay({
          active_name: res.active_name,
          active_file: 'dsmc_program.mem',
          active_size: 18972672,
          is_custom: false
        });
      }
    });
  }

  function updateFirmwareDisplay(fw) {
    if (!fw) return;
    const nameEl = document.getElementById('fw-active-name');
    const statusEl = document.getElementById('fw-active-status');
    const detailsEl = document.getElementById('fw-active-details');
    const ledEl = document.getElementById('mag-led');
    const ledText = document.getElementById('mag-led-text');

    if (nameEl) nameEl.textContent = fw.active_name;
    if (statusEl) statusEl.textContent = fw.is_custom ? 'Custom Mod Staged' : 'Confirmed Working Baseline';
    if (detailsEl) {
      const mb = (fw.active_size / (1024 * 1024)).toFixed(1);
      detailsEl.textContent = `${fw.active_file} • ${mb} MB`;
    }
    if (ledEl) {
      if (fw.is_custom) {
        ledEl.className = 'mag-led custom';
        if (ledText) ledText.textContent = 'CUSTOM MOD LOADED';
        if (nameEl) nameEl.style.color = '#00b0ff';
      } else {
        ledEl.className = 'mag-led';
        if (ledText) ledText.textContent = 'MEDIA READY';
        if (nameEl) nameEl.style.color = '#00e676';
      }
    }
  }

  // DSMC2 Camera Selection Modal Functions
  async function openModelModal() {
    const modal = document.getElementById('camera-modal');
    if (modal) {
      modal.style.display = 'flex';
      await renderModelCards();
    }
  }

  function closeModelModal() {
    const modal = document.getElementById('camera-modal');
    if (modal) modal.style.display = 'none';
  }

  async function renderModelCards() {
    try {
      const res = await fetch('/api/camera/models');
      const data = await res.json();
      const container = document.getElementById('camera-model-grid');
      if (!container) return;
      container.innerHTML = '';

      data.models.forEach(m => {
        const card = document.createElement('div');
        card.className = `cam-card ${m.is_active ? 'active' : ''}`;
        card.onclick = () => selectCameraModel(m.CAMERA_TYPE);
        card.innerHTML = `
          <div>
            <div class="cam-card-top">
              <span class="cam-card-name">${m.CAMERA_NAME}</span>
              <span class="cam-card-id">TAG ${m.CAMERA_TYPE}</span>
            </div>
            <div class="cam-card-sensor">${m.SENSOR_NAME}</div>
            <div class="cam-card-desc" style="margin-top: 8px;">${m.DESCRIPTION}</div>
          </div>
          <div>
            <div class="cam-card-specs" style="margin-bottom: 8px;">
              <span class="spec-pill">${m.SENSOR_WIDTH}x${m.SENSOR_HEIGHT}</span>
              <span class="spec-pill">Max ${m.MAX_FPS} FPS</span>
              <span class="spec-pill">${m.DEFAULT_FORMAT}</span>
            </div>
            <div class="cam-card-btn">${m.is_active ? '✓ ACTIVE CAMERA' : 'SELECT & BOOT'}</div>
          </div>
        `;
        container.appendChild(card);
      });
    } catch (e) {
      console.error('Error rendering model cards:', e);
    }
  }

  async function selectCameraModel(modelId) {
    try {
      const res = await fetch('/api/camera/select', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({model: modelId})
      });
      const data = await res.json();
      if (data.status === 'ok') {
        localStorage.setItem('dsmc2_model_initialized', 'true');
        closeModelModal();
        pollStatus();
      }
    } catch (e) {
      console.error('Error selecting camera model:', e);
    }
  }

  // Load initial lens catalog and media clips on startup
  loadLensCatalog();
  loadClips();
  setInterval(loadClips, 2000);

  // Auto-prompt camera chooser on initial session if not initialized
  if (!localStorage.getItem('dsmc2_model_initialized')) {
    setTimeout(openModelModal, 400);
  }

  // Start telemetry loop
  pollStatus();
</script>

<!-- DSMC2 Camera Selection Modal -->
<div id="camera-modal" class="modal-backdrop" style="display: none;">
  <div class="modal-box">
    <div class="modal-top">
      <div style="display: flex; align-items: center;">
        <span class="dsmc2-badge">DSMC2 BRAIN</span>
        <h2 style="font-size: 17px; letter-spacing: 1.5px; margin: 0; color: #fff;">SELECT CAMERA SYSTEM</h2>
      </div>
      <button class="modal-x-btn" onclick="closeModelModal()">✕</button>
    </div>
    <div class="modal-intro">
      Choose a genuine RED DSMC2 hardware personality and firmware package to boot into the virtual camera studio.
    </div>
    <div class="camera-model-grid" id="camera-model-grid">
      <!-- Populated via renderModelCards() -->
    </div>
    <div class="modal-bottom">
      <div class="modal-foot-note">Firmware assets extracted from stock RED Combo Build v7.4.1.</div>
      <button class="cam-btn" onclick="closeModelModal()" style="padding: 7px 16px;">Continue with Current</button>
    </div>
  </div>
</div>
</body>
</html>
"""


def find_free_port(start_port: int = 8765, max_attempts: int = 20) -> int:
    """Find a free TCP port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start_port


def launch_web_studio(workspace_root: str, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True):
    """Launch the Web-Based RED DSMC2 Virtual Camera Studio."""
    free_port = find_free_port(port)
    studio = WebStudio(workspace_root, host=host, port=free_port)

    # Start camera worker
    studio.start_camera()

    # Create multi-threaded HTTP server
    handler_class = create_handler(studio)
    studio.http_server = ThreadedHTTPServer((host, free_port), handler_class)

    url = f"http://{host}:{free_port}/"
    print("\n" + "=" * 70, flush=True)
    print(f"  RED DSMC2 VIRTUAL CAMERA STUDIO ONLINE", flush=True)
    print(f"  URL: {url}", flush=True)
    print(f"  Platform: Standard Library Web Studio (Port {free_port})", flush=True)
    print(f"  Firmware: RED SCARLET-W 5K (v7.4.1)", flush=True)
    print(f"  Ready for interactive touchscreen, physical buttons & RCP control.", flush=True)
    print("=" * 70 + "\n", flush=True)

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        studio.http_server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Stopping Virtual Camera Studio...", flush=True)
    finally:
        studio.stop()


if __name__ == "__main__":
    root_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    launch_web_studio(root_dir)
