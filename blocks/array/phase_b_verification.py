#!/usr/bin/env python3
"""
Phase B: Deep Verification
1. Precharge stress test (charge from 0V)
2. Parameter sensitivity analysis
3. Anti-gaming checks
4. Edge cases: all-zero weights, all-one weights, single-row active
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from evaluate import (
    evaluate, generate_netlist, run_ngspice, parse_measurements,
    load_bitcell_params, load_pwm_params, make_bitcell_subckt,
    VDD, BLOCK_DIR, SKY130_LIB, score, passes_specs, spec_summary
)
import re

PLOTS_DIR = BLOCK_DIR / "plots"

params = {
    "Wpre": 4.0,
    "Lpre": 0.15,
    "Tpre_ns": 5.0,
    "Cbl_extra_ff": 10000.0,
}
bitcell_params = load_bitcell_params()
pwm_params = load_pwm_params()


def anti_gaming_checks():
    """Verify the circuit is actually computing, not just outputting constant values."""
    print("\n" + "="*60)
    print("ANTI-GAMING CHECKS")
    print("="*60)

    n_rows, n_cols = 8, 4

    # Check 1: All-zero weights -> all BLs should stay at VDD
    print("\n1. All-zero weights (expect BL ≈ VDD):")
    W = np.zeros((n_rows, n_cols), dtype=int)
    x = np.full(n_rows, 8, dtype=int)
    netlist, t_meas, t_start = generate_netlist(n_rows, n_cols, W, x, params, bitcell_params, pwm_params)
    output, _ = run_ngspice(netlist)
    meas = parse_measurements(output, n_cols)
    for c in range(n_cols):
        vbl = meas.get(f"vbl{c}", 0)
        ok = abs(vbl - VDD) < 0.01
        print(f"   BL{c} = {vbl:.4f}V  {'OK' if ok else 'FAIL - should be near VDD!'}")

    # Check 2: All-one weights -> BLs should discharge significantly
    print("\n2. All-one weights, input=8 (expect BL << VDD):")
    W = np.ones((n_rows, n_cols), dtype=int)
    x = np.full(n_rows, 8, dtype=int)
    netlist, t_meas, t_start = generate_netlist(n_rows, n_cols, W, x, params, bitcell_params, pwm_params)
    output, _ = run_ngspice(netlist)
    meas = parse_measurements(output, n_cols)
    for c in range(n_cols):
        vbl = meas.get(f"vbl{c}", VDD)
        drop = VDD - vbl
        print(f"   BL{c} = {vbl:.4f}V  (drop = {drop:.4f}V)  {'OK' if drop > 0.1 else 'FAIL - should discharge!'}")

    # Check 3: Single row active -> only that row's contribution
    print("\n3. Single row active (row 0, input=15, all weights=1):")
    W = np.ones((n_rows, n_cols), dtype=int)
    x = np.zeros(n_rows, dtype=int)
    x[0] = 15
    netlist, t_meas, t_start = generate_netlist(n_rows, n_cols, W, x, params, bitcell_params, pwm_params)
    output, _ = run_ngspice(netlist)
    meas = parse_measurements(output, n_cols)
    vbls = []
    for c in range(n_cols):
        vbl = meas.get(f"vbl{c}", VDD)
        vbls.append(vbl)
        print(f"   BL{c} = {vbl:.4f}V")
    # All columns should have same voltage (all weights=1 for this row)
    spread = max(vbls) - min(vbls)
    print(f"   Spread across columns: {spread*1000:.2f} mV  {'OK' if spread < 5e-3 else 'WARNING - should be uniform'}")

    # Check 4: Swap weight columns -> outputs should swap
    print("\n4. Column swap test:")
    W_a = np.array([[1,0,1,0],[0,1,0,1],[1,1,0,0],[0,0,1,1],
                     [1,0,0,1],[0,1,1,0],[1,1,1,0],[0,0,0,1]])
    x = np.array([5, 10, 3, 12, 7, 1, 8, 6])

    # Original
    netlist, t_meas, _ = generate_netlist(n_rows, n_cols, W_a, x, params, bitcell_params, pwm_params)
    output, _ = run_ngspice(netlist)
    meas_a = parse_measurements(output, n_cols)
    v_a = [meas_a.get(f"vbl{c}", VDD) for c in range(n_cols)]

    # Swap columns 0 and 1
    W_b = W_a.copy()
    W_b[:, [0, 1]] = W_b[:, [1, 0]]
    netlist, t_meas, _ = generate_netlist(n_rows, n_cols, W_b, x, params, bitcell_params, pwm_params)
    output, _ = run_ngspice(netlist)
    meas_b = parse_measurements(output, n_cols)
    v_b = [meas_b.get(f"vbl{c}", VDD) for c in range(n_cols)]

    print(f"   Original: BL0={v_a[0]:.4f}V  BL1={v_a[1]:.4f}V")
    print(f"   Swapped:  BL0={v_b[0]:.4f}V  BL1={v_b[1]:.4f}V")
    swap_ok = (abs(v_a[0] - v_b[1]) < 0.005 and abs(v_a[1] - v_b[0]) < 0.005)
    print(f"   Columns swapped correctly: {swap_ok}")


def parameter_sensitivity():
    """Test sensitivity to key parameters."""
    print("\n" + "="*60)
    print("PARAMETER SENSITIVITY ANALYSIS")
    print("="*60)

    base_params = params.copy()
    n_rows, n_cols, n_tests = 8, 8, 3

    # Sweep each parameter
    sweeps = {
        "Wpre": [1.0, 2.0, 4.0, 6.0, 8.0],
        "Lpre": [0.15, 0.20, 0.30, 0.50],
        "Tpre_ns": [2.0, 3.0, 5.0, 10.0, 15.0],
        "Cbl_extra_ff": [5000, 7500, 10000, 12000, 15000],
    }

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()

    for idx, (param_name, values) in enumerate(sweeps.items()):
        rmses = []
        max_errs = []
        for val in values:
            p = base_params.copy()
            p[param_name] = float(val)
            results = evaluate(params=p, n_rows=n_rows, n_cols=n_cols,
                               n_tests=n_tests, verbose=False, seed=42)
            if results:
                rmses.append(results["mvm_rmse_pct"])
                max_errs.append(results["max_error_pct"])
            else:
                rmses.append(None)
                max_errs.append(None)

            status = "PASS" if results and passes_specs(results) else "FAIL"
            rmse_str = f"{results['mvm_rmse_pct']:.4f}" if results else "N/A"
            print(f"  {param_name}={val}: RMSE={rmse_str}% {status}")

        ax = axes[idx]
        valid = [(v, r) for v, r in zip(values, rmses) if r is not None]
        if valid:
            vs, rs = zip(*valid)
            ax.plot(vs, rs, 'bo-', markersize=6)
        ax.axhline(10, color='r', linestyle='--', alpha=0.5, label='Spec limit')
        ax.set_xlabel(param_name)
        ax.set_ylabel('RMSE (%)')
        ax.set_title(f'Sensitivity to {param_name}')
        ax.legend()
        ax.grid(True, alpha=0.3)

    fig.suptitle('Parameter Sensitivity Analysis (8×8)', fontsize=14, fontweight='bold')
    fig.tight_layout()
    fig.savefig(str(PLOTS_DIR / "parameter_sensitivity.png"), dpi=150)
    plt.close(fig)
    print(f"\nPlot saved: plots/parameter_sensitivity.png")


def edge_case_tests():
    """Test edge cases for robustness."""
    print("\n" + "="*60)
    print("EDGE CASE TESTS")
    print("="*60)

    n_rows, n_cols = 8, 4

    # Edge 1: All inputs = 0 (no pulses)
    print("\n1. All inputs = 0 (expect BL ≈ VDD):")
    W = np.ones((n_rows, n_cols), dtype=int)
    x = np.zeros(n_rows, dtype=int)
    netlist, t_meas, _ = generate_netlist(n_rows, n_cols, W, x, params, bitcell_params, pwm_params)
    output, _ = run_ngspice(netlist)
    meas = parse_measurements(output, n_cols)
    for c in range(n_cols):
        vbl = meas.get(f"vbl{c}", 0)
        print(f"   BL{c} = {vbl:.4f}V  {'OK' if abs(vbl - VDD) < 0.01 else 'FAIL'}")

    # Edge 2: All inputs = 15, all weights = 1 (maximum discharge)
    print("\n2. All inputs = 15, all weights = 1 (max discharge):")
    W = np.ones((n_rows, n_cols), dtype=int)
    x = np.full(n_rows, 15, dtype=int)
    netlist, t_meas, _ = generate_netlist(n_rows, n_cols, W, x, params, bitcell_params, pwm_params)
    output, _ = run_ngspice(netlist)
    meas = parse_measurements(output, n_cols)
    for c in range(n_cols):
        vbl = meas.get(f"vbl{c}", VDD)
        print(f"   BL{c} = {vbl:.4f}V  {'OK - above ground' if vbl > -0.05 else 'FAIL - below ground!'}")

    # Edge 3: Identity-like weight matrix
    print("\n3. Diagonal weight matrix (each output depends on one input):")
    W = np.eye(n_rows, n_cols, dtype=int)  # n_rows x n_cols, ones on diagonal
    x = np.array([1, 5, 10, 15, 0, 3, 7, 12])
    netlist, t_meas, _ = generate_netlist(n_rows, n_cols, W, x, params, bitcell_params, pwm_params)
    output, _ = run_ngspice(netlist)
    meas = parse_measurements(output, n_cols)
    for c in range(n_cols):
        vbl = meas.get(f"vbl{c}", VDD)
        active_input = x[c] if c < n_rows else 0
        print(f"   BL{c}: V={vbl:.4f}V  (input code={active_input})")
    # BL voltages should decrease with increasing input code
    vbls = [meas.get(f"vbl{c}", VDD) for c in range(n_cols)]
    print(f"   BL0 (in=1) > BL2 (in=10) > BL3 (in=15): {vbls[0] > vbls[2] > vbls[3]}")


if __name__ == "__main__":
    anti_gaming_checks()
    edge_case_tests()
    parameter_sensitivity()
    print("\n\n### PHASE B VERIFICATION COMPLETE ###")
