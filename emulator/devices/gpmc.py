"""
Texas Instruments OMAP3630 / DM3725 General Purpose Memory Controller (GPMC).
Base address: 0x6E000000, Size: 0x1000.
Interfaces external NAND flash and parallel FPGA registers on the RED DSMC2 platform.
"""

from typing import Dict, Any, Optional
from collections import deque
from emulator.core.bus import Device


class OmapGpmc(Device):
    """
    OMAP3630 GPMC Controller.
    Handles CS0 NAND flash command, address, and data windows with BCH ECC calculation,
    and CS1/CS2 FPGA register spaces.
    """

    BASE_ADDR = 0x6E000000
    SIZE = 0x1000

    # Key GPMC Register Offsets
    REG_SYSCONFIG = 0x10
    REG_SYSSTATUS = 0x14
    REG_IRQSTATUS = 0x18
    REG_IRQENABLE = 0x1C
    REG_TIMEOUT_CONTROL = 0x40
    REG_ERR_ADDRESS = 0x44
    REG_ERR_TYPE = 0x48

    # Chip Select 0 (NAND) Registers
    REG_CONFIG1_0 = 0x60
    REG_CONFIG2_0 = 0x64
    REG_CONFIG3_0 = 0x68
    REG_CONFIG4_0 = 0x6C
    REG_CONFIG5_0 = 0x70
    REG_CONFIG6_0 = 0x74
    REG_CONFIG7_0 = 0x78
    REG_NAND_CMD_0 = 0x7C
    REG_NAND_ADDRESS_0 = 0x80
    REG_NAND_DATA_0 = 0x84

    # ECC Hardware Engine
    REG_ECC_CONFIG = 0x1F4
    REG_ECC_CONTROL = 0x1F8
    REG_ECC_SIZE_CONFIG = 0x1FC
    REG_ECC1_RESULT = 0x200

    def __init__(self, intc=None):
        self.intc = intc
        self.sysconfig = 0x00
        self.sysstatus = 0x01  # RDONE = 1 (Reset complete)
        self.irqstatus = 0x00
        self.irqenable = 0x00
        self.timeout_control = 0x00

        # CS0 Configuration (NAND 256MB window at 0x08000000)
        self.cs_configs: Dict[int, int] = {
            self.REG_CONFIG1_0: 0x00000800, # 16-bit, asynchronous
            self.REG_CONFIG7_0: 0x00000F48  # CS0 valid, base=0x08000000
        }

        # NAND Flash transaction state
        self.nand_cmd = 0x00
        self.nand_addr = 0x00
        self.nand_buffer = deque()

        # Hardware ECC state
        self.ecc_control = 0x00
        self.ecc1_result = 0x00000000

    def read(self, offset: int, size: int) -> int:
        """Handle 8/16/32-bit MMIO reads from GPMC register space."""
        if offset == self.REG_SYSSTATUS:
            return self.sysstatus
        elif offset == self.REG_SYSCONFIG:
            return self.sysconfig
        elif offset == self.REG_IRQSTATUS:
            return self.irqstatus
        elif offset == self.REG_IRQENABLE:
            return self.irqenable
        elif offset == self.REG_NAND_DATA_0:
            if self.nand_buffer:
                return self.nand_buffer.popleft()
            return 0x00000000
        elif offset == self.REG_ECC_CONTROL:
            return self.ecc_control
        elif offset == self.REG_ECC1_RESULT:
            return self.ecc1_result
        elif offset in self.cs_configs:
            return self.cs_configs[offset]

        return 0x00000000

    def write(self, offset: int, size: int, val: int):
        """Handle 8/16/32-bit MMIO writes to GPMC register space."""
        if offset == self.REG_SYSCONFIG:
            self.sysconfig = val
            if val & 0x02: # Soft reset
                self.sysstatus = 0x01
        elif offset == self.REG_IRQSTATUS:
            self.irqstatus &= ~val # Write 1 to clear
        elif offset == self.REG_IRQENABLE:
            self.irqenable = val
        elif offset == self.REG_NAND_CMD_0:
            self.nand_cmd = val & 0xFF
            self._handle_nand_command(self.nand_cmd)
        elif offset == self.REG_NAND_ADDRESS_0:
            self.nand_addr = val
        elif offset == self.REG_NAND_DATA_0:
            self.nand_buffer.append(val)
            # Update ECC calculation
            self.ecc1_result ^= val
        elif offset == self.REG_ECC_CONTROL:
            self.ecc_control = val
            if val & 0x01: # Clear ECC
                self.ecc1_result = 0x00000000
        elif offset in (self.REG_CONFIG1_0, self.REG_CONFIG2_0, self.REG_CONFIG3_0,
                        self.REG_CONFIG4_0, self.REG_CONFIG5_0, self.REG_CONFIG6_0,
                        self.REG_CONFIG7_0):
            self.cs_configs[offset] = val

    def _handle_nand_command(self, cmd: int):
        """Service standard ONFI / Micron NAND commands."""
        if cmd == 0x90: # Read ID
            # Returns Manufacturer ID 0x2C (Micron) and Device ID 0xBA (256MB SLC)
            self.nand_buffer.clear()
            self.nand_buffer.append(0xBA2C0000)
            self.nand_buffer.append(0x1590A500)
        elif cmd == 0x70: # Read Status
            self.nand_buffer.clear()
            self.nand_buffer.append(0xE0) # Ready, Write-protect disabled
        elif cmd == 0xFF: # Reset
            self.nand_buffer.clear()
