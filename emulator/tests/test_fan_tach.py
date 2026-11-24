"""
Fan tachometer / speed-test depth probe: model the BRAIN fan controller so the fan init worker
completes and the Fan board-init bit 0x800 sets.

FAN BOOT PATH (reverse-engineered from RED_FW_COMPLETE.bin; see MODEL_FIDELITY_AUDIT.md
"Fan tachometer / speed test"):

    fan_init (@0x8067049C)
      -> HAL_Temp_Fan_Control_Init (@0x809B09A8)
         opens two fan devices (fan_dev_open @0x809B720C), probes presence
         (fan_dev_probe @0x809B6E88: writes CONFIG 0x20 / STATUS 0x2B + per-channel config),
      -> detect_fan_type (@0x809B08C4)
         -> HAL_Temp_SpeedTest (@0x809B061C)
            - starts the fan at full PWM (fan_start @0x809B14F4 -> fan_pwm_write @0x809B73CC),
            - tx_thread_sleep(6000) (@0x80E9E32C) -- fan spin-up wait,
            - reads the tachometer (fan_tach_read @0x809B18B0 -> fan_tach_regread @0x809B750C,
              which reads the tach MSB at *(desc+8) and LSB at the next register over the generic
              I2C read primitive fan_bus_regread @0x809B693C -> i2c_read @0x806874B0),
            - forms count = ((MSB<<8)|LSB) >> 3, converts to RPM via the ratio constants at
              g_fan_rpm_consts (@0x81474130 = {5,2,2,0x7530}), and classifies:
                  >= 0x36B0 (14000)        -> fan type 1
                  0x1B58 (7000)..0x36AF    -> fan type 3  (valid spun-up band)
                  < config threshold       -> fan type 0 = FAIL (fan too slow) -> worker strands.

Two models make this complete in the emulator:
  1. ``MaxFanController`` (devices/i2c_fan_controller.py) at I2C1 @ 0x4D answers the tach registers
     with a plausible spun-up count, so HAL_Temp_SpeedTest measures a valid RPM.
  2. The fan-tach spin-up sleep compression (camera.py ``_install_fan_spinup_hook``): the 6000-tick
     spin-up ``tx_thread_sleep`` issued from inside HAL_Temp_SpeedTest is clamped to a few ticks, so
     the fan worker proceeds to the tach read within a practical boot (6000 ThreadX ticks never
     accrue under the emulator's one-OVF-per-slice tick cadence). The firmware still runs the full
     start/sleep/read/classify sequence; only the spin-up *duration* is time-compressed.

With both, the Fan init-event bit 0x800 sets and the board-bring-up barrier (0x03300FF7) passes --
boot reaches 0xC3300FFF.
"""

import pytest
from emulator.camera import Dsmc2Camera
from emulator.devices.i2c_fan_controller import MaxFanController
from conftest import requires_firmware

pytestmark = requires_firmware

FAN_BIT = 0x800
BOARD_BARRIER = 0x03300FF7


@pytest.fixture(scope="module")
def bringup():
    cam = Dsmc2Camera(workspace_root=".")
    cam.load_firmware()
    res = cam.boot_to_ui_bringup()
    return cam, res


def test_fan_controller_attached_at_i2c1_0x4d():
    """The BRAIN fan controller model sits on I2C1 at 7-bit address 0x4D (the address the FW drives)."""
    cam = Dsmc2Camera(workspace_root=".")
    dev = cam.i2c[1].slaves.get(0x4D)
    assert isinstance(dev, MaxFanController)


def test_fan_bit_sets(bringup):
    """The Fan init-event bit 0x800 is SET after boot (the fan worker completed)."""
    _, res = bringup
    assert res["init_event_flags"] & FAN_BIT, (
        "Fan 0x800 should be set once the fan tach speed test completes; "
        f"flags=0x{res['init_event_flags']:08X}"
    )
    assert res["fan_tach_modeled"] is True


def test_board_barrier_passes_with_fan(bringup):
    """With the fan bit set, the whole board-bring-up barrier (0x03300FF7) now passes (0xC3300FFF)."""
    _, res = bringup
    flags = res["init_event_flags"]
    assert (flags & BOARD_BARRIER) == BOARD_BARRIER
    assert flags == 0xC3300FFF
    assert res["barrier_passed"] is True
    assert res["blocked_board_bits"] == 0


def test_speed_test_measures_valid_fan_type():
    """HAL_Temp_SpeedTest, run against the tach model, returns a NON-ZERO (valid) fan type.

    fan_tach_read (@0x809B18B0) and the speed-test classifier (@0x809B061C) are both reached, and
    detect_fan_type (@0x809B08C4) returns a non-zero type -- i.e. the fan is not classified as
    "too slow" (type 0, which strands the worker). We confirm this behaviourally: the Fan init
    bit setting (above) is produced only when detect_fan_type succeeds.
    """
    import unicorn

    cam = Dsmc2Camera(workspace_root=".")
    cam.load_firmware()

    hits = {"tach_read": 0, "detect": 0, "speedtest": 0}

    def _mk(name):
        def _h(uc, addr, size, ud):
            hits[name] += 1
        return _h

    for stock_addr, name in ((0x809B18B0, "tach_read"), (0x809B08C4, "detect"), (0x809B061C, "speedtest")):
        phys = cam.fw(stock_addr)
        cam.cpu.mu.hook_add(unicorn.UC_HOOK_CODE, _mk(name), None, phys, phys)

    res = cam.boot_to_ui_bringup()

    # The fan speed-test and tach read were actually executed (not skipped), and the Fan bit set.
    assert hits["speedtest"] > 0, "HAL_Temp_SpeedTest should run"
    assert hits["detect"] > 0, "detect_fan_type should run"
    assert hits["tach_read"] > 0, "fan_tach_read should run (the spin-up sleep was compressed)"
    assert res["init_event_flags"] & FAN_BIT
