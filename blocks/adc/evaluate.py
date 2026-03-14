"""
evaluate.py -- Simulation and validation utilities for 6-bit SAR ADC design.

Provides:
- NGSpice simulation runner
- Input sweep and output code extraction
- DNL, INL, ENOB computation
- Cost function, scoring, and plotting

This file does NOT contain an optimizer. The agent chooses and implements
its own optimization strategy.

Usage as utility library:
    from evaluate import (load_parameters, load_design, load_specs,
                          run_adc_sweep, compute_dnl_inl, compute_enob,
                          compute_cost, evaluate_params)

Usage standalone (validate existing best_parameters.csv):
    python evaluate.py                   # full validation
    python evaluate.py --quick           # quick validation
"""

import os
import sys
import re
import json
import csv
import time
import argparse
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NGSPICE = os.environ.get("NGSPICE", "ngspice")
DESIGN_FILE = "design.cir"
PARAMS_FILE = "parameters.csv"
SPECS_FILE = "specs.json"
RESULTS_FILE = "results.tsv"
PLOTS_DIR = "plots"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# ADC parameters
N_BITS = 6
N_CODES = 2 ** N_BITS  # 64
N_SWEEP_POINTS = 256  # input voltage sweep resolution

# Nominal corner
NOMINAL_CORNER = "tt"
NOMINAL_TEMP = 24
NOMINAL_SUPPLY = 1.8

# ---------------------------------------------------------------------------
# Parameter loading
# ---------------------------------------------------------------------------

def load_parameters(path: str = PARAMS_FILE) -> List[Dict]:
    params = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            params.append({
                "name": row["name"].strip(),
                "min": float(row["min"]),
                "max": float(row["max"]),
                "scale": row.get("scale", "lin").strip(),
            })
    return params


def load_design(path: str = DESIGN_FILE) -> str:
    with open(path) as f:
        return f.read()


def load_specs(path: str = SPECS_FILE) -> Dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_design(template: str, params: List[Dict]) -> List[str]:
    errors = []
    # Scan ALL lines (including .control blocks) for placeholders
    non_comment_lines = []
    for line in template.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("*"):
            non_comment_lines.append(line)
    full_text = "\n".join(non_comment_lines)
    placeholders = set(re.findall(r'\{(\w+)\}', full_text))
    param_names = {p["name"] for p in params}

    # These are set by the evaluator, not design parameters
    evaluator_params = {"corner", "Vsupply", "temperature"}
    design_placeholders = placeholders - evaluator_params

    for m in sorted(design_placeholders - param_names):
        errors.append(f"Placeholder {{{m}}} in design.cir has no entry in parameters.csv")
    for u in sorted(param_names - design_placeholders):
        errors.append(f"Parameter '{u}' in parameters.csv is not used in design.cir")

    return errors


# ---------------------------------------------------------------------------
# NGSpice simulation
# ---------------------------------------------------------------------------

def format_netlist(template: str, param_values: Dict[str, float],
                   corner: str = "tt", temperature: int = 24,
                   supply_v: float = 1.8) -> str:
    """Substitute all parameters including PVT settings."""
    all_params = dict(param_values)
    all_params["corner"] = corner
    all_params["temperature"] = str(temperature)
    all_params["Vsupply"] = str(supply_v)

    def _replace(match):
        key = match.group(1)
        if key in all_params:
            return str(all_params[key])
        return match.group(0)
    return re.sub(r'\{(\w+)\}', _replace, template)


