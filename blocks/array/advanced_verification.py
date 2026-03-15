#!/usr/bin/env python3
"""
Advanced verification:
1. Two consecutive compute cycles (verify precharge restores BL correctly)
2. Supply current analysis
3. Try to reduce precharge time with larger PMOS
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import re
from evaluate import (
    load_bitcell_params, load_pwm_params, make_bitcell_subckt,
    run_ngspice, parse_measurements, generate_netlist,
    VDD, BLOCK_DIR, SKY130_LIB
)

PLOTS_DIR = BLOCK_DIR / "plots"
bitcell_params = load_bitcell_params()
pwm_params = load_pwm_params()


def two_cycle_test():
    """Run two consecutive compute cycles to verify precharge restores BL correctly."""
    print("\n" + "="*60)
    print("TWO-CYCLE TEST (verify precharge between cycles)")
    print("="*60)

    Wpre = 10.0
    Tpre = 20.0
    Cbl_extra = 10000.0
    n_rows, n_cols = 8, 4
    t_lsb = pwm_params["t_lsb_ns"]
    t_rf = pwm_params["t_rf_ns"]
    c_bl_cell = bitcell_params["c_bl_cell_ff"]

    # Timing for two cycles
    t_max_pulse = 15 * t_lsb  # 75ns
    cycle_time = Tpre + 1 + t_max_pulse + 20  # precharge + margin + compute + settle
    t_cycle1_pre_start = 0
    t_cycle1_pre_end = Tpre
    t_cycle1_compute_start = Tpre + 1
    t_cycle1_meas = t_cycle1_compute_start + t_max_pulse + 15

    t_cycle2_pre_start = cycle_time
    t_cycle2_pre_end = cycle_time + Tpre
    t_cycle2_compute_start = cycle_time + Tpre + 1
    t_cycle2_meas = t_cycle2_compute_start + t_max_pulse + 15

    t_sim = t_cycle2_meas + 10

    # Weight matrix (same for both cycles)
    W = np.array([[1, 0, 1, 0],
                   [0, 1, 0, 1],
                   [1, 1, 0, 0],
                   [0, 0, 1, 1],
                   [1, 0, 0, 1],
                   [0, 1, 1, 0],
                   [1, 1, 1, 0],
                   [0, 0, 0, 1]])

    # Different inputs for each cycle
    x1 = np.array([8, 4, 12, 1, 15, 0, 7, 10])
    x2 = np.array([3, 11, 5, 14, 2, 9, 6, 1])

    lines = []
    lines.append(f"* Two-Cycle CIM Test")
    lines.append(f'.lib "{SKY130_LIB}" tt')
    lines.append(f".param supply={VDD}")
    lines.append("")
    lines.append(make_bitcell_subckt(bitcell_params))
    lines.append("")
    lines.append(".subckt precharge bl pre vdd vss")
    lines.append(f"XPRE vdd pre bl vdd sky130_fd_pr__pfet_01v8 w={Wpre}u l=0.15u")
    lines.append(".ends precharge")
    lines.append("")
    lines.append("Vdd vdd 0 {supply}")
    lines.append("Vss vss 0 0")
    lines.append("")

    # Precharge signal: two cycles
    # Cycle 1: PRE low from 0 to Tpre, then high
    # Cycle 2: PRE low from cycle_time to cycle_time+Tpre, then high
    lines.append(f"Vpre pre 0 PWL(0 0 {Tpre}n 0 {Tpre+0.1}n 1.8 "
                 f"{t_cycle2_pre_start}n 1.8 {t_cycle2_pre_start+0.1}n 0 "
                 f"{t_cycle2_pre_end}n 0 {t_cycle2_pre_end+0.1}n 1.8)")
    lines.append("")

    for c in range(n_cols):
        lines.append(f"Xpre{c} bl{c} pre vdd vss precharge")
    lines.append("")
    for c in range(n_cols):
        lines.append(f"Cbl{c} bl{c} 0 {Cbl_extra}f")
    lines.append("")

    # Wordline signals for both cycles
    for r in range(n_rows):
        # Cycle 1 pulse
        v1 = int(x1[r])
        # Cycle 2 pulse
        v2 = int(x2[r])

        pwl_parts = ["0 0"]
        if v1 > 0:
            t0 = t_cycle1_compute_start
            pw = v1 * t_lsb
            pwl_parts.extend([
                f"{t0}n 0", f"{t0+t_rf}n 1.8",
                f"{t0+pw}n 1.8", f"{t0+pw+t_rf}n 0"
            ])
        if v2 > 0:
            t0 = t_cycle2_compute_start
            pw = v2 * t_lsb
            pwl_parts.extend([
                f"{t0}n 0", f"{t0+t_rf}n 1.8",
                f"{t0+pw}n 1.8", f"{t0+pw+t_rf}n 0"
            ])

        lines.append(f"Vwl{r} wl{r} 0 PWL({' '.join(pwl_parts)})")
    lines.append("")

    for r in range(n_rows):
        lines.append(f"Vwwl{r} wwl{r} 0 0")
    lines.append("")

    # Cells
    for r in range(n_rows):
        for c in range(n_cols):
            lines.append(f"Xcell_r{r}_c{c} bl{c} blb{c} wl{r} wwl{r} q_r{r}c{c} qb_r{r}c{c} vdd vss cim_bitcell")
    lines.append("")

    # Initial conditions
    for c in range(n_cols):
        lines.append(f".ic v(bl{c})={VDD}")
    for r in range(n_rows):
        for c in range(n_cols):
            w = W[r, c]
            lines.append(f".ic v(q_r{r}c{c})={VDD if w else 0} v(qb_r{r}c{c})={0 if w else VDD}")
    lines.append("")

    lines.append(f".tran 0.05n {t_sim}n UIC")
    lines.append("")

    # Measurements at end of each cycle
    for c in range(n_cols):
        lines.append(f".meas tran vbl1_{c} FIND v(bl{c}) AT={t_cycle1_meas}n")
        lines.append(f".meas tran vbl2_{c} FIND v(bl{c}) AT={t_cycle2_meas}n")
        lines.append(f".meas tran vbl_pre2_{c} FIND v(bl{c}) AT={t_cycle2_pre_end}n")
    lines.append("")

    save_sigs = " ".join([f"v(bl{c})" for c in range(n_cols)])
    lines.append(f".save {save_sigs} v(pre) v(wl0)")
    lines.append(".control")
    lines.append("run")
    lines.append(f"wrdata two_cycle_wf.txt {save_sigs} v(pre) v(wl0)")
    lines.append(".endc")
    lines.append(".end")

    netlist = "\n".join(lines)
    output, rc = run_ngspice(netlist)

    # Parse
    v_cycle1 = []
    v_cycle2 = []
    v_pre2 = []
    for c in range(n_cols):
        m1 = re.search(rf"vbl1_{c}\s*=\s*([0-9eE.+-]+)", output, re.I)
        m2 = re.search(rf"vbl2_{c}\s*=\s*([0-9eE.+-]+)", output, re.I)
        mp = re.search(rf"vbl_pre2_{c}\s*=\s*([0-9eE.+-]+)", output, re.I)
        v_cycle1.append(float(m1.group(1)) if m1 else 0)
        v_cycle2.append(float(m2.group(1)) if m2 else 0)
        v_pre2.append(float(mp.group(1)) if mp else 0)

    print(f"Cycle 1 inputs: {x1}")
    print(f"Cycle 2 inputs: {x2}")
    print(f"\nCycle 1 BL voltages: {[f'{v:.4f}' for v in v_cycle1]}")
    print(f"After precharge 2:  {[f'{v:.4f}' for v in v_pre2]}")
    print(f"Cycle 2 BL voltages: {[f'{v:.4f}' for v in v_cycle2]}")

    precharge_err = [abs(VDD - v) * 1000 for v in v_pre2]
    print(f"\nPrecharge error before cycle 2: {[f'{e:.1f}mV' for e in precharge_err]}")
    max_pre_err = max(precharge_err)
    print(f"Max precharge error: {max_pre_err:.1f} mV")

    # Plot
    wf_file = BLOCK_DIR / "two_cycle_wf.txt"
    if wf_file.exists():
        data = np.loadtxt(str(wf_file))
        t = data[:, 0] * 1e9
        fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

        # Precharge and WL0
        axes[0].plot(t, data[:, n_cols+1], 'k-', linewidth=1.5, label='PRE (gate)')
        axes[0].plot(t, data[:, n_cols+2], 'g--', alpha=0.7, label='WL0')
        axes[0].set_ylabel('Voltage (V)')
        axes[0].set_title('Control Signals')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # Bitlines
        colors = ['blue', 'red', 'green', 'orange']
        for c in range(n_cols):
            axes[1].plot(t, data[:, 1+c], color=colors[c], linewidth=1.5, label=f'BL{c}')
        axes[1].axhline(VDD, color='gray', linestyle=':', alpha=0.5)
        axes[1].set_ylabel('Voltage (V)')
        axes[1].set_title('Bitline Voltages (2 Consecutive Compute Cycles)')
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        # Zoomed precharge region
        pre2_start = t_cycle2_pre_start - 5
        pre2_end = t_cycle2_pre_end + 5
        mask = (t >= pre2_start) & (t <= pre2_end)
        for c in range(n_cols):
            axes[2].plot(t[mask], data[mask, 1+c], color=colors[c], linewidth=1.5, label=f'BL{c}')
        axes[2].axhline(VDD, color='gray', linestyle=':', alpha=0.5)
        axes[2].axhline(VDD-0.005, color='orange', linestyle=':', alpha=0.5, label='VDD-5mV')
        axes[2].set_ylabel('Voltage (V)')
        axes[2].set_xlabel('Time (ns)')
        axes[2].set_title('Precharge Between Cycles (Zoomed)')
        axes[2].legend(fontsize=8)
        axes[2].grid(True, alpha=0.3)

        fig.suptitle('Two-Cycle CIM Operation', fontsize=14, fontweight='bold')
        fig.tight_layout()
        fig.savefig(str(PLOTS_DIR / "two_cycle_operation.png"), dpi=150)
        plt.close(fig)
        print(f"\nPlot saved: plots/two_cycle_operation.png")
        wf_file.unlink()

    return v_cycle1, v_cycle2, v_pre2


if __name__ == "__main__":
    two_cycle_test()
    print("\n### ADVANCED VERIFICATION COMPLETE ###")
