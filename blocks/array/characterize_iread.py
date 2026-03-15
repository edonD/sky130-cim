#!/usr/bin/env python3
"""Characterize read current vs BL voltage using transient simulations."""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from evaluate import (load_bitcell_params, make_bitcell_subckt,
                      run_ngspice, VDD, BLOCK_DIR, SKY130_LIB)
import re

bitcell_params = load_bitcell_params()


def measure_iread_vs_vbl_transient():
    """
    Measure read current at different BL voltages using transient simulation.
    Fix BL at each voltage, measure current after cell settles.
    """
    vbl_values = np.arange(0.0, 1.85, 0.1)
    currents = []

    for vbl in vbl_values:
        lines = [
            f"* I_READ vs V_BL characterization (transient)",
            f'.lib "{SKY130_LIB}" tt',
            f".param supply={VDD}",
            "",
            make_bitcell_subckt(bitcell_params),
            "",
            "Vdd vdd 0 {supply}",
            "Vss vss 0 0",
            f"Vbl bl 0 DC {vbl}",   # Fixed BL voltage
            "Vwl wl 0 PWL(0 0 2n 0 2.1n 1.8)",  # WL rises at 2ns
            "Vwwl wwl 0 0",  # WWL always off
            "",
            "Xcell bl blb wl wwl q qb vdd vss cim_bitcell",
            "",
            "Vblb blb 0 0",  # BLB unused
            "",
            ".ic v(q)=1.8 v(qb)=0",
            "",
            ".tran 0.05n 20n UIC",
            "",
            "* Measure current at 15ns (well after WL rises, cell settled)",
            ".meas tran iread FIND i(Vbl) AT=15n",
            "",
            ".control",
            "run",
            ".endc",
            ".end",
        ]
        netlist = "\n".join(lines)
        output, rc = run_ngspice(netlist)

        m = re.search(r"iread\s*=\s*([0-9eE.+-]+)", output, re.IGNORECASE)
        if m:
            i_val = abs(float(m.group(1)))
            currents.append(i_val)
            print(f"  V_BL = {vbl:.2f}V -> I_READ = {i_val*1e6:.2f} µA")
        else:
            currents.append(0)
            print(f"  V_BL = {vbl:.2f}V -> FAILED to parse")

    vbl_arr = np.array(vbl_values[:len(currents)])
    i_arr = np.array(currents)

    # Plot
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(vbl_arr, i_arr * 1e6, 'bo-', markersize=6, linewidth=2)
    ax.set_xlabel('BL Voltage (V)')
    ax.set_ylabel('Read Current (µA)')
    ax.set_title('I_READ vs V_BL (Weight=1, WL=VDD) — Transient Measurement')
    ax.grid(True, alpha=0.3)
    ax.axhline(bitcell_params["i_read_ua"], color='r', linestyle='--',
               label=f'Upstream I_READ = {bitcell_params["i_read_ua"]:.2f} µA')
    ax.legend()
    fig.tight_layout()
    fig.savefig(str(BLOCK_DIR / "plots" / "iread_vs_vbl.png"), dpi=150)
    plt.close(fig)
    print(f"\nPlot saved: plots/iread_vs_vbl.png")

    return vbl_arr, i_arr


if __name__ == "__main__":
    vbl, iread = measure_iread_vs_vbl_transient()
    np.savez(str(BLOCK_DIR / "iread_char.npz"), vbl=vbl, iread=iread)
    print(f"\nCharacterization saved to iread_char.npz")
    print(f"\nI_READ at V_BL=1.8V: {iread[-1]*1e6:.2f} µA")
    print(f"I_READ at V_BL=0.0V: {iread[0]*1e6:.2f} µA")