def run_simulation(template: str, param_values: Dict[str, float],
                   idx: int, tmp_dir: str,
                   corner: str = "tt", temperature: int = 24,
                   supply_v: float = 1.8) -> Dict:
    """Run the SAR ADC simulation (full input sweep).

    Returns dict with keys: idx, error, codes (list of (vin, code) tuples),
    measurements (extracted metrics).
    """
    try:
        netlist = format_netlist(template, param_values,
                                 corner=corner, temperature=temperature,
                                 supply_v=supply_v)
    except Exception as e:
        return {"idx": idx, "error": f"format error: {e}",
                "codes": [], "measurements": {}}

    path = os.path.join(tmp_dir, f"adc_{idx}_{corner}_T{temperature}_V{supply_v}.cir")
    with open(path, "w") as f:
        f.write(netlist)

    try:
        result = subprocess.run(
            [NGSPICE, "-b", path],
            capture_output=True, text=True, timeout=600,
            cwd=PROJECT_DIR
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return {"idx": idx, "error": "timeout", "codes": [], "measurements": {}}
    except Exception as e:
        return {"idx": idx, "error": str(e), "codes": [], "measurements": {}}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if "RESULT_DONE" not in output:
        return {"idx": idx, "error": "no_RESULT_DONE", "codes": [],
                "measurements": {}, "output_tail": output[-1000:]}

    # Parse output codes from RESULT_STEP lines
    codes = parse_adc_codes(output)
    measurements = extract_measurements(output, codes, param_values, supply_v)
    measurements["corner"] = corner
    measurements["temperature"] = temperature
    measurements["supply_v"] = supply_v

    return {"idx": idx, "error": None, "codes": codes,
            "measurements": measurements}


def parse_adc_codes(output: str) -> List[Tuple[float, int]]:
    """Parse RESULT_STEP lines to extract (vin, code) pairs."""
    codes = []
    for line in output.split("\n"):
        match = re.search(r'RESULT_STEP\s+(\d+)\s+VIN\s+([\d.eE+-]+)\s+CODE\s+(\d+)', line)
        if match:
            vin = float(match.group(2))
            code = int(float(match.group(3)))
            codes.append((vin, code))
    return sorted(codes, key=lambda x: x[0])


def extract_measurements(output: str, codes: List[Tuple[float, int]],
                          param_values: Dict[str, float],
                          supply_v: float) -> Dict[str, float]:
    """Compute DNL, INL, ENOB from the code sweep data."""
    measurements = {}

    if len(codes) < 10:
        # Not enough data points
        measurements["RESULT_DNL_LSB"] = 99.0
        measurements["RESULT_INL_LSB"] = 99.0
        measurements["RESULT_ENOB"] = 0.0
        measurements["RESULT_CONVERSION_TIME_NS"] = 999.0
        measurements["RESULT_POWER_UW"] = 999.0
        return measurements

    # Extract conversion time from output
    conv_time_match = re.search(r'RESULT_CONVERSION_TIME_NS\s+([\d.eE+-]+)', output)
    if conv_time_match:
        measurements["RESULT_CONVERSION_TIME_NS"] = float(conv_time_match.group(1))
    else:
        tsar = param_values.get("Tsar_ns", 20.0)
        measurements["RESULT_CONVERSION_TIME_NS"] = 6.0 * tsar

    # Compute DNL and INL
    dnl, inl, max_dnl, max_inl = compute_dnl_inl(codes, supply_v)
    measurements["RESULT_DNL_LSB"] = max_dnl
    measurements["RESULT_INL_LSB"] = max_inl

    # Compute ENOB
    enob = compute_enob(codes, supply_v)
    measurements["RESULT_ENOB"] = enob

    # Estimate power (rough: C * V^2 * f for cap DAC switching)
    cu = param_values.get("Cu", 100.0) * 1e-15  # fF to F
    total_cap = 64 * cu
    tsar = param_values.get("Tsar_ns", 20.0) * 1e-9
    f_conv = 1.0 / (6 * tsar)
    # Energy per conversion ~ 0.5 * C_total * Vdd^2 * (average switching activity ~0.5)
    energy_per_conv = 0.5 * total_cap * supply_v**2 * 0.5
    power_uw = energy_per_conv * f_conv * 1e6
    measurements["RESULT_POWER_UW"] = power_uw

    return measurements


def compute_dnl_inl(codes: List[Tuple[float, int]],
                     supply_v: float) -> Tuple[np.ndarray, np.ndarray, float, float]:
    """Compute DNL and INL from (vin, code) sweep data.

    Returns (dnl_array, inl_array, max_abs_dnl, max_abs_inl).
    """
    n_codes = 2 ** N_BITS
    v_lsb_ideal = supply_v / n_codes

    # Find code transition points
    # Transition[k] = input voltage where code changes from k-1 to k
    transitions = {}
    for i in range(1, len(codes)):
        v_prev, c_prev = codes[i - 1]
        v_curr, c_curr = codes[i]
        if c_curr != c_prev:
            # Interpolate transition voltage
            v_trans = (v_prev + v_curr) / 2.0
            # Record transition for the new code
            if c_curr not in transitions:
                transitions[c_curr] = v_trans

    if len(transitions) < 2:
        return np.zeros(n_codes), np.zeros(n_codes), 99.0, 99.0

    # Sort transitions by code
    sorted_codes = sorted(transitions.keys())
    first_code = sorted_codes[0]
    last_code = sorted_codes[-1]

    # Compute actual step widths
    # Step width for code k = transition[k+1] - transition[k]
    dnl = np.zeros(n_codes)
    inl = np.zeros(n_codes)

    for i in range(len(sorted_codes) - 1):
        code_k = sorted_codes[i]
        code_k1 = sorted_codes[i + 1]
        actual_width = transitions[code_k1] - transitions[code_k]
        # DNL = (actual_width / ideal_width) - 1
        dnl_val = (actual_width / v_lsb_ideal) - 1.0
        dnl[code_k] = dnl_val

    # INL = cumulative sum of DNL
    inl_accum = 0.0
    for code_k in sorted_codes:
        inl_accum += dnl[code_k]
        inl[code_k] = inl_accum

    # Apply endpoint correction (remove gain and offset errors)
    if len(sorted_codes) >= 2:
        first = sorted_codes[0]
        last = sorted_codes[-1]
        if last != first:
            slope = (inl[last] - inl[first]) / (last - first)
            for k in range(n_codes):
                inl[k] -= inl[first] + slope * (k - first)

    max_dnl = np.max(np.abs(dnl[first_code:last_code + 1])) if first_code <= last_code else 99.0
    max_inl = np.max(np.abs(inl[first_code:last_code + 1])) if first_code <= last_code else 99.0

    return dnl, inl, max_dnl, max_inl


def compute_enob(codes: List[Tuple[float, int]], supply_v: float) -> float:
    """Compute ENOB from a ramp input sweep.

    Uses the RMS quantization error method:
    ENOB = N - log2(sigma_actual / sigma_ideal)
    where sigma_ideal = LSB / sqrt(12) for an ideal N-bit ADC.
    """
    if len(codes) < 10:
        return 0.0

    vins = np.array([c[0] for c in codes])
    code_vals = np.array([c[1] for c in codes])

    n_codes = 2 ** N_BITS
    v_lsb = supply_v / n_codes

    # Ideal code for each input voltage
    ideal_codes = np.clip(np.floor(vins / v_lsb), 0, n_codes - 1)

    # Quantization error
    errors = code_vals - ideal_codes
    rms_error = np.sqrt(np.mean(errors**2))

    # Ideal RMS quantization noise
    ideal_rms = 1.0 / np.sqrt(12.0)

    if rms_error < 1e-10:
        return float(N_BITS)  # Perfect (suspicious)

    enob = N_BITS - np.log2(rms_error / ideal_rms)
    enob = max(0.0, min(float(N_BITS), enob))

    return enob


# ---------------------------------------------------------------------------
# Cost function -- usable by any optimizer
# ---------------------------------------------------------------------------

def compute_cost(measurements: Dict[str, float], specs: Dict = None) -> float:
    """Cost function for optimization (lower is better).

    Penalises:
    - DNL > 0.5 LSB
    - INL > 1.0 LSB
    - ENOB < 5.0 bits
    - Conversion time > 200 ns
    - Power > 50 uW
    """
    if not measurements:
        return 1e6

    cost = 0.0

    # DNL penalty (target < 0.5 LSB, weight 30)
    dnl = measurements.get("RESULT_DNL_LSB", 99.0)
    if dnl < 0.5:
        cost -= (0.5 - dnl) / 0.5 * 30
    else:
        cost += ((dnl - 0.5) / 0.5) ** 2 * 300

    # INL penalty (target < 1.0 LSB, weight 25)
    inl = measurements.get("RESULT_INL_LSB", 99.0)
    if inl < 1.0:
        cost -= (1.0 - inl) / 1.0 * 25
    else:
        cost += ((inl - 1.0) / 1.0) ** 2 * 250

    # ENOB penalty (target > 5.0, weight 20)
    enob = measurements.get("RESULT_ENOB", 0.0)
    if enob > 5.0:
        cost -= (enob - 5.0) / 1.0 * 20
    else:
        cost += ((5.0 - enob) / 1.0) ** 2 * 200

    # Conversion time penalty (target < 200 ns, weight 15)
    conv_time = measurements.get("RESULT_CONVERSION_TIME_NS", 999.0)
    if conv_time < 200.0:
        cost -= (200.0 - conv_time) / 200.0 * 15
    else:
        cost += ((conv_time - 200.0) / 200.0) ** 2 * 150

    # Power penalty (target < 50 uW, weight 10)
    power = measurements.get("RESULT_POWER_UW", 999.0)
    if power < 50.0:
        cost -= (50.0 - power) / 50.0 * 10
    else:
        cost += ((power - 50.0) / 50.0) ** 2 * 100

    return cost


def evaluate_params(template: str, param_values: Dict[str, float],
                    specs: Dict = None) -> Tuple[float, Dict]:
    """Convenience: simulate at nominal corner and return (cost, measurements).

    Useful as the objective function for any optimizer.
    """
    tmp_dir = tempfile.mkdtemp(prefix="adc_eval_")
    result = run_simulation(template, param_values, 0, tmp_dir,
                            NOMINAL_CORNER, NOMINAL_TEMP, NOMINAL_SUPPLY)
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    if result.get("error") or not result.get("measurements"):
        return 1e6, {}

    measurements = result["measurements"]
    cost = compute_cost(measurements, specs)
    return cost, measurements


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _parse_target(target_str: str) -> Tuple[str, float, Optional[float]]:
    target_str = target_str.strip()
    if target_str.startswith(">"):
        return ("above", float(target_str[1:]), None)
    elif target_str.startswith("<"):
        return ("below", float(target_str[1:]), None)
    elif "-" in target_str and not target_str.startswith("-"):
        parts = target_str.split("-")
        return ("range", float(parts[0]), float(parts[1]))
    else:
        return ("exact", float(target_str), None)


def score_measurements(measurements: Dict[str, float], specs: Dict) -> Tuple[float, Dict]:
    details = {}
    total_weight = 0
    weighted_score = 0

    for spec_name, spec_def in specs["measurements"].items():
        target_str = spec_def["target"]
        weight = spec_def["weight"]
        unit = spec_def.get("unit", "")
        total_weight += weight

        direction, val1, val2 = _parse_target(target_str)
        measured = measurements.get(f"RESULT_{spec_name.upper()}", None)

        if measured is None:
            details[spec_name] = {
                "measured": None, "target": target_str, "met": False,
                "score": 0, "unit": unit
            }
            continue

        if direction == "above":
            met = measured >= val1
            spec_score = 1.0 if met else max(0, measured / val1) if val1 != 0 else 0
        elif direction == "below":
            met = measured <= val1
            spec_score = 1.0 if met else max(0, val1 / measured) if measured != 0 else 0
        elif direction == "exact":
            met = abs(measured - val1) < 0.01 * max(abs(val1), 1)
            spec_score = 1.0 if met else max(0, 1.0 - abs(measured - val1) / max(abs(val1), 1))
        else:
            met = False
            spec_score = 0

        weighted_score += weight * spec_score
        details[spec_name] = {
            "measured": measured, "target": target_str, "met": met,
            "score": spec_score, "unit": unit
        }

    overall = weighted_score / total_weight if total_weight > 0 else 0
    return overall, details


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def generate_plots(codes: List[Tuple[float, int]], measurements: Dict,
                   supply_v: float = 1.8):
    """Generate ADC validation plots: transfer curve, DNL, INL."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("  WARNING: matplotlib not available, skipping plots")
        return

    os.makedirs(PLOTS_DIR, exist_ok=True)

    dark_theme = {
        'figure.facecolor': '#1a1a2e', 'axes.facecolor': '#16213e',
        'axes.edgecolor': '#e94560', 'axes.labelcolor': '#eee',
        'text.color': '#eee', 'xtick.color': '#aaa', 'ytick.color': '#aaa',
        'grid.color': '#333', 'grid.alpha': 0.5, 'lines.linewidth': 1.5,
    }
    plt.rcParams.update(dark_theme)

    if not codes:
        return

    vins = [c[0] for c in codes]
    code_vals = [c[1] for c in codes]

    # --- Transfer Curve ---
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.step(vins, code_vals, where='mid', color='#00d2ff', linewidth=1)
    ax.plot(vins, [v / (supply_v / 64) for v in vins], '--', color='#e94560',
            alpha=0.5, label='Ideal')
    ax.set_xlabel('Input Voltage (V)')
    ax.set_ylabel('Output Code')
    ax.set_title('SAR ADC Transfer Curve')
    ax.legend()
    ax.grid(True)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'transfer_curve.png'), dpi=150)
    plt.close()

    # --- DNL and INL ---
    dnl, inl, max_dnl, max_inl = compute_dnl_inl(codes, supply_v)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))

    ax1.bar(range(len(dnl)), dnl, color='#00d2ff', alpha=0.8, width=1.0)
    ax1.axhline(y=0.5, color='#e94560', linestyle='--', label='Spec: +0.5 LSB')
    ax1.axhline(y=-0.5, color='#e94560', linestyle='--', label='Spec: -0.5 LSB')
    ax1.set_xlabel('Code')
    ax1.set_ylabel('DNL (LSB)')
    ax1.set_title(f'DNL (worst case: {max_dnl:.3f} LSB)')
    ax1.legend(fontsize=8)
    ax1.grid(True)

    ax2.plot(range(len(inl)), inl, color='#00ff88', linewidth=1.5)
    ax2.axhline(y=1.0, color='#e94560', linestyle='--', label='Spec: +1.0 LSB')
    ax2.axhline(y=-1.0, color='#e94560', linestyle='--', label='Spec: -1.0 LSB')
    ax2.set_xlabel('Code')
    ax2.set_ylabel('INL (LSB)')
    ax2.set_title(f'INL (worst case: {max_inl:.3f} LSB)')
    ax2.legend(fontsize=8)
    ax2.grid(True)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'dnl_inl.png'), dpi=150)
    plt.close()


def generate_progress_plot(results_file: str, plots_dir: str):
    """Generate progress.png from results.tsv."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return

    if not os.path.exists(results_file):
        return

    steps, scores = [], []
    with open(results_file) as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            try:
                steps.append(int(row.get("step", len(steps) + 1)))
                scores.append(float(row.get("score", 0)))
            except (ValueError, TypeError):
                continue

    if not scores:
        return

    os.makedirs(plots_dir, exist_ok=True)

    plt.rcParams.update({
        'figure.facecolor': '#1a1a2e', 'axes.facecolor': '#16213e',
        'axes.edgecolor': '#e94560', 'axes.labelcolor': '#eee',
        'text.color': '#eee', 'xtick.color': '#aaa', 'ytick.color': '#aaa',
        'grid.color': '#333', 'grid.alpha': 0.5, 'lines.linewidth': 2,
    })

    best_so_far = []
    best = -1e9
    for s in scores:
        best = max(best, s)
        best_so_far.append(best)

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(steps, scores, 'o', color='#0f3460', markersize=4, alpha=0.5, label='Run score')
    ax.plot(steps, best_so_far, '-', color='#e94560', linewidth=2, label='Best so far')
    ax.set_xlabel('Iteration')
    ax.set_ylabel('Score')
    ax.set_title('Optimization Progress')
    ax.legend()
    ax.grid(True)
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "progress.png"), dpi=150)
    plt.close()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(best_params: Dict, measurements: Dict, score: float,
                 details: Dict, specs: Dict, elapsed: float):
    print(f"\n{'='*70}")
    print(f"  VALIDATION REPORT -- {specs.get('name', 'SAR ADC')}")
    print(f"{'='*70}")
    print(f"\n  Score: {score:.2f} / 1.00  |  Time: {elapsed:.1f}s")

    specs_met = sum(1 for d in details.values() if d.get("met"))
    specs_total = len(details)
    print(f"\n  Specs met: {specs_met}/{specs_total}")

    print(f"\n  {'Spec':<25} {'Target':>12} {'Measured':>12} {'Unit':>8} {'Status':>8} {'Score':>6}")
    print(f"  {'-'*73}")

    for spec_name, d in details.items():
        measured = d["measured"]
        if measured is None:
            m_str = "N/A"
        elif abs(measured) > 1e6:
            m_str = f"{measured:.2e}"
        elif abs(measured) < 0.01:
            m_str = f"{measured:.2e}"
        else:
            m_str = f"{measured:.3f}"

        status = "PASS" if d["met"] else "FAIL"
        print(f"  {spec_name:<25} {d['target']:>12} {m_str:>12} {d['unit']:>8} {status:>8} {d['score']:>5.2f}")

    print(f"\n  Best Parameters:")
    for name, val in sorted(best_params.items()):
        print(f"    {name:<20} = {val:.4e}")
    print(f"\n{'='*70}\n")

    return specs_met, specs_total


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

