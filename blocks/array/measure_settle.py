#!/usr/bin/env python3
"""Measure actual BL settling time from SPICE waveforms."""

import numpy as np
from evaluate import (generate_netlist, run_ngspice, parse_measurements,
                      load_bitcell_params, load_pwm_params, make_bitcell_subckt,
                      VDD, BLOCK_DIR, SKY130_LIB)

bitcell_params = load_bitcell_params()
pwm_params = load_pwm_params()

params = {
    "Wpre": 4.0,
    "Lpre": 0.15,
    "Tpre_ns": 5.0,
    "Cbl_extra_ff": 10000.0,
}

# Use a moderate test case
n_rows, n_cols = 8, 1
W = np.array([[1], [1], [1], [1], [0], [0], [0], [0]])  # 4 active
x = np.array([8, 8, 8, 8, 0, 0, 0, 0])  # moderate input

netlist, t_meas, t_start = generate_netlist(
    n_rows, n_cols, W, x, params, bitcell_params, pwm_params
)

# Modify to write dense waveform data
netlist = netlist.replace(
    "wrdata array_output.txt v(bl0)",
    "wrdata settle_wf.txt v(bl0) v(wl0)\nwrdata array_output.txt v(bl0)"
)

output, rc = run_ngspice(netlist)

# Read waveform
wf_file = BLOCK_DIR / "settle_wf.txt"
if wf_file.exists():
    data = np.loadtxt(str(wf_file))
    t = data[:, 0] * 1e9  # ns
    vbl = data[:, 1]
    vwl = data[:, 2]

    # Find when WL goes low (end of max pulse)
    t_lsb = pwm_params["t_lsb_ns"]
    t_pulse_end = t_start + 8 * t_lsb  # input=8 -> pulse = 8 * T_LSB

    # Find final BL value
    final_v = vbl[-1]

    # Find when BL is within 1% of final value after pulse ends
    idx_after_pulse = t > t_pulse_end
    if np.any(idx_after_pulse):
        t_after = t[idx_after_pulse]
        v_after = vbl[idx_after_pulse]

        threshold = 0.01 * abs(VDD - final_v)  # 1% of total swing
        settled_mask = np.abs(v_after - final_v) < threshold

        if np.any(settled_mask):
            settle_idx = np.where(settled_mask)[0][0]
            t_settle = t_after[settle_idx] - t_pulse_end
            print(f"Pulse end time: {t_pulse_end:.2f} ns")
            print(f"Final BL value: {final_v:.4f} V")
            print(f"1% settle time: {t_settle:.2f} ns after pulse end")
            print(f"Total compute time: {t_pulse_end - params['Tpre_ns'] + t_settle:.2f} ns (from precharge end)")
        else:
            print("BL did not settle within 1% of final value")

    wf_file.unlink()
else:
    print("No waveform file generated")
