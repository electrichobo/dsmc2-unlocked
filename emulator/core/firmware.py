"""
RED DSMC2 Firmware Inspection, Staging & Custom Execution Engine.
Validates, parses entry vectors, computes cryptographic hashes, and executes custom
firmware binaries (.bin, .mem, .pkg) loaded from the RED MINI-MAG bay into DDR memory.
"""

import hashlib
import os
import struct
from typing import Optional, Dict, Any, Union


class FirmwareInspectionResult:
    """Diagnostic analysis and metadata for a staged firmware binary."""

    def __init__(
        self,
        filename: str,
        size_bytes: int,
        sha256: str,
        md5: str,
        sp: int,
        reset_pc: int,
        format_name: str,
        is_valid: bool,
        is_runnable: bool,
        error_message: Optional[str] = None,
        irq_vector: Optional[int] = None,
        vector_base: Optional[int] = None,
    ):
        self.filename = filename
        self.size_bytes = size_bytes
        self.size_mb = round(size_bytes / (1024 * 1024), 2)
        self.sha256 = sha256
        self.md5 = md5
        self.sp = sp
        self.reset_pc = reset_pc
        self.format_name = format_name
        self.is_valid = is_valid
        self.is_runnable = is_runnable
        self.error_message = error_message
        self.irq_vector = irq_vector
        self.vector_base = vector_base

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "size_mb": self.size_mb,
            "sha256": self.sha256,
            "md5": self.md5,
            "sp": f"0x{self.sp:08X}",
            "reset_pc": f"0x{self.reset_pc:08X}",
            "format_name": self.format_name,
            "is_valid": self.is_valid,
            "is_runnable": self.is_runnable,
            "error_message": self.error_message,
            "irq_vector": None if self.irq_vector is None else f"0x{self.irq_vector:08X}",
            "vector_base": None if self.vector_base is None else f"0x{self.vector_base:08X}",
        }


