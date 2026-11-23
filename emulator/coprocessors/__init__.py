"""
RED DSMC2 Dedicated Coprocessors, ASICs & DSP Subsystem.
Includes Tensilica Xtensa (IOB0, IOB1, ISP1, ISP2), TI TMS320C64x+ DSP, and C2C Transport.
"""

from emulator.coprocessors.c2c import (
    CoprocessorTransport,
    CoprocessorSpiTarget,
    IobMailboxDevice,
    IobDoorbellDevice,
)
from emulator.coprocessors.xtensa import XtensaCore, Iob0Coprocessor, Iob1Coprocessor, IspCoprocessor
from emulator.coprocessors.dsp import TiC64xDsp, DspLinkIpc, CImagingServer, RAudioServer

__all__ = [
    "CoprocessorTransport",
    "CoprocessorSpiTarget",
    "IobMailboxDevice",
    "IobDoorbellDevice",
    "XtensaCore",
    "Iob0Coprocessor",
    "Iob1Coprocessor",
    "IspCoprocessor",
    "TiC64xDsp",
    "DspLinkIpc",
    "CImagingServer",
    "RAudioServer",
]
