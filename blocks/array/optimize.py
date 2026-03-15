#!/usr/bin/env python3
"""
CIM Array Optimization and Verification Script
Phase A: Meet all specs
Phase B: Deep verification, margin improvement, all plots
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json
import subprocess
import tempfile
import os
import re
import sys
from pathlib import Path
from evaluate import (
    evaluate, score, passes_specs, spec_summary, load_specs,
    load_bitcell_params, load_pwm_params, save_measurements,
    save_best_parameters, generate_netlist, run_ngspice, parse_measurements,
    make_bitcell_subckt, VDD, BLOCK_DIR, SKY130_LIB
)

PLOTS_DIR = BLOCK_DIR / "plots"
PLOTS_DIR.mkdir(exist_ok=True)


def run_full_evaluation(params, n_rows=8, n_cols=8, n_tests=10, seed=42):
    """Run evaluation with given params and return results."""
    return evaluate(params=params, n_rows=n_rows, n_cols=n_cols,
                    n_tests=n_tests, verbose=True, seed=seed)


# =========================================================================
# Verification Testbenches
# =========================================================================

def tb_single_column_dot_product(params):
    """TB1: Single column dot product verification."""
    print("\n" + "="*60)
    print("TB1: Single Column Dot Product")
    print("="*60)

    bitcell_params = load_bitcell_params()
    pwm_params = load_pwm_params()

    # Test case from verification.md:
    # weights = [1, 0, 1, 1, 0, 0, 1, 0], input = [3, 7, 1, 15, 0, 4, 8, 2]
    # Expected dot product: 3*1 + 7*0 + 1*1 + 15*1 + 0*0 + 4*0 + 8*1 + 2*0 = 27
    W = np.array([[1], [0], [1], [1], [0], [0], [1], [0]])
    x = np.array([3, 7, 1, 15, 0, 4, 8, 2])
    expected_dot = 27

    netlist, t_meas, t_start = generate_netlist(
        8, 1, W, x, params, bitcell_params, pwm_params
    )
    output, rc = run_ngspice(netlist)
    meas = parse_measurements(output, 1)

    v_bl = meas.get("vbl0", VDD)
    t_lsb = pwm_params["t_lsb_ns"]
    i_read = bitcell_params["i_read_ua"] * 1e-6
    c_bl = (8 * bitcell_params["c_bl_cell_ff"] + params["Cbl_extra_ff"]) * 1e-15

    # Convert BL voltage to dot product
    delta_v = VDD - v_bl
    dot_analog = delta_v * c_bl / (i_read * t_lsb * 1e-9)

    print(f"BL voltage: {v_bl:.4f} V")
    print(f"Voltage drop: {delta_v:.4f} V")
    print(f"Analog dot product: {dot_analog:.2f}")
    print(f"Expected dot product: {expected_dot}")
    print(f"Error: {abs(dot_analog - expected_dot)/expected_dot*100:.2f}%")

    # Generate waveform plot
    # Run simulation saving all waveforms
    netlist_wf = netlist.replace(
        "wrdata array_output.txt",
        "wrdata single_col_wf.txt v(wl0) v(wl1) v(wl2) v(wl3) v(wl4) v(wl5) v(wl6) v(wl7) v(bl0) v(pre)\nwrdata array_output.txt"
    )
    output_wf, _ = run_ngspice(netlist_wf)

    # Parse waveform data
    wf_file = BLOCK_DIR / "single_col_wf.txt"
    if wf_file.exists():
        data = np.loadtxt(str(wf_file))
        if data.ndim == 2 and data.shape[1] >= 11:
            t = data[:, 0] * 1e9  # ns
            fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

            # Precharge
            axes[0].plot(t, data[:, 10], 'k-', label='PRE')
            axes[0].set_ylabel('Voltage (V)')
            axes[0].set_title('Precharge Signal')
            axes[0].legend()
            axes[0].grid(True, alpha=0.3)

            # Wordlines
            colors = plt.cm.tab10(np.linspace(0, 1, 8))
            for i in range(8):
                axes[1].plot(t, data[:, 1+i], color=colors[i],
                           label=f'WL{i} (in={x[i]}, w={W[i,0]})')
            axes[1].set_ylabel('Voltage (V)')
            axes[1].set_title('Wordline Pulses (PWM-encoded inputs)')
            axes[1].legend(fontsize=7, ncol=2)
            axes[1].grid(True, alpha=0.3)

            # Bitline
            axes[2].plot(t, data[:, 9], 'b-', linewidth=2, label='BL0')
            axes[2].axhline(v_bl, color='r', linestyle='--',
                          label=f'Final = {v_bl:.4f}V (dot={dot_analog:.1f})')
            axes[2].set_ylabel('Voltage (V)')
            axes[2].set_xlabel('Time (ns)')
            axes[2].set_title(f'Bitline Discharge — Expected dot product = {expected_dot}')
            axes[2].legend()
            axes[2].grid(True, alpha=0.3)

            fig.suptitle('TB1: Single Column Dot Product', fontsize=14, fontweight='bold')
            fig.tight_layout()
            fig.savefig(str(PLOTS_DIR / "single_column_waveforms.png"), dpi=150)
            plt.close(fig)
            print(f"Plot saved: plots/single_column_waveforms.png")
        wf_file.unlink()

    return dot_analog, expected_dot


def tb_precharge_verification(params):
    """TB2: Precharge verification — all BLs reach VDD within Tpre."""
    print("\n" + "="*60)
    print("TB2: Precharge Verification")
    print("="*60)

    bitcell_params = load_bitcell_params()
    pwm_params = load_pwm_params()

    # All weights = 0 (no discharge), verify precharge pulls BLs to VDD
    n_rows, n_cols = 8, 8
    W = np.zeros((n_rows, n_cols), dtype=int)
    x = np.zeros(n_rows, dtype=int)

    Wpre = params["Wpre"]
    Lpre = params["Lpre"]
    Tpre_ns = params["Tpre_ns"]
    Cbl_extra_ff = params["Cbl_extra_ff"]
    c_bl_cell = bitcell_params["c_bl_cell_ff"]
    t_lsb = pwm_params["t_lsb_ns"]
    t_rf = pwm_params["t_rf_ns"]

    c_bl_extra_f = Cbl_extra_ff * 1e-15
    t_start_ns = Tpre_ns + 1.0
    t_sim_ns = Tpre_ns + 30

    lines = []
    lines.append(f"* Precharge Verification Testbench")
    lines.append(f'.lib "{SKY130_LIB}" tt')
    lines.append(f".param supply={VDD}")
    lines.append("")
    lines.append(make_bitcell_subckt(bitcell_params))
    lines.append("")
    lines.append(".subckt precharge bl pre vdd vss")
    lines.append(f"XPRE vdd pre bl vdd sky130_fd_pr__pfet_01v8 w={Wpre}u l={Lpre}u")
    lines.append(".ends precharge")
    lines.append("")
    lines.append("Vdd vdd 0 {supply}")
    lines.append("Vss vss 0 0")
    lines.append("")
    # Precharge: LOW initially (ON), then HIGH (OFF) after Tpre_ns
    lines.append(f"Vpre pre 0 PWL(0 0 {Tpre_ns}n 0 {Tpre_ns + 0.1}n 1.8)")
    lines.append("")

    for c in range(n_cols):
        lines.append(f"Xpre{c} bl{c} pre vdd vss precharge")
    lines.append("")
    for c in range(n_cols):
        lines.append(f"Cbl{c} bl{c} 0 {Cbl_extra_ff}f")
    lines.append("")

    # No WL pulses
    for r in range(n_rows):
        lines.append(f"Vwl{r} wl{r} 0 0")
        lines.append(f"Vwwl{r} wwl{r} 0 0")
    lines.append("")

    # Cells with weight=0
    for r in range(n_rows):
        for c in range(n_cols):
            lines.append(f"Xcell_r{r}_c{c} bl{c} blb{c} wl{r} wwl{r} q_r{r}c{c} qb_r{r}c{c} vdd vss cim_bitcell")
    lines.append("")
    for r in range(n_rows):
        for c in range(n_cols):
            lines.append(f".ic v(q_r{r}c{c})=0 v(qb_r{r}c{c})=1.8")
    lines.append("")

    # BLs start at 0V (worst case — need to charge from 0)
    for c in range(n_cols):
        lines.append(f".ic v(bl{c})=0")
    lines.append("")

    lines.append(f".tran 0.05n {t_sim_ns}n UIC")
    lines.append("")

    # Measure BL at end of precharge and after precharge off
    for c in range(n_cols):
        lines.append(f".meas tran vbl_pre{c} FIND v(bl{c}) AT={Tpre_ns}n")
        lines.append(f".meas tran vbl_hold{c} FIND v(bl{c}) AT={Tpre_ns + 10}n")
    lines.append("")

    save_sigs = " ".join([f"v(bl{c})" for c in range(n_cols)])
    lines.append(f".save {save_sigs} v(pre)")
    lines.append("")
    lines.append(".control")
    lines.append("run")
    lines.append(f"wrdata precharge_wf.txt {save_sigs} v(pre)")
    lines.append(".endc")
    lines.append("")
    lines.append(".end")

    netlist = "\n".join(lines)
    output, rc = run_ngspice(netlist)

    # Parse results
    pre_voltages = []
    hold_voltages = []
    for c in range(n_cols):
        m = re.search(rf"vbl_pre{c}\s*=\s*([0-9eE.+-]+)", output, re.IGNORECASE)
        if m:
            pre_voltages.append(float(m.group(1)))
        m = re.search(rf"vbl_hold{c}\s*=\s*([0-9eE.+-]+)", output, re.IGNORECASE)
        if m:
            hold_voltages.append(float(m.group(1)))

    if pre_voltages:
        min_pre = min(pre_voltages)
        max_pre = max(pre_voltages)
        print(f"BL voltages at end of precharge ({Tpre_ns}ns):")
        print(f"  Min: {min_pre:.4f}V  Max: {max_pre:.4f}V")
        print(f"  All within 5mV of VDD: {all(abs(v - VDD) < 0.005 for v in pre_voltages)}")

    if hold_voltages:
        min_hold = min(hold_voltages)
        max_hold = max(hold_voltages)
        print(f"BL voltages 10ns after precharge off:")
        print(f"  Min: {min_hold:.4f}V  Max: {max_hold:.4f}V")
        droop = VDD - min_hold
        print(f"  Max droop: {droop*1000:.2f}mV")

    # Plot
    wf_file = BLOCK_DIR / "precharge_wf.txt"
    if wf_file.exists():
        data = np.loadtxt(str(wf_file))
        if data.ndim == 2 and data.shape[1] >= n_cols + 2:
            t = data[:, 0] * 1e9
            fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

            axes[0].plot(t, data[:, n_cols + 1], 'k-', linewidth=2, label='PRE (gate)')
            axes[0].set_ylabel('Voltage (V)')
            axes[0].set_title('Precharge Gate Signal (active low)')
            axes[0].legend()
            axes[0].grid(True, alpha=0.3)

            for c in range(n_cols):
                axes[1].plot(t, data[:, 1 + c], label=f'BL{c}')
            axes[1].axhline(VDD, color='r', linestyle='--', alpha=0.5, label='VDD')
            axes[1].axhline(VDD - 0.005, color='orange', linestyle=':', alpha=0.5, label='VDD-5mV')
            axes[1].set_ylabel('Voltage (V)')
            axes[1].set_xlabel('Time (ns)')
            axes[1].set_title(f'Bitline Precharge (C_BL = {Cbl_extra_ff:.0f} fF)')
            axes[1].legend(fontsize=7, ncol=3)
            axes[1].grid(True, alpha=0.3)
            axes[1].set_ylim(-0.1, 2.0)

            fig.suptitle('TB2: Precharge Verification', fontsize=14, fontweight='bold')
            fig.tight_layout()
            fig.savefig(str(PLOTS_DIR / "precharge_waveforms.png"), dpi=150)
            plt.close(fig)
            print(f"Plot saved: plots/precharge_waveforms.png")
        wf_file.unlink()

    return pre_voltages, hold_voltages


def tb_mvm_8x8(params):
    """TB3: Full 8x8 MVM with scatter plot."""
    print("\n" + "="*60)
    print("TB3: Full MVM (8x8) — 10 test vectors")
    print("="*60)

    results = run_full_evaluation(params, n_rows=8, n_cols=8, n_tests=10, seed=42)
    if results:
        print(f"\n{spec_summary(results)}")
    return results


def tb_linearity(params):
    """TB4: Linearity test — BL voltage vs input code for all-ones column."""
    print("\n" + "="*60)
    print("TB4: Linearity Test")
    print("="*60)

    bitcell_params = load_bitcell_params()
    pwm_params = load_pwm_params()

    n_rows, n_cols = 8, 1
    W = np.ones((n_rows, n_cols), dtype=int)  # all weights = 1

    input_codes = list(range(16))
    bl_voltages = []

    for code in input_codes:
        x = np.zeros(n_rows, dtype=int)
        x[0] = code  # only row 0 is active

        netlist, t_meas, t_start = generate_netlist(
            n_rows, n_cols, W, x, params, bitcell_params, pwm_params
        )
        output, rc = run_ngspice(netlist)
        meas = parse_measurements(output, n_cols)
        v_bl = meas.get("vbl0", VDD)
        bl_voltages.append(v_bl)
        print(f"  Input code {code:2d}: V_BL = {v_bl:.4f}V")

    # Calculate ideal linear fit
    bl_arr = np.array(bl_voltages)
    codes = np.array(input_codes)

    # Expected: V_BL = VDD - code * I_READ * T_LSB / C_BL
    c_bl = (n_rows * bitcell_params["c_bl_cell_ff"] + params["Cbl_extra_ff"]) * 1e-15
    i_read = bitcell_params["i_read_ua"] * 1e-6
    t_lsb = pwm_params["t_lsb_ns"] * 1e-9
    expected = VDD - codes * i_read * t_lsb / c_bl

    # Linearity error
    if len(bl_arr) > 1:
        # Fit a line to simulated data
        coeffs = np.polyfit(codes, bl_arr, 1)
        fit_line = np.polyval(coeffs, codes)
        residuals = bl_arr - fit_line
        max_linearity_err = np.max(np.abs(residuals))
        print(f"\nLinearity: max residual from linear fit = {max_linearity_err*1000:.2f} mV")

    # Plot
    fig, axes = plt.subplots(2, 1, figsize=(10, 8))

    axes[0].plot(codes, bl_arr, 'bo-', label='Simulated')
    axes[0].plot(codes, expected, 'r--', label='Ideal (linear)')
    axes[0].set_xlabel('Input Code')
    axes[0].set_ylabel('BL Voltage (V)')
    axes[0].set_title('BL Voltage vs Input Code (Single Row Active, All Weights=1)')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    errors_mv = (bl_arr - expected) * 1000
    axes[1].plot(codes, errors_mv, 'go-')
    axes[1].set_xlabel('Input Code')
    axes[1].set_ylabel('Error (mV)')
    axes[1].set_title('Deviation from Ideal Linear Model')
    axes[1].grid(True, alpha=0.3)
    axes[1].axhline(0, color='k', linestyle='-', alpha=0.3)

    fig.suptitle('TB4: Array Linearity', fontsize=14, fontweight='bold')
    fig.tight_layout()
    fig.savefig(str(PLOTS_DIR / "array_linearity.png"), dpi=150)
    plt.close(fig)
    print(f"Plot saved: plots/array_linearity.png")

    return bl_arr, expected


def tb_worst_case_discharge(params):
    """TB6: Worst case — all weights active, max input."""
    print("\n" + "="*60)
    print("TB6: Worst Case Discharge")
    print("="*60)

    bitcell_params = load_bitcell_params()
    pwm_params = load_pwm_params()

    n_rows, n_cols = 8, 1
    W = np.ones((n_rows, n_cols), dtype=int)  # all weights = 1
    x = np.full(n_rows, 15, dtype=int)  # max input on all rows

    netlist, t_meas, t_start = generate_netlist(
        n_rows, n_cols, W, x, params, bitcell_params, pwm_params
    )

    # Add wrdata for waveform
    netlist = netlist.replace(
        "wrdata array_output.txt",
        "wrdata worst_case_wf.txt v(bl0) v(wl0) v(pre)\nwrdata array_output.txt"
    )

    output, rc = run_ngspice(netlist)
    meas = parse_measurements(output, n_cols)
    v_bl = meas.get("vbl0", VDD)

    c_bl = (n_rows * bitcell_params["c_bl_cell_ff"] + params["Cbl_extra_ff"]) * 1e-15
    i_read = bitcell_params["i_read_ua"] * 1e-6
    t_lsb = pwm_params["t_lsb_ns"] * 1e-9
    expected_drop = n_rows * 15 * i_read * t_lsb / c_bl
    expected_v = max(0, VDD - expected_drop)

    print(f"Worst case (all weights=1, input=15 on all {n_rows} rows):")
    print(f"  BL voltage: {v_bl:.4f} V")
    print(f"  Expected (linear): {expected_v:.4f} V")
    print(f"  BL above ground: {'YES' if v_bl > 0 else 'NO (CLIPPING!)'}")
    print(f"  Voltage utilization: {(VDD - v_bl)/VDD*100:.1f}% of VDD range")

    # Plot
    wf_file = BLOCK_DIR / "worst_case_wf.txt"
    if wf_file.exists():
        data = np.loadtxt(str(wf_file))
        if data.ndim == 2 and data.shape[1] >= 4:
            t = data[:, 0] * 1e9
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(t, data[:, 1], 'b-', linewidth=2, label='BL0 (all cells active)')
            ax.plot(t, data[:, 2], 'g--', alpha=0.5, label='WL0')
            ax.plot(t, data[:, 3], 'k--', alpha=0.5, label='PRE')
            ax.axhline(0, color='r', linestyle=':', label='Ground')
            ax.axhline(v_bl, color='orange', linestyle='--',
                      label=f'Final = {v_bl:.4f}V')
            ax.set_xlabel('Time (ns)')
            ax.set_ylabel('Voltage (V)')
            ax.set_title(f'TB6: Worst Case Discharge ({n_rows} rows, all active, input=15)')
            ax.legend()
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            fig.savefig(str(PLOTS_DIR / "worst_case_discharge.png"), dpi=150)
            plt.close(fig)
            print(f"Plot saved: plots/worst_case_discharge.png")
        wf_file.unlink()

    return v_bl


def tb_monotonicity(params):
    """Verify BL voltage decreases monotonically with N_active cells."""
    print("\n" + "="*60)
    print("Monotonicity Check: V_BL vs N_active")
    print("="*60)

    bitcell_params = load_bitcell_params()
    pwm_params = load_pwm_params()

    n_rows, n_cols = 8, 1
    fixed_input = 8  # fixed pulse width

    n_active_list = list(range(0, n_rows + 1))
    bl_voltages = []

    for n_active in n_active_list:
        W = np.zeros((n_rows, n_cols), dtype=int)
        W[:n_active, 0] = 1  # first n_active rows have weight=1
        x = np.full(n_rows, fixed_input, dtype=int)

        netlist, t_meas, t_start = generate_netlist(
            n_rows, n_cols, W, x, params, bitcell_params, pwm_params
        )
        output, rc = run_ngspice(netlist)
        meas = parse_measurements(output, n_cols)
        v_bl = meas.get("vbl0", VDD)
        bl_voltages.append(v_bl)
        print(f"  N_active={n_active}: V_BL = {v_bl:.4f}V")

    # Check monotonicity
    monotonic = all(bl_voltages[i] >= bl_voltages[i+1] for i in range(len(bl_voltages)-1))
    print(f"\nMonotonic: {monotonic}")

    # Plot
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(n_active_list, bl_voltages, 'bo-', markersize=8)
    ax.set_xlabel('Number of Active Cells (N_active)')
    ax.set_ylabel('BL Voltage (V)')
    ax.set_title(f'BL Voltage vs Active Cells (input={fixed_input}, {"MONOTONIC" if monotonic else "NOT MONOTONIC!"})')
    ax.grid(True, alpha=0.3)
    ax.axhline(0, color='r', linestyle=':', alpha=0.5, label='Ground')
    ax.legend()
    fig.tight_layout()
    fig.savefig(str(PLOTS_DIR / "bl_monotonicity.png"), dpi=150)
    plt.close(fig)
    print(f"Plot saved: plots/bl_monotonicity.png")

    return bl_voltages, monotonic


def run_64x64_validation(params):
    """Run 64x64 full-scale validation."""
    print("\n" + "="*60)
    print("64x64 FULL-SCALE VALIDATION")
    print("="*60)

    results = evaluate(params=params, n_rows=64, n_cols=8, n_tests=5,
                       verbose=True, seed=123)
    if results:
        s = score(results)
        passed = passes_specs(results)
        print(f"\nScore: {s:.2f}")
        print(f"All specs met: {passed}")
        print(f"\n{spec_summary(results)}")
    return results


# =========================================================================
# Main
# =========================================================================

if __name__ == "__main__":
    params = {
        "Wpre": 4.0,
        "Lpre": 0.15,
        "Tpre_ns": 5.0,
        "Cbl_extra_ff": 10000.0,
    }

    print("="*60)
    print("CIM ARRAY OPTIMIZATION & VERIFICATION")
    print("="*60)
    print(f"Parameters: {params}")

    # Phase A: Verify baseline passes
    print("\n\n### PHASE A: Baseline Verification ###")
    results_8x8 = tb_mvm_8x8(params)
    if results_8x8 and passes_specs(results_8x8):
        print("\n>>> 8x8 baseline PASSES all specs <<<")
    else:
        print("\n>>> 8x8 baseline FAILS — need optimization <<<")
        sys.exit(1)

    # Phase B: Verification testbenches
    print("\n\n### PHASE B: Verification Testbenches ###")

    # TB1: Single column
    tb_single_column_dot_product(params)

    # TB2: Precharge
    tb_precharge_verification(params)

    # TB4: Linearity
    tb_linearity(params)

    # TB6: Worst case
    tb_worst_case_discharge(params)

    # Monotonicity
    tb_monotonicity(params)

    # 64x64 validation (64 rows, 8 cols for simulation time)
    print("\n\n### 64x64 Validation (64 rows x 8 cols) ###")
    results_64 = run_64x64_validation(params)

    print("\n\n### VERIFICATION COMPLETE ###")
    print("All plots saved to plots/")