class FirmwareManager:
    """Manages firmware validation, staging, and custom execution in the virtual camera."""

    # Standard RED DSMC2 DDR Memory Limits
    DDR_BASE = 0x80000000
    DDR_SIZE = 512 * 1024 * 1024 # 512 MB mapped window (physical DDR: 256 MB)
    DDR_LIMIT = DDR_BASE + DDR_SIZE

    # Stock 7.4.1 entry (used only when the image header does not carry its own)
    DEFAULT_SP = 0x881D2690
    DEFAULT_RESET_PC = 0x80829F2C
    DEFAULT_IRQ_VECTOR = 0x80829F00

    LDR_PC_PC_M4 = 0xE51FF004    # ldr pc, [pc, #-4]: relinked-image base stub, next word = vectors
    LDR_PC_VEC = 0xE59FF024      # ldr pc, [pc, #0x24]: vector slot k loads word k + 11

    @classmethod
    def parse_vectors(cls, data: bytes, base: int = DDR_BASE) -> Optional[Dict[str, int]]:
        """Read SP / reset PC / IRQ handler from a DSMC2 vector header.

        Stock header (at the base): ``b +0x20`` then 7x ``ldr pc,[pc,#0x24]``, ``ldr sp,[pc]``,
        ``ldr pc,[pc]``, word 10 = SP, word 11 = reset PC, words 12..18 = handler table (IRQ =
        word 17). A relinked image whose vectors moved starts with the stub
        ``ldr pc,[pc,#-4]; .word <vectors>`` -- followed once.
        """
        off = 0
        if len(data) >= 8:
            w0, w1 = struct.unpack_from("<II", data, 0)
            if w0 == cls.LDR_PC_PC_M4 and base <= w1 < base + len(data) - 64 and w1 % 32 == 0:
                off = w1 - base
        if len(data) < off + 76:
            return None
        w = struct.unpack_from("<19I", data, off)
        sp, pc = w[10], w[11]
        if not (cls.DDR_BASE <= sp < cls.DDR_LIMIT and cls.DDR_BASE <= pc < cls.DDR_LIMIT):
            return None
        out = {"sp": sp, "reset_pc": pc, "vector_base": base + off}
        if w[6] == cls.LDR_PC_VEC and cls.DDR_BASE <= w[17] < cls.DDR_LIMIT:
            out["irq_vector"] = w[17]
        return out

    @classmethod
    def inspect(cls, source: Union[str, bytes], filename: str = "firmware.bin") -> FirmwareInspectionResult:
        """Inspect and validate a firmware file path or raw byte buffer."""
        if isinstance(source, str):
            filename = os.path.basename(source)
            if not os.path.exists(source):
                return FirmwareInspectionResult(
                    filename=filename, size_bytes=0, sha256="", md5="",
                    sp=0, reset_pc=0, format_name="File Not Found",
                    is_valid=False, is_runnable=False, error_message=f"File not found: {source}"
                )
            with open(source, "rb") as f:
                data = f.read()
        else:
            data = source

        size = len(data)
        if size == 0:
            return FirmwareInspectionResult(
                filename=filename, size_bytes=0, sha256="", md5="",
                sp=0, reset_pc=0, format_name="Empty File",
                is_valid=False, is_runnable=False, error_message="Firmware file is empty (0 bytes)"
            )

        sha = hashlib.sha256(data).hexdigest()
        md5 = hashlib.md5(data).hexdigest()

        # Check for minimum reasonable size
        if size < 64:
            return FirmwareInspectionResult(
                filename=filename, size_bytes=size, sha256=sha, md5=md5,
                sp=0, reset_pc=0, format_name="Truncated Header",
                is_valid=False, is_runnable=False, error_message="File too small for ARM vector table (< 64 bytes)"
            )

        word0 = struct.unpack_from("<I", data, 0)[0]

        vec = cls.parse_vectors(data)
        if vec is not None:
            relinked = vec["vector_base"] != cls.DDR_BASE
            format_name = "RED DSMC2 DDR Memory Image (.mem)" + (" [relinked, moved vectors]" if relinked else "")
            return FirmwareInspectionResult(
                filename=filename, size_bytes=size, sha256=sha, md5=md5,
                sp=vec["sp"], reset_pc=vec["reset_pc"], format_name=format_name,
                is_valid=True, is_runnable=True,
                irq_vector=vec.get("irq_vector"), vector_base=vec["vector_base"],
            )

        # Check if first word is an ARM branch (0xEAxxxxxx) or vector branch
        is_arm_branch = (word0 & 0xFF000000) in (0xEA000000, 0xEB000000)
        if is_arm_branch:
            # Raw ARM binary starting at base
            target_pc = cls.DDR_BASE
            format_name = "Raw ARM Executable Binary (.bin)"
            return FirmwareInspectionResult(
                filename=filename, size_bytes=size, sha256=sha, md5=md5,
                sp=cls.DEFAULT_SP, reset_pc=target_pc, format_name=format_name,
                is_valid=True, is_runnable=True
            )

        # Check for RED Package / Container Header
        magic = data[:8]
        if b"RED" in magic or b"COMBO" in magic or b"PKG" in magic:
            format_name = "RED Combo Firmware Package (.pkg)"
            return FirmwareInspectionResult(
                filename=filename, size_bytes=size, sha256=sha, md5=md5,
                sp=cls.DEFAULT_SP, reset_pc=cls.DEFAULT_RESET_PC, format_name=format_name,
                is_valid=True, is_runnable=True
            )

        # Fallback raw executable assumption if size > 64KB
        if size >= 65536:
            format_name = "Unsigned Custom ARM Image"
            return FirmwareInspectionResult(
                filename=filename, size_bytes=size, sha256=sha, md5=md5,
                sp=cls.DEFAULT_SP, reset_pc=cls.DEFAULT_RESET_PC, format_name=format_name,
                is_valid=True, is_runnable=True
            )

        return FirmwareInspectionResult(
            filename=filename, size_bytes=size, sha256=sha, md5=md5,
            sp=0, reset_pc=0, format_name="Unrecognized Binary Format",
            is_valid=False, is_runnable=False,
            error_message="Missing valid ARM vector table or entry point in DDR range"
        )

    @classmethod
    def boot_custom(cls, camera, firmware_path: str) -> Dict[str, Any]:
        """Load and boot custom firmware binary into the active camera emulator."""
        meta = cls.inspect(firmware_path)
        if not meta.is_runnable:
            camera.log_system(f"[UPGRADE] Rejected custom firmware: {meta.error_message}")
            return {
                "status": "error",
                "message": meta.error_message or "Invalid firmware",
                "metadata": meta.to_dict()
            }

        camera.log_system(f"[UPGRADE] Flashing staged custom firmware from RED MINI-MAG slot: {meta.filename}")
        camera.log_system(f"[UPGRADE] Size: {meta.size_mb:.2f} MB | SHA-256: {meta.sha256[:16]}... | Format: {meta.format_name}")
        camera.log_system(f"[UPGRADE] Vector initialization: SP=0x{meta.sp:08X}, Reset PC=0x{meta.reset_pc:08X}")

        # Cleanly reinitialize SoC hardware & peripheral state
        camera._init_hardware()

        # Load binary directly into DDR at 0x80000000
        with open(firmware_path, "rb") as f:
            fw_bytes = f.read()
        camera.bus.load_binary(cls.DDR_BASE, fw_bytes)

        # Configure reset vector and supervisor mode
        camera.apply_entry(fw_bytes)
        camera.cpu.pc = camera.reset_pc = meta.reset_pc
        camera.cpu.sp = camera.reset_sp = meta.sp

        # Execute initial instruction slice
        try:
            initial_count = min(45_000_000, 45_000_000)
            camera.cpu.run(camera.cpu.pc, 0, count=initial_count)
            camera.instruction_count += initial_count
            camera.is_booted = True
            camera.power_state = camera.POWER_ON
            camera.rcp_server.start()

            threads = camera.get_threads()
            camera.log_system("[UPGRADE] Custom firmware boot SUCCESSFUL! ThreadX RTOS operational.")
            for t in threads:
                camera.log_system(f"[RTOS] Custom Thread [{t['name']}] state={t['state']} prio={t['priority']}")

            return {
                "status": "ok",
                "message": "Custom firmware running",
                "metadata": meta.to_dict(),
                "threads": threads,
                "pc": f"0x{camera.cpu.pc:08X}"
            }
        except Exception as e:
            # Execution crash or illegal instruction in custom binary
            reg_dump = {
                "pc": f"0x{camera.cpu.pc:08X}",
                "sp": f"0x{camera.cpu.sp:08X}",
                "lr": f"0x{camera.cpu.lr:08X}",
                "cpsr": f"0x{camera.cpu.cpsr:08X}"
            }
            for i in range(13):
                reg_dump[f"r{i}"] = f"0x{camera.cpu.get_r(i):08X}"

            camera.log_system(f"[FATAL] Custom firmware execution halted at PC 0x{camera.cpu.pc:08X}: {e}")
            camera.power_state = camera.POWER_STANDBY
            camera.is_booted = False
            return {
                "status": "crash",
                "error": str(e),
                "pc": f"0x{camera.cpu.pc:08X}",
                "registers": reg_dump,
                "metadata": meta.to_dict()
            }
