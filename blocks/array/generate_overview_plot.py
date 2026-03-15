#!/usr/bin/env python3
"""Generate annotated overview plot of one complete compute cycle."""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from evaluate import (
    generate_netlist, run_ngspice, load_bitcell_params, load_pwm_params,
    VDD, BLOCK_DIR, SKY130_LIB
)

PLOTS_DIR = BLOCK_DIR / "plots"
bitcell_params = load_bitcell_params()
pwm_params = load_pwm_params()
params = {'Wpre': 10.0, 'Lpre': 0.15, 'Tpre_ns': 20.0, 'Cbl_extra_ff': 10000.0}

# Use the verification.md test case
W = np.array([[1], [0], [1], [1], [0], [0], [1], [0]])
x = np.array([3, 7, 1, 15, 0, 4, 8, 2])
expected_dot = 27  # 3*1 + 7*0 + 1*1 + 15*1 + 0*0 + 4*0 + 8*1 + 2*0 = 27

# Generate full 8x4 for more visual interest
W_full = np.array([
    [1, 0, 1, 0],
    [0, 1, 0, 1],
    [1, 1, 0, 0],
    [1, 0, 1, 1],
    [0, 0, 1, 0],
    [0, 1, 0, 1],
    [1, 1, 1, 0],
    [0, 0, 0, 1]
])
x_full = np.array([3, 7, 1, 15, 0, 4, 8, 2])

netlist, t_meas, t_start = generate_netlist(
    8, 4, W_full, x_full, params, bitcell_params, pwm_params
)

# Add wrdata for waveforms
save_sigs = "v(bl0) v(bl1) v(bl2) v(bl3) v(wl0) v(wl1) v(wl2) v(wl3) v(wl4) v(wl5) v(wl6) v(wl7) v(pre)"
netlist = netlist.replace(
    "wrdata array_output.txt",
    f"wrdata overview_wf.txt {save_sigs}\nwrdata array_output.txt"
)
output, _ = run_ngspice(netlist)

wf_file = BLOCK_DIR / "overview_wf.txt"
if wf_file.exists():
    data = np.loadtxt(str(wf_file))
    t = data[:, 0] * 1e9  # ns

    fig, axes = plt.subplots(4, 1, figsize=(14, 14), sharex=True,
                              gridspec_kw={'height_ratios': [1, 2, 3, 1]})

    # Panel 1: Precharge signal
    ax = axes[0]
    ax.plot(t, data[:, 13], 'k-', linewidth=2)
    ax.set_ylabel('PRE (V)')
    ax.set_title('CIM Array — One Complete Compute Cycle', fontsize=14, fontweight='bold')
    ax.set_ylim(-0.2, 2.0)
    ax.fill_between(t, -0.2, 2.0, where=data[:, 13] < 0.9, alpha=0.1, color='green', label='Precharge active')
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # Panel 2: Wordline pulses
    ax = axes[1]
    colors = plt.cm.Set1(np.linspace(0, 1, 8))
    for i in range(8):
        offset = i * 0.05
        ax.plot(t, data[:, 5+i] + offset, color=colors[i], linewidth=1.5,
                label=f'WL{i} (in={x_full[i]})')
    ax.set_ylabel('WL Voltage (V)')
    ax.set_title('PWM-Encoded Input Pulses')
    ax.legend(fontsize=7, ncol=4, loc='upper right')
    ax.grid(True, alpha=0.3)

    # Panel 3: Bitline voltages
    ax = axes[2]
    bl_colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
    dot_products = W_full.T @ x_full  # expected dot products
    for c in range(4):
        ax.plot(t, data[:, 1+c], color=bl_colors[c], linewidth=2,
                label=f'BL{c} (dot={dot_products[c]})')
    ax.axhline(VDD, color='gray', linestyle=':', alpha=0.5)
    ax.set_ylabel('BL Voltage (V)')
    ax.set_title('Bitline Discharge — Analog Dot Product Accumulation')
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # Annotate phases
    Tpre = 20
    t_comp_start = Tpre + 1
    t_max_pulse = 15 * pwm_params["t_lsb_ns"]
    t_comp_end = t_comp_start + t_max_pulse

    for ax_i in axes:
        ax_i.axvline(Tpre, color='gray', linestyle='--', alpha=0.3)
        ax_i.axvline(t_comp_start, color='gray', linestyle='--', alpha=0.3)
        ax_i.axvline(t_comp_end, color='gray', linestyle='--', alpha=0.3)

    # Panel 4: Phase labels
    ax = axes[3]
    ax.set_xlim(t[0], t[-1])
    ax.set_ylim(0, 1)
    ax.fill_between([0, Tpre], 0, 1, alpha=0.3, color='green')
    ax.fill_between([t_comp_start, t_comp_end], 0, 1, alpha=0.3, color='blue')
    ax.fill_between([t_comp_end, t[-1]], 0, 1, alpha=0.3, color='orange')
    ax.text(Tpre/2, 0.5, 'PRECHARGE\n(20ns)', ha='center', va='center', fontsize=11, fontweight='bold')
    ax.text((t_comp_start+t_comp_end)/2, 0.5, f'COMPUTE\n({t_max_pulse:.0f}ns)', ha='center', va='center', fontsize=11, fontweight='bold')
    ax.text((t_comp_end+t[-1])/2, 0.5, 'SETTLE\n(~0.1ns)', ha='center', va='center', fontsize=11, fontweight='bold')
    ax.set_xlabel('Time (ns)')
    ax.set_ylabel('Phase')
    ax.set_yticks([])

    fig.tight_layout()
    fig.savefig(str(PLOTS_DIR / "compute_cycle_overview.png"), dpi=150)
    plt.close(fig)
    print(f"Plot saved: plots/compute_cycle_overview.png")
    wf_file.unlink()
else:
    print("ERROR: No waveform data generated")
