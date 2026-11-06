"""
Physical tactile button controls for RED DSMC2 Virtual Camera.
Includes Record, Power, Menu, 1:1 Magnify, Nav D-Pad, and User Keys.
"""

import tkinter as tk
from tkinter import ttk


class PhysicalButtons(ttk.Frame):
    """
    Physical-style camera buttons modeled on the DSMC2 Sidekick / Top Handle / Brain body.
    """

    def __init__(self, parent, camera, on_action_callback=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.camera = camera
        self.on_action = on_action_callback

        self._build_widgets()

    def _build_widgets(self):
        panel = tk.Frame(self, bg="#202020", padx=10, pady=8, relief=tk.RAISED, bd=2)
        panel.pack(fill=tk.X, expand=True, padx=5, pady=5)

        # Left Section: Primary Controls (REC & POWER)
        left_box = tk.Frame(panel, bg="#202020")
        left_box.pack(side=tk.LEFT, padx=10)

        # Big Distinctive RED Record Button
        self.rec_btn = tk.Button(
            left_box,
            text="● REC",
            bg="#990000",
            fg="#FFFFFF",
            activebackground="#FF2222",
            activeforeground="#FFFFFF",
            font=("Helvetica", 11, "bold"),
            relief=tk.RAISED,
            bd=3,
            width=8,
            height=2,
            command=self._on_rec_click
        )
        self.rec_btn.pack(side=tk.LEFT, padx=5)

        # Power / Standby Button
        self.power_btn = tk.Button(
            left_box,
            text="POWER\nSTANDBY",
            bg="#333333",
            fg="#DDDDDD",
            font=("Helvetica", 8, "bold"),
            relief=tk.RAISED,
            bd=2,
            width=8,
            height=2,
            command=self._on_power_click
        )
        self.power_btn.pack(side=tk.LEFT, padx=5)

        # Middle Section: Standard Function Keys (MENU, 1:1 MAG, USER1, USER2)
        mid_box = tk.Frame(panel, bg="#202020")
        mid_box.pack(side=tk.LEFT, padx=15)

        self.menu_btn = tk.Button(
            mid_box,
            text="MENU",
            bg="#2B2B2B",
            fg="#CCCCCC",
            font=("Helvetica", 9, "bold"),
            width=7,
            command=lambda: self._press_gpio(pin=10)
        )
        self.menu_btn.pack(side=tk.LEFT, padx=3)

        self.mag_btn = tk.Button(
            mid_box,
            text="1:1 MAG",
            bg="#2B2B2B",
            fg="#CCCCCC",
            font=("Helvetica", 9, "bold"),
            width=7,
            command=lambda: self._press_gpio(pin=11)
        )
        self.mag_btn.pack(side=tk.LEFT, padx=3)

        self.u1_btn = tk.Button(
            mid_box,
            text="USER 1",
            bg="#2B2B2B",
            fg="#CCCCCC",
            font=("Helvetica", 9, "bold"),
            width=7,
            command=lambda: self._press_gpio(pin=14)
        )
        self.u1_btn.pack(side=tk.LEFT, padx=3)

        self.u2_btn = tk.Button(
            mid_box,
            text="USER 2",
            bg="#2B2B2B",
            fg="#CCCCCC",
            font=("Helvetica", 9, "bold"),
            width=7,
            command=lambda: self._press_gpio(pin=15)
        )
        self.u2_btn.pack(side=tk.LEFT, padx=3)

        # Right Section: Directional Navigation D-Pad (Up, Down, Left, Right, Enter)
        nav_box = tk.Frame(panel, bg="#202020")
        nav_box.pack(side=tk.RIGHT, padx=10)

        # D-pad grid layout
        tk.Button(
            nav_box, text="▲", bg="#333333", fg="#FFFFFF", width=3,
            command=lambda: self._press_gpio(pin=20)
        ).grid(row=0, column=1)

        tk.Button(
            nav_box, text="◀", bg="#333333", fg="#FFFFFF", width=3,
            command=lambda: self._press_gpio(pin=21)
        ).grid(row=1, column=0)

        tk.Button(
            nav_box, text="●", bg="#444444", fg="#FFFFFF", width=3,
            font=("Helvetica", 8, "bold"),
            command=lambda: self._press_gpio(pin=22)
        ).grid(row=1, column=1)

        tk.Button(
            nav_box, text="▶", bg="#333333", fg="#FFFFFF", width=3,
            command=lambda: self._press_gpio(pin=23)
        ).grid(row=1, column=2)

        tk.Button(
            nav_box, text="▼", bg="#333333", fg="#FFFFFF", width=3,
            command=lambda: self._press_gpio(pin=24)
        ).grid(row=2, column=1)

    def _on_rec_click(self):
        self.camera.press_record()
        # Update button visual state
        is_rec = (self.camera.rcp.state.get("RECORD_STATE") == "2")
        if is_rec:
            self.rec_btn.config(bg="#FF0000", text="■ STOP")
        else:
            self.rec_btn.config(bg="#990000", text="● REC")

        if self.on_action:
            self.on_action("record_toggled")

    def _on_power_click(self):
        # Trigger PRM reset
        self.camera.inject_fault("watchdog_reset")
        if self.on_action:
            self.on_action("power_reset")

    def _press_gpio(self, pin: int):
        # Pulse GPIO pin high then low
        if 1 in self.camera.gpio:
            self.camera.gpio[1].set_input_pin(pin, True)
            self.camera.gpio[1].set_input_pin(pin, False)
        if self.on_action:
            self.on_action(f"gpio_pin_{pin}")
