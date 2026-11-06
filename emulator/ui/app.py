"""
RED DSMC2 Virtual Camera Engineering Application.
Integrates Virtual RED Camera Zone (Screen + Physical Buttons) and
Emulator Developer Tools (Console, RCP, RTOS Inspector, MMIO, Faults, Snapshots).
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from emulator.camera import Dsmc2Camera
from emulator.ui.screen import CameraScreen
from emulator.ui.buttons import PhysicalButtons
from emulator.ui.dashboard import DeveloperDashboard
from emulator.ui.runtime import EmulatorRuntime


class Dsmc2DesktopApp:
    """Main Application Window."""

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)
        self.camera = Dsmc2Camera(self.workspace_root)

        # Initialize root window
        self.root = tk.Tk()
        self.root.title("RED DSMC2 Full-System Emulator — Virtual Camera & Engineering Workbench")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 750)
        self.root.configure(bg="#1E1E1E")

        # Styling
        self._setup_style()

        # Build Main Panels
        self._build_layout()

        # Build Menu Bar
        self._build_menu()

        # Track initialization of dashboard tabs
        self._dashboard_initialized = False

        # Window closing handler
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Background emulator runtime (moves CPU stepping and frame composition off the UI thread)
        self.runtime = EmulatorRuntime(self.camera, compositor=self.screen.compose_frame)
        self.screen.runtime = self.runtime
        self._last_frame_seq = 0

        # Initial power on and start runtime worker
        self.camera.power_on()
        self.runtime.start()
        self.dashboard.refresh_threads()
        self.dashboard.refresh_rcp()
        self.dashboard.update_logs()
        self.buttons.refresh_state()
        self.screen.refresh()

        # Start UI periodic update loop (~30 FPS)
        self.root.after(30, self._ui_loop)

    def _setup_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background="#1E1E1E", foreground="#CCCCCC", font=("Segoe UI", 9))
        style.configure("TNotebook", background="#181818", borderwidth=0)
        style.configure("TNotebook.Tab", background="#282828", foreground="#AAAAAA", padding=[10, 4])
        style.map("TNotebook.Tab", background=[("selected", "#383838")], foreground=[("selected", "#FFFFFF")])
        style.configure("Treeview", background="#222222", foreground="#EEEEEE", fieldbackground="#222222", rowheight=22)
        style.configure("Treeview.Heading", background="#2F2F2F", foreground="#FFFFFF", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#0E4D92")])

    def _build_menu(self):
        menubar = tk.Menu(self.root, bg="#2B2B2B", fg="#FFFFFF")

        # File Menu
        file_menu = tk.Menu(menubar, tearoff=0, bg="#2B2B2B", fg="#FFFFFF")
        file_menu.add_command(label="Open Firmware Slot...", command=self._open_firmware)
        file_menu.add_separator()
        file_menu.add_command(label="Save Snapshot...", command=lambda: self.dashboard._save_snapshot_dialog())
        file_menu.add_command(label="Restore Snapshot...", command=lambda: self.dashboard._restore_snapshot_dialog())
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        # Camera Menu
        cam_menu = tk.Menu(menubar, tearoff=0, bg="#2B2B2B", fg="#FFFFFF")
        cam_menu.add_command(label="Power On / Boot", command=self._power_on)
        cam_menu.add_command(label="Power Off / Standby", command=self._power_off)
        cam_menu.add_separator()
        cam_menu.add_command(label="Toggle Record (REC)", command=self.camera.press_record)
        cam_menu.add_separator()
        cam_menu.add_command(label="Format SSD / Media", command=lambda: self._set_param("MEDIA_FORMAT", "OK"))
        menubar.add_cascade(label="Camera", menu=cam_menu)

        # Tools Menu
        tools_menu = tk.Menu(menubar, tearoff=0, bg="#2B2B2B", fg="#FFFFFF")
        tools_menu.add_command(label="Refresh ThreadX RTOS", command=lambda: self.dashboard.refresh_threads())
        tools_menu.add_command(label="Refresh MMIO Tracker", command=lambda: self.dashboard.refresh_mmio())
        tools_menu.add_separator()
        tools_menu.add_command(label="Export Boot Trace to JSON...", command=self._export_trace)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        self.root.config(menu=menubar)

    def _build_layout(self):
        # Master PanedWindow dividing Virtual Camera (Left) and Developer Tools (Right)
        self.paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left Pane: Virtual Camera Zone
        left_frame = ttk.Frame(self.paned)
        self.paned.add(left_frame, weight=3)

        # Screen widget
        self.screen = CameraScreen(left_frame, self.camera, scale=0.75)
        self.screen.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Physical buttons widget
        self.buttons = PhysicalButtons(left_frame, self.camera, on_action_callback=self._on_button_action)
        self.buttons.pack(side=tk.BOTTOM, fill=tk.X)

        # Right Pane: Developer Dashboard Zone
        right_frame = ttk.Frame(self.paned)
        self.paned.add(right_frame, weight=2)

        self.dashboard = DeveloperDashboard(right_frame, self.camera)
        self.dashboard.pack(fill=tk.BOTH, expand=True)

    def _on_button_action(self, action: str):
        # Route physical button or GPIO pin to RedUi
        pin_names = {
            10: "MENU", 11: "1:1 MAG", 14: "USER 1", 15: "USER 2",
            20: "NAV UP", 21: "NAV LEFT", 22: "NAV ENTER", 23: "NAV RIGHT", 24: "NAV DOWN"
        }
        if action.startswith("gpio_pin_"):
            try:
                pin = int(action.split("_")[-1])
                btn_name = pin_names.get(pin)
                if btn_name:
                    self.screen.handle_button(btn_name)
            except Exception:
                pass
        elif action == "record_toggled":
            self.runtime.submit(self.camera.press_record)
        elif action == "power_toggled":
            self.runtime.submit(self.camera.toggle_power)

        self.buttons.refresh_state()
        self.dashboard.update_logs()

    def _power_on(self):
        self.runtime.submit(self.camera.power_on)
        self.runtime.invalidate()
        self.buttons.refresh_state()
        self.dashboard.update_logs()

    def _power_off(self):
        self.runtime.submit(self.camera.power_off)
        self.runtime.invalidate()
        self.buttons.refresh_state()
        self.dashboard.update_logs()

    def _set_param(self, param: str, val: str):
        self.runtime.submit(self.camera.rcp.process_command, f"SET / PARAM {param} {val}")
        self.runtime.invalidate()
        self.dashboard.refresh_rcp()

    def _open_firmware(self):
        folder = filedialog.askdirectory(
            title="Select Firmware Slot Directory",
            initialdir=os.path.join(self.workspace_root, "firmware_store")
        )
        if folder:
            mem_path = os.path.join(folder, "dsmc_program.mem")
            if os.path.exists(mem_path):
                self.camera.stop()
                self.camera.load_firmware(mem_path)
                self.camera.start()
                messagebox.showinfo("Firmware Loaded", f"Loaded firmware from:\n{folder}")
            else:
                messagebox.showerror("Error", "Selected directory does not contain dsmc_program.mem")

    def _export_trace(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Trace", "*.json"), ("All Files", "*.*")]
        )
        if path:
            import json
            trace_data = {
                "instruction_count": self.camera.instruction_count,
                "pc": f"0x{self.camera.cpu.pc:08X}",
                "threads": self.camera.get_threads(),
                "unknown_mmio": {f"0x{k:08X}": v for k, v in self.camera.bus.unknown_mmio.items()},
                "serial_logs": self.camera.get_serial_logs()
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(trace_data, f, indent=2)
            messagebox.showinfo("Export Complete", f"Boot trace exported to:\n{path}")

    def _ui_loop(self):
        """Periodic UI update loop on main thread (renders blits only)."""
        try:
            self.runtime.poll_results()
            seq, img = self.runtime.latest_frame()
            if seq != self._last_frame_seq and img is not None:
                self.screen.blit_image(img)
                self._last_frame_seq = seq
            self.buttons.refresh_state()
            self.dashboard.update_logs()
        except Exception:
            pass
        finally:
            self.root.after(30, self._ui_loop)

    def _on_close(self):
        self.runtime.stop()
        self.camera.stop()
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


def launch_gui(workspace_root: str):
    app = Dsmc2DesktopApp(workspace_root)
    app.run()


if __name__ == "__main__":
    root_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    launch_gui(root_dir)
