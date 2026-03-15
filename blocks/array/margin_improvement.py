#!/usr/bin/env python3
"""
Phase B.5: Margin improvement and additional verification.
- Multi-vector test (TB5)
- Sparse weight test (more realistic neural network patterns)
- BL voltage distribution analysis
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from evaluate import (
    evaluate, generate_netlist, run_ngspice, parse_measurements,
    compute_ideal_mvm, compute_mvm_errors,
    load_bitcell_params, load_pwm_params, VDD, BLOCK_DIR,
    score, passes_specs, spec_summary
)

PLOTS_DIR = BLOCK_DIR / "plots"

params = {
    "Wpre": 10.0,
    "Lpre": 0.15,
    "Tpre_ns": 20.0,
    "Cbl_extra_ff": 10000.0,
}
bitcell_params = load_bitcell_params()
pwm_params = load_pwm_params()


def tb5_multi_vector_test():
    """TB5: Multi-vector test with 10 random pairs, box plot of RMSE."""
    print("\n" + "="*60)
    print("TB5: Multi-Vector Test (10 random weight/input pairs)")
    print("="*60)

    results = evaluate(params=params, n_rows=8, n_cols=8, n_tests=10,
                       verbose=True, seed=42)
    print(f"\n{spec_summary(results)}")
    return results


def sparse_weight_test():
    """Test with sparse weights (10-20% density, more realistic for BNNs)."""
    print("\n" + "="*60)
    print("Sparse Weight Test (10-20% weight density)")
    print("="*60)

    np.random.seed(999)
    n_rows, n_cols = 64, 8
    n_tests = 5

    all_rmse = []
    all_max_err = []

    for t in range(n_tests):
        # Sparse weights: 15% density
        W = (np.random.rand(n_rows, n_cols) < 0.15).astype(int)
        x = np.random.randint(0, 16, size=(n_rows,))

        netlist, t_meas, t_start = generate_netlist(
            n_rows, n_cols, W, x, params, bitcell_params, pwm_params
        )
        output, rc = run_ngspice(netlist)
        meas = parse_measurements(output, n_cols)

        v_sim = np.array([meas.get(f"vbl{c}", VDD) for c in range(n_cols)])

        c_bl = (n_rows * bitcell_params["c_bl_cell_ff"] + params["Cbl_extra_ff"]) * 1e-15
        v_ideal = compute_ideal_mvm(
            W, x, pwm_params["t_lsb_ns"],
            bitcell_params["i_read_ua"],
            n_rows * bitcell_params["c_bl_cell_ff"] + params["Cbl_extra_ff"]
        )

        rmse, max_err = compute_mvm_errors(v_sim, v_ideal)
        all_rmse.append(rmse)
        all_max_err.append(max_err)

        wt_density = W.sum() / W.size * 100
        print(f"  Test {t+1}: density={wt_density:.0f}%, RMSE={rmse:.4f}%, MaxErr={max_err:.4f}%")
        print(f"    Sim BL range: [{v_sim.min():.4f}, {v_sim.max():.4f}]V")
        print(f"    Ideal BL range: [{v_ideal.min():.4f}, {v_ideal.max():.4f}]V")

    avg_rmse = np.mean(all_rmse)
    avg_max = np.max(all_max_err)
    print(f"\nSparse weight results: RMSE={avg_rmse:.4f}%, MaxErr={avg_max:.4f}%")

    return all_rmse, all_max_err


def bl_voltage_distribution():
    """Analyze BL voltage distribution across many test vectors."""
    print("\n" + "="*60)
    print("BL Voltage Distribution Analysis")
    print("="*60)

    np.random.seed(42)
    n_rows, n_cols = 64, 8
    n_tests = 5

    all_v_sim = []
    all_v_ideal = []

    # Dense weights (50%)
    for t in range(n_tests):
        W = np.random.randint(0, 2, size=(n_rows, n_cols))
        x = np.random.randint(0, 16, size=(n_rows,))

        netlist, t_meas, _ = generate_netlist(n_rows, n_cols, W, x, params, bitcell_params, pwm_params)
        output, _ = run_ngspice(netlist)
        meas = parse_measurements(output, n_cols)
        v_sim = np.array([meas.get(f"vbl{c}", VDD) for c in range(n_cols)])
        v_ideal = compute_ideal_mvm(
            W, x, pwm_params["t_lsb_ns"], bitcell_params["i_read_ua"],
            n_rows * bitcell_params["c_bl_cell_ff"] + params["Cbl_extra_ff"]
        )
        all_v_sim.extend(v_sim)
        all_v_ideal.extend(v_ideal)

    # Plot
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    axes[0].hist(all_v_sim, bins=30, edgecolor='black', alpha=0.7, color='blue', label='Simulated')
    axes[0].hist(all_v_ideal, bins=30, edgecolor='black', alpha=0.5, color='red', label='Ideal')
    axes[0].set_xlabel('BL Voltage (V)')
    axes[0].set_ylabel('Count')
    axes[0].set_title('BL Voltage Distribution (64×8, 50% weight density)')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    # Correlation plot
    axes[1].scatter(all_v_ideal, all_v_sim, alpha=0.6, s=30)
    axes[1].plot([0, VDD], [0, VDD], 'r--', label='y=x')
    axes[1].set_xlabel('Ideal BL Voltage (V)')
    axes[1].set_ylabel('Simulated BL Voltage (V)')
    axes[1].set_title('Sim vs Ideal (64×8)')
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)
    axes[1].set_aspect('equal')
    axes[1].set_xlim(-0.05, VDD+0.05)
    axes[1].set_ylim(-0.05, VDD+0.05)

    fig.suptitle('BL Voltage Analysis — 64-Row CIM Array', fontsize=14, fontweight='bold')
    fig.tight_layout()
    fig.savefig(str(PLOTS_DIR / "bl_voltage_distribution.png"), dpi=150)
    plt.close(fig)
    print(f"Plot saved: plots/bl_voltage_distribution.png")


if __name__ == "__main__":
    tb5_multi_vector_test()
    sparse_weight_test()
    bl_voltage_distribution()
    print("\n### MARGIN IMPROVEMENT ANALYSIS COMPLETE ###")
