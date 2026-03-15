#!/usr/bin/env python3
"""
SPICE-based small-scale MVM validation for the CIM tile.

Generates an 8x8 SPICE testbench with known binary weights and binary inputs,
runs ngspice, extracts bitline voltages, and compares to the behavioral model.
"""

import numpy as np
import subprocess
import os
import sys
from pathlib import Path

BLOCK_DIR = Path(__file__).parent.resolve()
BLOCKS_DIR = BLOCK_DIR.parent
PLOTS_DIR = BLOCK_DIR / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(BLOCK_DIR))
from evaluate import load_upstream_measurements, CIMTileBehavioral


def generate_spice_testbench(W, x, size=8):
    """
    Generate a SPICE netlist for an 8x8 MVM with binary inputs.
    W: (size, size) binary weight matrix {0, 1}
    x: (size,) binary input vector {0, 1}
    """
    lines = []
    lines.append("* CIM Integration SPICE Validation — 8x8 Binary MVM")
    lines.append(f'* W = {W.tolist()}')
    lines.append(f'* x = {x.tolist()}')
    lines.append("")
    lines.append('.lib "sky130_models/sky130.lib.spice" tt')
    lines.append('.param supply=1.8')
    lines.append('.param Wpre=10 Lpre=0.15 Tpre_ns=20 Cbl_extra_ff=10000')
    lines.append('.param T_LSB=5n')
    lines.append('.param t_start={Tpre_ns*1e-9+1n}')
    lines.append("")

    # Bitcell subcircuit
    lines.append('.subckt cim_bitcell bl blb wl wwl q qb vdd vss')
    lines.append('XPL q qb vdd vdd sky130_fd_pr__pfet_01v8 w=0.55u l=0.15u')
    lines.append('XPR qb q vdd vdd sky130_fd_pr__pfet_01v8 w=0.55u l=0.15u')
    lines.append('XNL q qb vss vss sky130_fd_pr__nfet_01v8 w=0.84u l=0.15u')
    lines.append('XNR qb q vss vss sky130_fd_pr__nfet_01v8 w=0.84u l=0.15u')
    lines.append('XAXL blb wwl q vss sky130_fd_pr__nfet_01v8 w=0.42u l=0.15u')
    lines.append('XAXR bl wwl qb vss sky130_fd_pr__nfet_01v8 w=0.42u l=0.15u')
    lines.append('XRD1 bl q mid vss sky130_fd_pr__nfet_01v8 w=0.42u l=1.0u')
    lines.append('XRD2 mid wl vss vss sky130_fd_pr__nfet_01v8 w=0.42u l=1.0u')
    lines.append('.ends cim_bitcell')
    lines.append("")

    # Precharge
    lines.append('.subckt precharge bl pre vdd vss')
    lines.append('XPRE vdd pre bl vdd sky130_fd_pr__pfet_01v8 w=10u l=0.15u')
    lines.append('.ends precharge')
    lines.append("")

    # Supply
    lines.append('Vdd vdd 0 {supply}')
    lines.append('Vss vss 0 0')
    lines.append("")

    # Precharge signal
    lines.append('Vpre pre 0 PWL(0 0 {Tpre_ns*1e-9} 0 {(Tpre_ns+0.1)*1e-9} 1.8)')
    lines.append("")

    # Precharge and BL cap
    for j in range(size):
        lines.append(f'Xpre{j} bl{j} pre vdd vss precharge')
    for j in range(size):
        lines.append(f'Cbl{j} bl{j} 0 {{Cbl_extra_ff*1e-15}}')
    lines.append("")

    # Wordline signals (binary: 0 or 1 T_LSB pulse)
    for i in range(size):
        if x[i] > 0:
            lines.append(f'Vwl{i} wl{i} 0 PWL(0 0 {{t_start}} 0 {{t_start+0.1n}} 1.8 {{t_start+T_LSB}} 1.8 {{t_start+T_LSB+0.1n}} 0)')
        else:
            lines.append(f'Vwl{i} wl{i} 0 0')
    lines.append("")

    # Write wordlines held low
    for i in range(size):
        lines.append(f'Vwwl{i} wwl{i} 0 0')
    lines.append("")

    # Bitcell array
    for i in range(size):
        for j in range(size):
            lines.append(f'Xcell_r{i}_c{j} bl{j} blb{j} wl{i} wwl{i} q_r{i}c{j} qb_r{i}c{j} vdd vss cim_bitcell')
    lines.append("")

    # Weight programming via initial conditions
    for i in range(size):
        for j in range(size):
            if W[i, j] > 0:
                lines.append(f'.ic v(q_r{i}c{j})=1.8 v(qb_r{i}c{j})=0')
            else:
                lines.append(f'.ic v(q_r{i}c{j})=0 v(qb_r{i}c{j})=1.8')
    lines.append("")

    # BL initial conditions
    for j in range(size):
        lines.append(f'.ic v(bl{j})=1.8')
    lines.append("")

    # Simulation
    lines.append('.tran 0.05n 50n UIC')
    lines.append("")

    # Measure BL at settle time (after pulse ends + settle)
    t_meas = 40  # ns — well after the 5ns pulse ends
    for j in range(size):
        lines.append(f'.meas tran vbl{j} FIND v(bl{j}) AT={t_meas}n')
    lines.append("")

    # Save and control
    save_nodes = ' '.join([f'v(bl{j})' for j in range(size)])
    save_nodes += ' ' + ' '.join([f'v(wl{i})' for i in range(size)])
    save_nodes += ' v(pre)'
    lines.append(f'.save {save_nodes}')
    lines.append("")
    lines.append('.control')
    lines.append('run')
    bl_cols = ' '.join([f'v(bl{j})' for j in range(size)])
    lines.append(f'wrdata spice_bl_results.txt {bl_cols}')
    lines.append('.endc')
    lines.append('.end')

    return '\n'.join(lines)


