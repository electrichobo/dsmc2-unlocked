"""
RED DSMC2 BRAIN fan controller I2C model (MAX6639-class dual-channel PWM fan controller).

Attached to OMAP3630 I2C1 (bus index 1), 7-bit slave address 0x4D. This is the controller the
firmware's thermal/fan HAL (``temp_hal_weapon.c``) drives through the generic I2C register-read /
register-write primitives ``fan_bus_regread`` (@0x809B693C -> i2c_read @0x806874B0) and
``fan_bus_regwrite`` (@0x809B69C0 -> i2c_write @0x80687DBC).

FW-derived register map (reverse-engineered from RED_FW_COMPLETE.bin; see
MODEL_FIDELITY_AUDIT.md "Fan tachometer / speed test"):

  * ``HAL_Temp_Fan_Control_Init`` @0x809B09A8 opens two fan devices (``fan_dev_open`` @0x809B720C),
    probes presence (``fan_dev_probe`` @0x809B6E88 writes CONFIG 0x20 / STATUS 0x2B and the
    per-channel PWM target / config bytes), then runs ``detect_fan_type`` (@0x809B08C4) which calls
    ``HAL_Temp_SpeedTest`` (@0x809B061C).
  * ``HAL_Temp_SpeedTest`` starts the fan at full PWM (``fan_start`` @0x809B14F4 ->
    ``fan_pwm_write`` @0x809B73CC, which writes the PWM target register), sleeps ~6000 ThreadX
    ticks for spin-up (``tx_thread_sleep`` @0x80E9E32C), then reads the tachometer
    (``fan_tach_read`` @0x809B18B0 -> ``fan_tach_regread`` @0x809B750C).
  * ``fan_tach_regread`` (@0x809B750C) reads **two tach bytes**: the high byte at register
    ``*(desc+8)`` (the channel's tach MSB register) and the low byte at the next register, then
    forms a 12-bit count ``count = ((MSB<<8) | LSB) >> 3`` and converts it to RPM with the ratio
    constants at g_fan_rpm_consts (@0x81474130 = {5, 2, 2, 0x7530=30000}).
  * The multi-channel reader ``fan_tach_read_cb`` (@0x809B7638) reads tach registers 0x24, 0x25,
    0x26, 0x27 (channel 1 / channel 2 tach bytes), so the tach-count registers live at 0x24+.

``HAL_Temp_SpeedTest`` classifies the resulting RPM (constants from the decompile):
    >= 0x36B0 (14000)              -> fan type 1
    0x1B58 (7000) .. 0x36AF        -> fan type 3   (the in-band "standard BRAIN fan" result)
    <  threshold (*(cfg+8))        -> fan type 0 = FAIL (fan too slow) -> fan worker strands

So the tach registers must report a count that maps to a plausible spun-up RPM in the 7000..13999
band. The tach MSB/LSB values below are tuned so the firmware's own ``count>>3`` + ratio-constant
formula yields ~9000-10000 RPM (verified by booting the firmware and asserting ``detect_fan_type``
returns a non-zero fan type and the Fan init bit 0x800 sets). Nothing about the RPM is asserted by
the Python; the firmware computes it from these raw bytes.
"""

from typing import Dict
from emulator.devices.i2c import I2cSlave


class MaxFanController(I2cSlave):
    """MAX6639-class dual-channel fan controller (BRAIN fan), I2C1 @ 0x4D.

    Returns a plausible spun-up tachometer count so the firmware's
    ``HAL_Temp_SpeedTest`` measures a valid fan RPM and ``detect_fan_type`` succeeds.
    """

    # Control / status registers probed by fan_dev_probe (@0x809B6E88).
    REG_GLOBAL_CONFIG = 0x20      # written during probe (device config)
    REG_GLOBAL_STATUS = 0x2B      # device status / fault register

    # Tachometer count registers. fan_tach_read (@0x809B750C) reads the channel's MSB at
    # *(desc+8) and the LSB at the next register; fan_tach_read_cb (@0x809B7638) reads 0x24..0x27.
    # Channel 1 tach = 0x24 (MSB) / 0x25 (LSB); channel 2 tach = 0x26 / 0x27.
    REG_TACH1_MSB = 0x24
    REG_TACH1_LSB = 0x25
    REG_TACH2_MSB = 0x26
    REG_TACH2_LSB = 0x27

    # Raw 16-bit tach word programmed into (MSB<<8 | LSB). The firmware computes
    # count = word >> 3, then RPM from the ratio constants at 0x81474130. This word places the
    # measured RPM inside the 7000..13999 "valid BRAIN fan" band of HAL_Temp_SpeedTest.
    # (Tuned empirically against the firmware's own formula -- see module docstring / audit.)
    DEFAULT_TACH_WORD = 0x2800    # 10240 -> count 1280; maps into the valid spun-up band

    def __init__(self, address: int = 0x4D, name: str = "BRAIN_FAN"):
        super().__init__(address)
        self.name = name
        self.registers: Dict[int, int] = {}
        self._tach_word = self.DEFAULT_TACH_WORD
        self._init_registers()

    def _init_registers(self):
        # Presence / status: a probed-present, fault-free controller.
        self.registers[self.REG_GLOBAL_CONFIG] = 0x00
        self.registers[self.REG_GLOBAL_STATUS] = 0x00
        self._program_tach(self._tach_word)

    def _program_tach(self, word: int):
        word &= 0xFFFF
        self._tach_word = word
        msb = (word >> 8) & 0xFF
        lsb = word & 0xFF
        # Both fan channels report the same spun-up tach word.
        self.registers[self.REG_TACH1_MSB] = msb
        self.registers[self.REG_TACH1_LSB] = lsb
        self.registers[self.REG_TACH2_MSB] = msb
        self.registers[self.REG_TACH2_LSB] = lsb

    def set_tach_word(self, word: int):
        """Override the raw tach word (test / tuning helper)."""
        self._program_tach(word)

    def read(self, reg_addr: int, length: int) -> bytes:
        return bytes(self.registers.get((reg_addr + i) & 0xFF, 0x00) for i in range(length))

    def write(self, reg_addr: int, data: bytes) -> bool:
        for i, b in enumerate(data):
            self.registers[(reg_addr + i) & 0xFF] = b & 0xFF
        return True
