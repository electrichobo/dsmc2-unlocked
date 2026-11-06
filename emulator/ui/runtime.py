"""
Emulator runtime worker for the desktop GUI.

Threading model (the fix for the GUI / system-input freeze):

* One dedicated worker thread (``EmulatorRuntime``) exclusively owns the
  ``Dsmc2Camera``: the Unicorn CPU, the peripheral models and the RedUi state.
  Every firmware step, every input injection and every frame composition runs
  on that thread, so Unicorn is never touched from two threads at once.
* The Tk thread never calls into the emulator. Input handlers only enqueue
  commands (non-blocking, microseconds) and the periodic UI tick only blits the
  most recently published frame. However slow or stuck the emulator gets, the
  Tk thread keeps pumping Windows messages, so a mouse press on the window can
  never leave the OS mouse captured by a hung thread.
* Each CPU slice also carries a Unicorn wall-clock timeout, so a halted core
  (e.g. WFI with no interrupt source) can not wedge the worker either.
"""

import queue
import threading
import time
import traceback
from typing import Any, Callable, Optional, Tuple


class EmulatorRuntime:
    """Background owner of the virtual camera for interactive front-ends."""

    def __init__(
        self,
        camera,
        compositor: Optional[Callable[[], Any]] = None,
        slice_instructions: int = 50_000,
        fps: float = 20.0,
        idle_wait_s: float = 0.002,
    ):
        self.camera = camera
        self.compositor = compositor
        self.slice_instructions = slice_instructions
        self.frame_interval = 1.0 / max(1.0, fps)
        self.idle_wait_s = idle_wait_s

        self._commands: "queue.Queue[Tuple[Callable, tuple, dict, Optional[Callable]]]" = queue.Queue()
        self._results: "queue.Queue[Tuple[Callable, Any, Optional[BaseException]]]" = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._dirty = True
        self._frame: Tuple[int, Any] = (0, None)
        self._next_frame_at = 0.0
        self.thread: Optional[threading.Thread] = None

        # Telemetry (read-only from other threads)
        self.last_slice_ms = 0.0
        self.last_compose_ms = 0.0
        self.max_slice_ms = 0.0
        self.commands_executed = 0
        self.errors = 0

    # ------------------------------------------------------------------ control
    def set_compositor(self, compositor: Callable[[], Any]):
        self.compositor = compositor
        self.invalidate()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name="EmulatorRuntime", daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop.set()
        self._wake.set()
        if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=timeout)

    @property
    def is_alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    @property
    def on_worker_thread(self) -> bool:
        return threading.current_thread() is self.thread

    # ------------------------------------------------- thread-safe entry points
    def submit(self, fn: Callable, *args, **kwargs):
        """Queue ``fn`` for execution on the worker thread. Never blocks."""
        self._commands.put((fn, args, kwargs, None))
        self._dirty = True
        self._wake.set()

    def request(self, fn: Callable, callback: Callable[[Any], None], *args, **kwargs):
        """Run ``fn`` on the worker; ``callback(result)`` later runs in ``poll_results``."""
        self._commands.put((fn, args, kwargs, callback))
        self._wake.set()

    def invalidate(self):
        """Ask the worker to publish a fresh frame as soon as possible."""
        self._dirty = True
        self._wake.set()

    def latest_frame(self) -> Tuple[int, Any]:
        """Return ``(sequence, image)`` of the newest composed frame. Never blocks."""
        return self._frame

    def poll_results(self, max_items: int = 32) -> int:
        """Deliver completed ``request`` callbacks. Call from the UI thread."""
        delivered = 0
        while delivered < max_items:
            try:
                callback, result, error = self._results.get_nowait()
            except queue.Empty:
                break
            delivered += 1
            if error is not None:
                self._report(f"request failed: {error!r}")
                continue
            try:
                callback(result)
            except Exception:
                self._report("request callback failed:\n" + traceback.format_exc())
        return delivered

    # ------------------------------------------------------------- worker loop
    def _run(self):
        while not self._stop.is_set():
            self._drain_commands()
            if self._stop.is_set():
                break

            cam = self.camera
            if cam.power_state == cam.POWER_ON and cam.is_booted:
                t0 = time.perf_counter()
                try:
                    cam.step_slice(self.slice_instructions)
                except Exception:
                    self._report("step_slice failed:\n" + traceback.format_exc())
                self.last_slice_ms = (time.perf_counter() - t0) * 1000.0
                self.max_slice_ms = max(self.max_slice_ms, self.last_slice_ms)

            now = time.perf_counter()
            if self._dirty or now >= self._next_frame_at:
                self.compose_now()
                self._next_frame_at = now + self.frame_interval

            # Pace the loop: yields the GIL to the Tk thread and wakes early on input.
            self._wake.wait(self.idle_wait_s)
            self._wake.clear()

    def compose_now(self):
        """Compose and publish a frame. Only call on the worker thread."""
        self._dirty = False
        if self.compositor is None:
            return
        t0 = time.perf_counter()
        try:
            img = self.compositor()
        except Exception:
            self._report("frame composition failed:\n" + traceback.format_exc())
            return
        self.last_compose_ms = (time.perf_counter() - t0) * 1000.0
        self._frame = (self._frame[0] + 1, img)

    def _drain_commands(self, budget: int = 64):
        for _ in range(budget):
            try:
                fn, args, kwargs, callback = self._commands.get_nowait()
            except queue.Empty:
                return
            result, error = None, None
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                error = exc
                self._report(f"command {getattr(fn, '__name__', fn)!s} failed:\n" + traceback.format_exc())
            self.commands_executed += 1
            if callback is not None:
                self._results.put((callback, result, error))

    def _report(self, message: str):
        self.errors += 1
        try:
            self.camera.log_system(f"[RUNTIME] {message.splitlines()[0]}")
        except Exception:
            pass
        print(f"[EmulatorRuntime] {message}", flush=True)