def run_spice(netlist_path, timeout=120):
    """Run ngspice on the netlist."""
    try:
        result = subprocess.run(
            ['ngspice', '-b', str(netlist_path)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(BLOCK_DIR)
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", -1
    except FileNotFoundError:
        return "", "ngspice not found", -1


def parse_spice_measurements(output):
    """Extract .meas results from ngspice output."""
    measurements = {}
    for line in output.split('\n'):
        if '=' in line and 'vbl' in line.lower():
            parts = line.strip().split('=')
            if len(parts) == 2:
                name = parts[0].strip().lower()
                try:
                    value = float(parts[1].strip())
                    measurements[name] = value
                except ValueError:
                    pass
    return measurements


def run_validation(n_tests=5):
    """Run multiple SPICE validation tests and compare to behavioral model."""
    measurements = load_upstream_measurements()
    tile = CIMTileBehavioral(measurements, max_input_value=1)

    print("\n" + "=" * 60)
    print("SPICE vs Behavioral Model Validation (8x8 array)")
    print("=" * 60)

    all_spice_bl = []
    all_behav_bl = []
    all_ideal_dot = []
    test_configs = []

    np.random.seed(42)

    for test_idx in range(n_tests):
        # Random binary weights and inputs
        W = np.random.choice([0, 1], size=(8, 8)).astype(float)
        x = np.random.choice([0, 1], size=8).astype(float)

        # Expected dot product
        ideal_dot = x @ W

        # Generate and run SPICE
        netlist = generate_spice_testbench(W, x.astype(int))
        netlist_path = BLOCK_DIR / f"spice_test_{test_idx}.cir"
        with open(netlist_path, 'w') as f:
            f.write(netlist)

        print(f"\n  Test {test_idx+1}/{n_tests}: W density={W.mean():.2f}, x active={x.sum():.0f}")
        stdout, stderr, rc = run_spice(netlist_path)

        if rc != 0:
            print(f"    SPICE failed (rc={rc})")
            if "not found" in stderr:
                print("    ngspice not available — skipping SPICE validation")
                return None
            continue

        # Parse SPICE results
        meas = parse_spice_measurements(stdout)
        if not meas:
            # Try parsing stderr (ngspice sometimes puts output there)
            meas = parse_spice_measurements(stderr)

        spice_bl = []
        for j in range(8):
            key = f'vbl{j}'
            if key in meas:
                spice_bl.append(meas[key])
            else:
                print(f"    Warning: {key} not found in SPICE output")
                spice_bl.append(1.8)  # fallback
        spice_bl = np.array(spice_bl)

        # Behavioral model comparison
        # Pad to 64x64 for tile
        W_pad = np.zeros((64, 64))
        W_pad[:8, :8] = W
        x_pad = np.zeros(64)
        x_pad[:8] = x

        # Run behavioral model (no noise for fair comparison)
        codes, v_bl_behav = tile.mvm(W_pad, x_pad, add_noise=False)
        behav_bl = v_bl_behav[:8]

        # Compare
        spice_discharge = 1.8 - spice_bl
        behav_discharge = 1.8 - behav_bl
        ideal_discharge = ideal_dot * tile.v_step_per_unit

        for j in range(8):
            print(f"    Col {j}: dot={ideal_dot[j]:.0f} | "
                  f"SPICE={spice_discharge[j]*1000:.1f}mV | "
                  f"Behav={behav_discharge[j]*1000:.1f}mV | "
                  f"Ideal={ideal_discharge[j]*1000:.1f}mV")

        all_spice_bl.extend(spice_discharge.tolist())
        all_behav_bl.extend(behav_discharge.tolist())
        all_ideal_dot.extend(ideal_discharge.tolist())
        test_configs.append((W.copy(), x.copy(), ideal_dot.copy()))

        # Cleanup
        netlist_path.unlink(missing_ok=True)

    if not all_spice_bl:
        print("  No SPICE results to plot")
        return None

    all_spice_bl = np.array(all_spice_bl)
    all_behav_bl = np.array(all_behav_bl)
    all_ideal_dot = np.array(all_ideal_dot)

    # Compute agreement metrics
    mask = all_ideal_dot > 0  # only compare non-zero dot products
    if mask.sum() > 0:
        spice_vs_ideal_rmse = np.sqrt(np.mean((all_spice_bl[mask] - all_ideal_dot[mask])**2))
        behav_vs_ideal_rmse = np.sqrt(np.mean((all_behav_bl[mask] - all_ideal_dot[mask])**2))
        spice_vs_behav_rmse = np.sqrt(np.mean((all_spice_bl[mask] - all_behav_bl[mask])**2))

        print(f"\n  SPICE vs Ideal RMSE: {spice_vs_ideal_rmse*1000:.2f} mV")
        print(f"  Behavioral vs Ideal RMSE: {behav_vs_ideal_rmse*1000:.2f} mV")
        print(f"  SPICE vs Behavioral RMSE: {spice_vs_behav_rmse*1000:.2f} mV")

        nrmse = spice_vs_behav_rmse / max(all_spice_bl.max(), 0.001) * 100
        print(f"  Normalized RMSE (SPICE vs Behav): {nrmse:.1f}%")

    # Plot
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    ax = axes[0]
    ax.scatter(all_ideal_dot*1000, all_spice_bl*1000, c='blue', alpha=0.7, s=40, label='SPICE')
    ax.scatter(all_ideal_dot*1000, all_behav_bl*1000, c='red', alpha=0.7, s=40, marker='x', label='Behavioral')
    lims = [0, max(all_ideal_dot.max(), all_spice_bl.max()) * 1000 * 1.1]
    ax.plot(lims, lims, 'k--', alpha=0.5, label='y=x')
    ax.set_xlabel('Ideal discharge (mV)')
    ax.set_ylabel('Measured discharge (mV)')
    ax.set_title('SPICE vs Behavioral vs Ideal')
    ax.legend()
    ax.grid(alpha=0.3)

    ax = axes[1]
    err_spice = (all_spice_bl - all_ideal_dot) * 1000
    err_behav = (all_behav_bl - all_ideal_dot) * 1000
    ax.hist(err_spice, bins=20, alpha=0.7, color='blue', label=f'SPICE (std={err_spice.std():.1f}mV)')
    ax.hist(err_behav, bins=20, alpha=0.7, color='red', label=f'Behav (std={err_behav.std():.1f}mV)')
    ax.set_xlabel('Error vs Ideal (mV)')
    ax.set_ylabel('Count')
    ax.set_title('Error Distribution')
    ax.legend()
    ax.grid(alpha=0.3)

    ax = axes[2]
    ax.scatter(all_spice_bl*1000, all_behav_bl*1000, c='purple', alpha=0.7, s=40)
    lims = [0, max(all_spice_bl.max(), all_behav_bl.max()) * 1000 * 1.1]
    ax.plot(lims, lims, 'k--', alpha=0.5, label='y=x')
    ax.set_xlabel('SPICE discharge (mV)')
    ax.set_ylabel('Behavioral discharge (mV)')
    ax.set_title('SPICE vs Behavioral (direct)')
    ax.legend()
    ax.grid(alpha=0.3)
    ax.set_aspect('equal')

    plt.suptitle('Integration TB1: SPICE vs Behavioral Model Validation', fontsize=14)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "spice_vs_behavioral.png"), dpi=150)
    plt.close()
    print(f"\n  Saved plots/spice_vs_behavioral.png")

    return {
        'spice_vs_ideal_rmse_mv': spice_vs_ideal_rmse * 1000 if mask.sum() > 0 else None,
        'behav_vs_ideal_rmse_mv': behav_vs_ideal_rmse * 1000 if mask.sum() > 0 else None,
        'spice_vs_behav_rmse_mv': spice_vs_behav_rmse * 1000 if mask.sum() > 0 else None,
        'n_tests': n_tests,
    }


if __name__ == "__main__":
    results = run_validation(n_tests=5)
    if results:
        print(f"\nSPICE validation complete: {results}")
    else:
        print("\nSPICE validation skipped (ngspice not available)")
