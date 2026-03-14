#!/usr/bin/env python3
"""
PWM Wordline Driver — Evaluation Script
Sweeps all 16 input codes, measures pulse width linearity, rise/fall times, and power.
"""

import subprocess
import re
import json
import csv
import os
import sys
import tempfile
import numpy as np
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SPECS_FILE = "specs.json"
PARAMS_FILE = "parameters.csv"
DESIGN_FILE = "design.cir"
RESULTS_FILE = "results.tsv"
SUPPLY = 1.8
CLK_PERIOD = 333.3e-9  # 3MHz

def load_specs():
    with open(SPECS_FILE) as f:
        return json.load(f)

def load_parameters():
    """Load current parameter values from best_parameters.csv or defaults."""
    best_file = "best_parameters.csv"
    if os.path.exists(best_file):
        params = {}
        with open(best_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                params[row["name"]] = float(row["value"])
        if params:
            return params

    # Default: reasonable starting values
    params = {
        "Wbuf": 4.0,
        "Lbuf": 0.15,
        "Nstages": 3,
        "Tlsb": 5.0,
        "Wlogic": 1.0,
    }
    return params

def build_netlist(params, input_code, corner="tt", temp=27):
    """Generate a SPICE netlist for a specific input code."""
    with open(DESIGN_FILE) as f:
        template = f.read()

    netlist = template
    nstages = int(round(params.get("Nstages", 3)))
    tlsb_ns = params.get("Tlsb", 5.0)
    wbuf = params.get("Wbuf", 4)
    lbuf = params.get("Lbuf", 0.15)
    wlogic = params.get("Wlogic", 1)

    # Replace the .param line with actual values
    import re as _re
    old_param_line = _re.search(r'\.param\s+Wbuf=.*', netlist)
    if old_param_line:
        new_param_line = (f'.param Wbuf={wbuf}u '
                          f'Lbuf={lbuf}u '
                          f'Nstages={nstages} '
                          f'Tlsb={tlsb_ns}n '
                          f'Wlogic={wlogic}u')
        netlist = netlist[:old_param_line.start()] + new_param_line + netlist[old_param_line.end():]

    # Generate INVERTED PWL pulse source for the given input code
    # The 3-inverter chain inverts, so input must be inverted:
    #   idle = VDD, active pulse = 0V
    clk_period_ns = 333.3
    edge_time = 0.1  # ns
    pwl_points = []
    supply = SUPPLY

    if input_code == 0:
        pwl_points = [(0, supply), (700, supply)]
    else:
        pulse_width_ns = input_code * tlsb_ns
        for cycle in range(2):
            t_start = cycle * clk_period_ns
            t_fall = t_start + edge_time
            t_low = t_fall + edge_time
            t_rise_start = t_fall + pulse_width_ns
            t_high = t_rise_start + edge_time

            pwl_points.append((t_fall, supply))
            pwl_points.append((t_low, 0))
            pwl_points.append((t_rise_start, 0))
            pwl_points.append((t_high, supply))

    pwl_str = " ".join(f"{t}n {v}" for t, v in pwl_points)
    # Replace the DC source with PWL
    netlist = netlist.replace("Vpwm pwm_in 0 DC {supply}",
                              f"Vpwm pwm_in 0 PWL({pwl_str})")

    # Set input bit DC voltage sources
    for i in range(4):
        bit_val = (input_code >> i) & 1
        v = supply if bit_val else 0
        netlist = netlist.replace(f"Vin{i} in{i} 0 0",
                                  f"Vin{i} in{i} 0 {v}")

    # Set corner
    netlist = netlist.replace('.lib "sky130_models/sky130.lib.spice" tt',
                              f'.lib "sky130_models/sky130.lib.spice" {corner}')

    # Set temperature
    netlist = netlist.replace(".control",
                              f".temp {temp}\n.control")

    return netlist

def run_ngspice(netlist_str, tag="sim"):
    """Write netlist to temp file and run ngspice, return stdout."""
    fname = f"pwm_{tag}.cir"
    with open(fname, "w") as f:
        f.write(netlist_str)

    result = subprocess.run(
        ["ngspice", "-b", fname],
        capture_output=True, text=True, timeout=120
    )
    return result.stdout + result.stderr

def parse_meas(output, name):
    """Extract a .meas result from ngspice output."""
    patterns = [
        rf"{name}\s*=\s*([eE0-9.+-]+)",
        rf"{name}\s*=\s*([0-9.]+[eE][+-]?[0-9]+)",
    ]
    for pat in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None

def measure_code(params, code, corner="tt", temp=27):
    """Simulate one input code, return dict of measurements."""
    netlist = build_netlist(params, code, corner, temp)
    output = run_ngspice(netlist, tag=f"code{code}")

    pw = parse_meas(output, "pw_rise")
    tr = parse_meas(output, "t_rise")
    tf = parse_meas(output, "t_fall")
    pwr = parse_meas(output, "power_uw")

    return {
        "code": code,
        "pulse_width_ns": pw * 1e9 if pw else None,
        "rise_time_ns": tr * 1e9 if tr else None,
        "fall_time_ns": tf * 1e9 if tf else None,
        "power_uw": pwr if pwr else None,
    }

def compute_linearity(pulse_widths):
    """
    Compute linearity error as max deviation from ideal line.
    pulse_widths: dict mapping code -> pulse_width_ns (codes 1..15)
    Returns: linearity error in percent
    """
    codes = sorted(pulse_widths.keys())
    if len(codes) < 2:
        return 100.0

    widths = np.array([pulse_widths[c] for c in codes])
    codes_arr = np.array(codes, dtype=float)

    # Ideal: pulse_width = code * T_LSB
    # Fit T_LSB from data (least squares through origin)
    t_lsb_fit = np.sum(codes_arr * widths) / np.sum(codes_arr ** 2)
    ideal = codes_arr * t_lsb_fit

    # Max deviation as percentage of full-scale (code 15)
    max_dev = np.max(np.abs(widths - ideal))
    full_scale = 15 * t_lsb_fit
    linearity_pct = (max_dev / full_scale) * 100 if full_scale > 0 else 100

    return linearity_pct, t_lsb_fit

def evaluate(params=None, corner="tt", temp=27, verbose=True):
    """
    Full evaluation: sweep all 16 codes, compute all metrics.
    Returns dict of measurements matching specs.json keys.
    """
    if params is None:
        params = load_parameters()

    if verbose:
        print(f"Evaluating PWM driver: corner={corner}, temp={temp}C")
        print(f"Parameters: {params}")

    # --- Sweep all codes ---
    pulse_widths = {}
    rise_times = []
    fall_times = []
    powers = []

    for code in range(16):
        result = measure_code(params, code, corner, temp)

        if verbose:
            print(f"  Code {code:2d}: pw={result['pulse_width_ns']}, "
                  f"tr={result['rise_time_ns']}, tf={result['fall_time_ns']}, "
                  f"pwr={result['power_uw']}")

        if code == 0:
            # Code 0: verify no pulse (pulse width should be ~0 or unmeasurable)
            if result["pulse_width_ns"] is not None and result["pulse_width_ns"] > 0.5:
                print(f"  WARNING: Code 0 has non-zero pulse width: "
                      f"{result['pulse_width_ns']:.2f} ns")
            continue

        if result["pulse_width_ns"] is not None:
            pulse_widths[code] = result["pulse_width_ns"]
        if result["rise_time_ns"] is not None:
            rise_times.append(result["rise_time_ns"])
        if result["fall_time_ns"] is not None:
            fall_times.append(result["fall_time_ns"])
        if result["power_uw"] is not None:
            powers.append(result["power_uw"])

    # --- Compute metrics ---
    measurements = {}

    # Linearity
    if len(pulse_widths) >= 2:
        lin_pct, t_lsb = compute_linearity(pulse_widths)
        measurements["linearity_pct"] = round(lin_pct, 3)
        measurements["t_lsb_ns"] = round(t_lsb, 3)
    else:
        measurements["linearity_pct"] = 100.0
        measurements["t_lsb_ns"] = 0.0

    # Rise/fall time (worst case across codes)
    measurements["rise_time_ns"] = round(max(rise_times), 3) if rise_times else 10.0
    measurements["fall_time_ns"] = round(max(fall_times), 3) if fall_times else 10.0

    # Power (average across codes)
    measurements["power_uw"] = round(np.mean(powers), 3) if powers else 100.0

    if verbose:
        print(f"\n--- Results ---")
        for k, v in measurements.items():
            print(f"  {k}: {v}")

    return measurements

def compute_cost(measurements, specs=None):
    """
    Compute weighted cost from measurements vs specs.
    Lower is better. 0 = all specs met with margin.
    """
    if specs is None:
        specs = load_specs()

    total_cost = 0
    total_weight = 0
    details = {}

    for name, spec in specs["measurements"].items():
        weight = spec["weight"]
        target = spec["target"]
        value = measurements.get(name, None)

        if value is None:
            penalty = 10.0  # missing measurement
        elif target.startswith("<"):
            limit = float(target[1:])
            if value <= limit:
                penalty = 0
            else:
                penalty = (value - limit) / limit
        elif target.startswith(">"):
            limit = float(target[1:])
            if value >= limit:
                penalty = 0
            else:
                penalty = (limit - value) / limit
        elif "-" in target:
            lo, hi = map(float, target.split("-"))
            if lo <= value <= hi:
                penalty = 0
            elif value < lo:
                penalty = (lo - value) / lo
            else:
                penalty = (value - hi) / hi
        else:
            penalty = 0

        cost = weight * penalty
        total_cost += cost
        total_weight += weight
        details[name] = {"value": value, "target": target, "penalty": penalty,
                         "cost": cost}

    normalized = total_cost / total_weight if total_weight > 0 else 10
    return normalized, details

def save_results(params, measurements, cost):
    """Append results to results.tsv."""
    header_needed = not os.path.exists(RESULTS_FILE) or \
                    os.path.getsize(RESULTS_FILE) == 0
    cols = list(params.keys()) + list(measurements.keys()) + ["cost"]
    with open(RESULTS_FILE, "a") as f:
        if header_needed:
            f.write("\t".join(cols) + "\n")
        vals = [str(params.get(c, measurements.get(c, ""))) for c in cols[:-1]]
        vals.append(str(round(cost, 6)))
        f.write("\t".join(vals) + "\n")

# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------
def plot_linearity(pulse_widths, t_lsb, outfile="plots/linearity.png"):
    """Plot measured vs ideal pulse width."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available, skipping plot")
        return

    os.makedirs("plots", exist_ok=True)
    codes = sorted(pulse_widths.keys())
    measured = [pulse_widths[c] for c in codes]
    ideal = [c * t_lsb for c in codes]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 8))

    ax1.plot(codes, measured, "bo-", label="Measured")
    ax1.plot(codes, ideal, "r--", label=f"Ideal (T_LSB={t_lsb:.2f} ns)")
    ax1.set_xlabel("Input Code")
    ax1.set_ylabel("Pulse Width (ns)")
    ax1.set_title("PWM Driver Linearity")
    ax1.legend()
    ax1.grid(True)

    errors = [(m - i) for m, i in zip(measured, ideal)]
    ax2.bar(codes, errors, color="orange")
    ax2.set_xlabel("Input Code")
    ax2.set_ylabel("Error (ns)")
    ax2.set_title("Pulse Width Error vs Ideal")
    ax2.grid(True)

    plt.tight_layout()
    plt.savefig(outfile, dpi=150)
    plt.close()
    print(f"Saved linearity plot to {outfile}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    params = load_parameters()
    measurements = evaluate(params, verbose=True)
    cost, details = compute_cost(measurements)

    print(f"\nCost: {cost:.4f}")
    print("Details:")
    for name, d in details.items():
        status = "PASS" if d["penalty"] == 0 else "FAIL"
        print(f"  {name}: {d['value']} (target {d['target']}) [{status}]")

    save_results(params, measurements, cost)
    print(f"\nResults appended to {RESULTS_FILE}")
