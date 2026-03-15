#!/usr/bin/env python3
"""Characterize I_READ vs V_BL at all PVT corners."""

import numpy as np
import re
from evaluate import (load_bitcell_params, make_bitcell_subckt,
                      run_ngspice, VDD, BLOCK_DIR, SKY130_LIB)

bitcell_params = load_bitcell_params()


def measure_iread_at_corner(corner):
    """Measure I_READ vs V_BL at a specific process corner."""
    vbl_values = np.arange(0.0, 1.85, 0.1)
    currents = []

    for vbl in vbl_values:
        lines = [
            f"* I_READ characterization at {corner} corner",
            f'.lib "{SKY130_LIB}" {corner}',
            f".param supply={VDD}",
            "",
            make_bitcell_subckt(bitcell_params),
            "",
            "Vdd vdd 0 {supply}",
            "Vss vss 0 0",
            f"Vbl bl 0 DC {vbl}",
            "Vwl wl 0 PWL(0 0 2n 0 2.1n 1.8)",
            "Vwwl wwl 0 0",
            "",
            "Xcell bl blb wl wwl q qb vdd vss cim_bitcell",
            "Vblb blb 0 0",
            "",
            ".ic v(q)=1.8 v(qb)=0",
            ".tran 0.05n 20n UIC",
            ".meas tran iread FIND i(Vbl) AT=15n",
            ".control", "run", ".endc", ".end",
        ]
        output, rc = run_ngspice("\n".join(lines))
        m = re.search(r"iread\s*=\s*([0-9eE.+-]+)", output, re.I)
        if m:
            currents.append(abs(float(m.group(1))))
        else:
            currents.append(0)

    return vbl_values[:len(currents)], np.array(currents)


if __name__ == "__main__":
    corners = ["tt", "ss", "ff", "sf", "fs"]
    all_data = {}

    for corner in corners:
        print(f"\nCharacterizing corner: {corner.upper()}")
        vbl, iread = measure_iread_at_corner(corner)
        all_data[corner] = {"vbl": vbl, "iread": iread}
        print(f"  I_READ at VDD: {iread[-1]*1e6:.2f} µA")
        print(f"  I_READ at 0.5V: {iread[5]*1e6:.2f} µA")

    # Save all corners
    save_data = {}
    for corner in corners:
        save_data[f"vbl_{corner}"] = all_data[corner]["vbl"]
        save_data[f"iread_{corner}"] = all_data[corner]["iread"]

    np.savez(str(BLOCK_DIR / "iread_char_corners.npz"), **save_data)
    print(f"\nAll corners saved to iread_char_corners.npz")