def save_results(best_params: Dict, measurements: Dict, score: float,
                 details: Dict):
    """Save best_parameters.csv and measurements.json."""
    os.makedirs(PLOTS_DIR, exist_ok=True)

    with open("best_parameters.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "value"])
        for name, val in sorted(best_params.items()):
            w.writerow([name, val])

    with open("measurements.json", "w") as f:
        json.dump({
            "measurements": measurements,
            "score": score,
            "details": details,
            "parameters": best_params,
        }, f, indent=2, default=str)

    print(f"Saved: best_parameters.csv, measurements.json")


# ---------------------------------------------------------------------------
# Main -- standalone validation of existing parameters
# ---------------------------------------------------------------------------

def main():
    """Validate an existing best_parameters.csv.

    This does NOT run optimization. The agent implements its own optimizer
    and calls evaluate_params() etc.
    This main() is just for standalone validation.
    """
    parser = argparse.ArgumentParser(
        description="Validate SAR ADC parameters (no optimization)")
    parser.add_argument("--quick", action="store_true",
                        help="Quick validation (fewer sweep points)")
    parser.add_argument("--params-file", type=str, default="best_parameters.csv",
                        help="CSV file with parameter values (name,value)")
    args = parser.parse_args()

    print("Loading design...")
    template = load_design()
    params = load_parameters()
    specs = load_specs()

    errors = validate_design(template, params)
    if errors:
        print("\nVALIDATION ERRORS:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    # Load parameters to validate
    if not os.path.exists(args.params_file):
        print(f"\nNo {args.params_file} found. Run your optimizer first.")
        print("\nAvailable utilities for your optimizer:")
        print("  from evaluate import evaluate_params")
        print("  cost, meas = evaluate_params(template, param_dict)")
        sys.exit(1)

    best_params = {}
    with open(args.params_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            best_params[row["name"]] = float(row["value"])

    print(f"Design: {specs.get('name', 'Unknown')}")
    print(f"Parameters: {len(params)} (loaded {len(best_params)} values)")
    print()

    t0 = time.time()

    # Run simulation
    tmp_dir = tempfile.mkdtemp(prefix="adc_validate_")
    result = run_simulation(template, best_params, 0, tmp_dir,
                            NOMINAL_CORNER, NOMINAL_TEMP, NOMINAL_SUPPLY)

    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    if result.get("error"):
        print(f"Simulation failed: {result['error']}")
        if result.get("output_tail"):
            print(f"Output tail: {result['output_tail']}")
        sys.exit(1)

    measurements = result["measurements"]
    codes = result["codes"]
    elapsed = time.time() - t0

    score, details = score_measurements(measurements, specs)

    print_report(best_params, measurements, score, details, specs, elapsed)
    generate_plots(codes, measurements, NOMINAL_SUPPLY)
    generate_progress_plot(RESULTS_FILE, PLOTS_DIR)
    save_results(best_params, measurements, score, details)

    print(f"Score: {score:.2f}")
    return score


if __name__ == "__main__":
    score = main()
    sys.exit(0 if score >= 0.9 else 1)
