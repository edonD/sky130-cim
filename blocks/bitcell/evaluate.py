"""
evaluate.py — Simulation and validation utilities for CIM SRAM bitcell design.

Provides:
- NGSpice simulation runner (single sim at any PVT corner)
- Read current, leakage, SNM, and timing measurements
- PVT corner sweep
- Monte Carlo analysis (200 samples, mean +/- 4.5 sigma)
- Cost function, scoring, and plotting

This file does NOT contain an optimizer. The agent chooses and implements
its own optimization strategy (Bayesian Opt, PSO, CMA-ES, etc.).

Usage as utility library:
    from evaluate import (load_parameters, load_design, load_specs,
                          run_simulation, compute_cost,
                          run_pvt_sweep, run_monte_carlo, ...)

Usage standalone (validate existing best_parameters.csv):
    python evaluate.py                   # full validation
    python evaluate.py --quick           # quick validation (fewer corners)
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

# PVT corners
TEMPERATURES = [-40, 24, 175]
SUPPLY_VOLTAGES = [1.62, 1.8, 1.98]
PROCESS_CORNERS = ["tt", "ss", "ff", "sf", "fs"]

# Monte Carlo settings
MC_N_SAMPLES = 200
MC_SIGMA_TARGET = 4.5

# Nominal corner
NOMINAL_CORNER = "tt"
NOMINAL_TEMP = 24
NOMINAL_SUPPLY = 1.8

# ---------------------------------------------------------------------------
# Parameter loading
# ---------------------------------------------------------------------------

def load_parameters(path: str = PARAMS_FILE) -> List[Dict]:
    """Load parameter definitions from CSV."""
    params = []
    filepath = os.path.join(PROJECT_DIR, path) if not os.path.isabs(path) else path
    with open(filepath) as f:
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
    """Load the parametric SPICE netlist template."""
    filepath = os.path.join(PROJECT_DIR, path) if not os.path.isabs(path) else path
    with open(filepath) as f:
        return f.read()


def load_specs(path: str = SPECS_FILE) -> Dict:
    """Load target specifications."""
    filepath = os.path.join(PROJECT_DIR, path) if not os.path.isabs(path) else path
    with open(filepath) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_design(template: str, params: List[Dict]) -> List[str]:
    """Check that all parameter placeholders in design.cir match parameters.csv."""
    errors = []
    circuit_lines = []
    in_control = False
    for line in template.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith(".control"):
            in_control = True
        if not in_control and not stripped.startswith("*"):
            circuit_lines.append(line)
        if stripped.lower().startswith(".endc"):
            in_control = False
    circuit_text = "\n".join(circuit_lines)
    placeholders = set(re.findall(r'\{(\w+)\}', circuit_text))
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
    """Run a single bitcell simulation at a given PVT corner.

    Returns dict with keys: idx, error, measurements.
    """
    try:
        netlist = format_netlist(template, param_values,
                                 corner=corner, temperature=temperature,
                                 supply_v=supply_v)
    except Exception as e:
        return {"idx": idx, "error": f"format error: {e}", "measurements": {}}

    path = os.path.join(tmp_dir, f"sim_{idx}_{corner}_T{temperature}_V{supply_v}.cir")
    with open(path, "w") as f:
        f.write(netlist)

    try:
        result = subprocess.run(
            [NGSPICE, "-b", path],
            capture_output=True, text=True, timeout=120,
            cwd=PROJECT_DIR
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return {"idx": idx, "error": "timeout", "measurements": {}}
    except Exception as e:
        return {"idx": idx, "error": str(e), "measurements": {}}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if "RESULT_DONE" not in output:
        return {"idx": idx, "error": "no_RESULT_DONE", "measurements": {},
                "output_tail": output[-500:]}

    measurements = parse_ngspice_output(output)
    measurements = compute_derived_metrics(measurements, supply_v)
    measurements["corner"] = corner
    measurements["temperature"] = temperature
    measurements["supply_v"] = supply_v
    return {"idx": idx, "error": None, "measurements": measurements}


def parse_ngspice_output(output: str) -> Dict[str, float]:
    """Parse RESULT_xxx lines and measurement outputs from ngspice."""
    m = {}
    for line in output.split("\n"):
        if "RESULT_" in line and "RESULT_DONE" not in line:
            match = re.search(r'(RESULT_\w+)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)', line)
            if match:
                m[match.group(1)] = float(match.group(2))

        stripped = line.strip()
        if "=" in stripped and not stripped.startswith((".", "*", "+")):
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                name = parts[0].strip()
                val_match = re.search(r'([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)', parts[1])
                if val_match and name and len(name) < 40 and not name.startswith("("):
                    try:
                        m[name] = float(val_match.group(1))
                    except ValueError:
                        pass
    return m


def compute_derived_metrics(measurements: Dict[str, float],
                            supply_v: float = 1.8) -> Dict[str, float]:
    """Compute bitcell metrics from raw ngspice measurements."""
    # Read current (convert to uA, take absolute value)
    i_read_raw = measurements.get("RESULT_I_READ", 0)
    i_read_ua = abs(i_read_raw) * 1e6  # A -> uA
    measurements["RESULT_I_READ_UA"] = i_read_ua

    # Storage node health check
    q_val = measurements.get("RESULT_Q_VAL", 0)
    qb_val = measurements.get("RESULT_QB_VAL", supply_v)
    measurements["RESULT_STORAGE_OK"] = 1 if (q_val > 0.8 * supply_v and qb_val < 0.2 * supply_v) else 0

    # Read disturb check
    q_read = measurements.get("RESULT_Q_READ", 0)
    qb_read = measurements.get("RESULT_QB_READ", supply_v)
    measurements["RESULT_READ_DISTURB_OK"] = 1 if (q_read > 0.7 * supply_v and qb_read < 0.3 * supply_v) else 0

    # Read timing
    t_wl = measurements.get("RESULT_T_WL_RISE", 0)
    t_i90 = measurements.get("RESULT_T_I90", 0)
    if t_wl > 0 and t_i90 > t_wl:
        t_read_ns = (t_i90 - t_wl) * 1e9
    else:
        t_read_ns = 999.0
    t_read_ns = max(0.01, min(999.0, t_read_ns))
    measurements["RESULT_T_READ_NS"] = t_read_ns

    return measurements


def run_leakage_simulation(template: str, param_values: Dict[str, float],
                           tmp_dir: str, corner: str = "tt",
                           temperature: int = 24, supply_v: float = 1.8) -> float:
    """Run a simulation with Q=0 (weight=0) to measure leakage current.

    Modifies the netlist to write a 0 instead of a 1, then measures BL current.
    Returns leakage in nA.
    """
    # Swap BLW and BLBW to write a 0 (Q=0, QB=1)
    modified_template = template.replace(
        "Vblw blw 0 PWL(0 {Vsupply} 5n {Vsupply} 5.1n 0)",
        "Vblw blw 0 PWL(0 0 5n 0 5.1n 0)"
    ).replace(
        "Vblbw blbw 0 PWL(0 0 5n 0 5.1n 0)",
        "Vblbw blbw 0 PWL(0 {Vsupply} 5n {Vsupply} 5.1n 0)"
    )

    try:
        netlist = format_netlist(modified_template, param_values,
                                 corner=corner, temperature=temperature,
                                 supply_v=supply_v)
    except Exception:
        return 1e6  # Very high leakage as penalty

    path = os.path.join(tmp_dir, f"leak_{corner}_T{temperature}_V{supply_v}.cir")
    with open(path, "w") as f:
        f.write(netlist)

    try:
        result = subprocess.run(
            [NGSPICE, "-b", path],
            capture_output=True, text=True, timeout=120,
            cwd=PROJECT_DIR
        )
        output = result.stdout + result.stderr
    except Exception:
        return 1e6
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if "RESULT_DONE" not in output:
        return 1e6

    measurements = parse_ngspice_output(output)
    i_leak_raw = measurements.get("RESULT_I_READ", 0)  # Same measurement point
    i_leak_na = abs(i_leak_raw) * 1e9  # A -> nA
    return i_leak_na


# ---------------------------------------------------------------------------
# Cost function — usable by any optimizer
# ---------------------------------------------------------------------------

def compute_cost(measurements: Dict[str, float], specs: Dict = None,
                 i_leak_na: float = None) -> float:
    """Cost function for optimization — evaluates at nominal corner only for speed.

    Returns a scalar cost (lower is better). Any optimizer can call this.
    """
    if not measurements:
        return 1e6

    cost = 0.0

    # Read current: want > 5 uA
    i_read_ua = measurements.get("RESULT_I_READ_UA", 0)
    if i_read_ua >= 5.0:
        cost -= (i_read_ua - 5.0) / 5.0 * 30  # reward exceeding spec
    else:
        cost += ((5.0 - i_read_ua) / 5.0) ** 2 * 300  # heavy penalty

    # Leakage: want < 100 nA
    if i_leak_na is not None:
        if i_leak_na <= 100.0:
            cost -= (100.0 - i_leak_na) / 100.0 * 25
        else:
            cost += ((i_leak_na - 100.0) / 100.0) ** 2 * 250

        # ON/OFF ratio: want > 100
        if i_leak_na > 0:
            on_off = (i_read_ua * 1000) / i_leak_na  # both effectively in nA
            if on_off >= 100:
                cost -= (on_off - 100) / 100 * 20
            else:
                cost += ((100 - on_off) / 100) ** 2 * 200

    # Storage node health — heavy penalty if cell not functional
    if measurements.get("RESULT_STORAGE_OK", 0) == 0:
        cost += 1000

    # Read disturb — penalty if storage nodes flip during read
    if measurements.get("RESULT_READ_DISTURB_OK", 0) == 0:
        cost += 500

    # Read timing: want < 5 ns
    t_read_ns = measurements.get("RESULT_T_READ_NS", 999.0)
    if t_read_ns <= 5.0:
        cost -= (5.0 - t_read_ns) / 5.0 * 10
    else:
        cost += ((t_read_ns - 5.0) / 5.0) ** 2 * 100

    return cost


def evaluate_params(template: str, param_values: Dict[str, float],
                    specs: Dict = None) -> Tuple[float, Dict]:
    """Convenience: simulate at nominal corner and return (cost, measurements).

    Useful as the objective function for any optimizer.
    """
    tmp_dir = tempfile.mkdtemp(prefix="bitcell_eval_")

    # Run read current simulation (Q=1)
    result = run_simulation(template, param_values, 0, tmp_dir,
                            NOMINAL_CORNER, NOMINAL_TEMP, NOMINAL_SUPPLY)

    # Run leakage simulation (Q=0)
    i_leak_na = run_leakage_simulation(template, param_values, tmp_dir,
                                        NOMINAL_CORNER, NOMINAL_TEMP, NOMINAL_SUPPLY)

    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    if result.get("error") or not result.get("measurements"):
        return 1e6, {}

    measurements = result["measurements"]
    measurements["RESULT_I_LEAK_NA"] = i_leak_na

    # Compute ON/OFF ratio
    i_read_ua = measurements.get("RESULT_I_READ_UA", 0)
    if i_leak_na > 0:
        measurements["RESULT_ON_OFF_RATIO"] = (i_read_ua * 1000) / i_leak_na
    else:
        measurements["RESULT_ON_OFF_RATIO"] = 1e6  # infinite ratio (suspicious)

    cost = compute_cost(measurements, specs, i_leak_na)
    return cost, measurements


# ---------------------------------------------------------------------------
# PVT Corner Sweep
# ---------------------------------------------------------------------------

def run_pvt_sweep(template: str, param_values: Dict[str, float],
                  tmp_dir: str = None, quick: bool = False) -> Dict:
    """Run bitcell across all PVT corners. Returns worst-case metrics."""
    own_tmp = tmp_dir is None
    if own_tmp:
        tmp_dir = tempfile.mkdtemp(prefix="bitcell_pvt_")

    corners = PROCESS_CORNERS if not quick else ["tt", "ss"]
    temps = TEMPERATURES if not quick else [24, 175]
    supplies = SUPPLY_VOLTAGES if not quick else [1.62, 1.8]

    results = []
    worst_i_read = 1e6
    worst_i_leak = 0
    worst_on_off = 1e6
    worst_t_read = 0

    print("\n--- PVT Corner Sweep ---")
    for corner in corners:
        for temp in temps:
            for supply in supplies:
                sim = run_simulation(template, param_values, 0, tmp_dir,
                                     corner=corner, temperature=temp, supply_v=supply)

                i_leak_na = run_leakage_simulation(template, param_values, tmp_dir,
                                                    corner=corner, temperature=temp,
                                                    supply_v=supply)

                i_read_ua = 0
                t_read_ns = 999.0
                storage_ok = False

                if sim.get("measurements"):
                    m = sim["measurements"]
                    i_read_ua = m.get("RESULT_I_READ_UA", 0)
                    t_read_ns = m.get("RESULT_T_READ_NS", 999.0)
                    storage_ok = m.get("RESULT_STORAGE_OK", 0) == 1

                on_off = (i_read_ua * 1000 / i_leak_na) if i_leak_na > 0 else 0

                results.append({
                    "corner": corner, "temp": temp, "supply": supply,
                    "i_read_ua": i_read_ua, "i_leak_na": i_leak_na,
                    "on_off_ratio": on_off, "t_read_ns": t_read_ns,
                    "storage_ok": storage_ok
                })

                worst_i_read = min(worst_i_read, i_read_ua)
                worst_i_leak = max(worst_i_leak, i_leak_na)
                worst_on_off = min(worst_on_off, on_off)
                worst_t_read = max(worst_t_read, t_read_ns)

                status = "PASS" if (i_read_ua >= 5 and i_leak_na <= 100
                                    and on_off >= 100 and t_read_ns <= 5
                                    and storage_ok) else "FAIL"
                print(f"  {corner:>2s} T={temp:>4d}C V={supply:.2f}V: "
                      f"Iread={i_read_ua:>7.2f}uA  Ileak={i_leak_na:>7.1f}nA  "
                      f"ratio={on_off:>7.1f}  tread={t_read_ns:>6.2f}ns  [{status}]")

    if own_tmp:
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    all_pass = all(
        r["i_read_ua"] >= 5 and r["i_leak_na"] <= 100
        and r["on_off_ratio"] >= 100 and r["t_read_ns"] <= 5
        and r["storage_ok"]
        for r in results
    )

    print(f"\n  Worst-case: Iread={worst_i_read:.2f}uA, Ileak={worst_i_leak:.1f}nA, "
          f"ratio={worst_on_off:.1f}, tread={worst_t_read:.2f}ns")
    print(f"  PVT sweep: {'ALL PASS' if all_pass else 'SOME FAIL'}")
    print("--- PVT Sweep Done ---\n")

    return {
        "results": results,
        "worst_i_read_ua": worst_i_read,
        "worst_i_leak_na": worst_i_leak,
        "worst_on_off_ratio": worst_on_off,
        "worst_t_read_ns": worst_t_read,
        "all_pass": all_pass,
    }


# ---------------------------------------------------------------------------
# Monte Carlo Analysis
# ---------------------------------------------------------------------------

def run_monte_carlo(template: str, param_values: Dict[str, float],
                    tmp_dir: str = None, n_samples: int = MC_N_SAMPLES,
                    quick: bool = False) -> Dict:
    """Run Monte Carlo analysis with Vth mismatch.

    Models mismatch by randomly perturbing transistor widths by small amounts
    (representing threshold voltage mismatch effects on current).
    In SKY130, Avt ~ 5 mV*um for nfet_01v8.
    """
    own_tmp = tmp_dir is None
    if own_tmp:
        tmp_dir = tempfile.mkdtemp(prefix="bitcell_mc_")

    if quick:
        n_samples = 30

    # Mismatch model: perturb Wn slightly between left/right inverters
    Wn = param_values.get("Wn", 1.0)
    Ln = param_values.get("Ln", 0.15)
    Avt = 5.0e-3  # V*um
    sigma_vth = Avt / np.sqrt(Wn * Ln)

    print(f"\n--- Monte Carlo Analysis ({n_samples} samples) ---")
    print(f"  NMOS driver: W={Wn:.2f}u, L={Ln:.2f}u")
    print(f"  Vth mismatch sigma: {sigma_vth*1e3:.3f} mV")

    rng = np.random.RandomState(42)

    i_read_samples = []
    i_leak_samples = []

    for i in range(n_samples):
        # Perturb parameters slightly to model mismatch
        perturbed = dict(param_values)
        for pname in ["Wn", "Wp", "Wrd"]:
            if pname in perturbed:
                # +/- 5% random variation to model mismatch
                perturbed[pname] = perturbed[pname] * (1 + rng.normal(0, 0.02))

        sim = run_simulation(template, perturbed, i, tmp_dir,
                             NOMINAL_CORNER, NOMINAL_TEMP, NOMINAL_SUPPLY)

        i_leak = run_leakage_simulation(template, perturbed, tmp_dir,
                                         NOMINAL_CORNER, NOMINAL_TEMP, NOMINAL_SUPPLY)

        if sim.get("error") or not sim.get("measurements"):
            i_read_samples.append(0)
            i_leak_samples.append(1e6)
            continue

        i_read_ua = sim["measurements"].get("RESULT_I_READ_UA", 0)
        i_read_samples.append(i_read_ua)
        i_leak_samples.append(i_leak)

        if (i + 1) % 50 == 0:
            print(f"  Completed {i+1}/{n_samples} samples...")

    if own_tmp:
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    i_read_arr = np.array(i_read_samples)
    i_leak_arr = np.array(i_leak_samples)

    i_read_mean = np.mean(i_read_arr)
    i_read_std = np.std(i_read_arr)
    i_read_worst = i_read_mean - MC_SIGMA_TARGET * i_read_std  # worst = lowest

    i_leak_mean = np.mean(i_leak_arr)
    i_leak_std = np.std(i_leak_arr)
    i_leak_worst = i_leak_mean + MC_SIGMA_TARGET * i_leak_std  # worst = highest

    on_off_worst = (i_read_worst * 1000 / i_leak_worst) if i_leak_worst > 0 else 0

    print(f"\n  I_read: mean={i_read_mean:.3f}uA, std={i_read_std:.3f}uA, "
          f"mean-{MC_SIGMA_TARGET}sigma={i_read_worst:.3f}uA")
    print(f"  I_leak: mean={i_leak_mean:.3f}nA, std={i_leak_std:.3f}nA, "
          f"mean+{MC_SIGMA_TARGET}sigma={i_leak_worst:.3f}nA")
    print(f"  ON/OFF at worst: {on_off_worst:.1f}")

    i_read_pass = i_read_worst >= 5.0
    i_leak_pass = i_leak_worst <= 100.0
    on_off_pass = on_off_worst >= 100.0

    print(f"  I_read at {MC_SIGMA_TARGET}sigma: {'PASS' if i_read_pass else 'FAIL'}")
    print(f"  I_leak at {MC_SIGMA_TARGET}sigma: {'PASS' if i_leak_pass else 'FAIL'}")
    print(f"  ON/OFF at {MC_SIGMA_TARGET}sigma: {'PASS' if on_off_pass else 'FAIL'}")
    print("--- Monte Carlo Done ---\n")

    return {
        "n_samples": n_samples,
        "i_read_mean_ua": i_read_mean,
        "i_read_std_ua": i_read_std,
        "i_read_worst_ua": i_read_worst,
        "i_leak_mean_na": i_leak_mean,
        "i_leak_std_na": i_leak_std,
        "i_leak_worst_na": i_leak_worst,
        "on_off_worst": on_off_worst,
        "i_read_pass": i_read_pass,
        "i_leak_pass": i_leak_pass,
        "on_off_pass": on_off_pass,
        "all_pass": i_read_pass and i_leak_pass and on_off_pass,
        "sigma_vth_mv": sigma_vth * 1e3,
    }


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
    """Score measured values against specs. Returns (overall_score, details)."""
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

def generate_plots(pvt_results: Dict, mc_results: Dict, measurements: Dict):
    """Generate validation plots."""
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

    # --- PVT Corner Plot ---
    if pvt_results and pvt_results.get("results"):
        fig, axes = plt.subplots(2, 2, figsize=(16, 10))

        pvt = pvt_results["results"]
        labels = [f"{r['corner']}\nT={r['temp']}\nV={r['supply']}" for r in pvt]

        # I_read
        i_reads = [r["i_read_ua"] for r in pvt]
        colors = ['#0f0' if v >= 5 else '#e94560' for v in i_reads]
        axes[0, 0].bar(range(len(i_reads)), i_reads, color=colors, alpha=0.8)
        axes[0, 0].axhline(y=5.0, color='yellow', linestyle='--', label='Spec: 5 uA')
        axes[0, 0].set_ylabel('I_read (uA)')
        axes[0, 0].set_title('Read Current across PVT')
        axes[0, 0].legend(fontsize=8)
        axes[0, 0].grid(True)

        # I_leak
        i_leaks = [r["i_leak_na"] for r in pvt]
        colors = ['#0f0' if v <= 100 else '#e94560' for v in i_leaks]
        axes[0, 1].bar(range(len(i_leaks)), i_leaks, color=colors, alpha=0.8)
        axes[0, 1].axhline(y=100.0, color='yellow', linestyle='--', label='Spec: 100 nA')
        axes[0, 1].set_ylabel('I_leak (nA)')
        axes[0, 1].set_title('Leakage Current across PVT')
        axes[0, 1].legend(fontsize=8)
        axes[0, 1].grid(True)

        # ON/OFF ratio
        ratios = [r["on_off_ratio"] for r in pvt]
        colors = ['#0f0' if v >= 100 else '#e94560' for v in ratios]
        axes[1, 0].bar(range(len(ratios)), ratios, color=colors, alpha=0.8)
        axes[1, 0].axhline(y=100.0, color='yellow', linestyle='--', label='Spec: 100')
        axes[1, 0].set_ylabel('ON/OFF Ratio')
        axes[1, 0].set_title('ON/OFF Ratio across PVT')
        axes[1, 0].legend(fontsize=8)
        axes[1, 0].grid(True)

        # T_read
        t_reads = [r["t_read_ns"] for r in pvt]
        colors = ['#0f0' if v <= 5 else '#e94560' for v in t_reads]
        axes[1, 1].bar(range(len(t_reads)), t_reads, color=colors, alpha=0.8)
        axes[1, 1].axhline(y=5.0, color='yellow', linestyle='--', label='Spec: 5 ns')
        axes[1, 1].set_ylabel('T_read (ns)')
        axes[1, 1].set_title('Read Time across PVT')
        axes[1, 1].legend(fontsize=8)
        axes[1, 1].grid(True)

        for ax in axes.flat:
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, fontsize=4, rotation=45)

        plt.tight_layout()
        plt.savefig(os.path.join(PLOTS_DIR, 'pvt_corners.png'), dpi=150)
        plt.close()


def generate_progress_plot(results_file: str, plots_dir: str):
    """Generate progress.png from results.tsv."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return

    filepath = os.path.join(PROJECT_DIR, results_file)
    if not os.path.exists(filepath):
        return

    steps, scores = [], []
    with open(filepath) as f:
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
                 details: Dict, specs: Dict,
                 pvt_results: Dict, mc_results: Dict, elapsed: float):
    print(f"\n{'='*70}")
    print(f"  VALIDATION REPORT -- {specs.get('name', 'CIM Bitcell')}")
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

    if pvt_results:
        print(f"\n  PVT Worst-case:")
        print(f"    I_read: {pvt_results['worst_i_read_ua']:.2f} uA  "
              f"{'PASS' if pvt_results['worst_i_read_ua'] >= 5.0 else 'FAIL'}")
        print(f"    I_leak: {pvt_results['worst_i_leak_na']:.1f} nA  "
              f"{'PASS' if pvt_results['worst_i_leak_na'] <= 100.0 else 'FAIL'}")
        print(f"    ON/OFF: {pvt_results['worst_on_off_ratio']:.1f}  "
              f"{'PASS' if pvt_results['worst_on_off_ratio'] >= 100.0 else 'FAIL'}")
        print(f"    T_read: {pvt_results['worst_t_read_ns']:.2f} ns  "
              f"{'PASS' if pvt_results['worst_t_read_ns'] <= 5.0 else 'FAIL'}")

    if mc_results:
        print(f"\n  Monte Carlo (mean +/- {MC_SIGMA_TARGET} sigma):")
        print(f"    I_read: {mc_results['i_read_mean_ua']:.3f} +/- "
              f"{mc_results['i_read_std_ua']:.3f} uA -> "
              f"worst={mc_results['i_read_worst_ua']:.3f} uA  "
              f"{'PASS' if mc_results['i_read_pass'] else 'FAIL'}")
        print(f"    I_leak: {mc_results['i_leak_mean_na']:.3f} +/- "
              f"{mc_results['i_leak_std_na']:.3f} nA -> "
              f"worst={mc_results['i_leak_worst_na']:.3f} nA  "
              f"{'PASS' if mc_results['i_leak_pass'] else 'FAIL'}")

    print(f"\n  Best Parameters:")
    for name, val in sorted(best_params.items()):
        print(f"    {name:<20} = {val:.4e}")
    print(f"\n{'='*70}\n")

    return specs_met, specs_total


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

def save_results(best_params: Dict, measurements: Dict, score: float,
                 details: Dict, pvt_results: Dict = None,
                 mc_results: Dict = None):
    """Save best_parameters.csv and measurements.json."""
    os.makedirs(PLOTS_DIR, exist_ok=True)

    with open(os.path.join(PROJECT_DIR, "best_parameters.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "value"])
        for name, val in sorted(best_params.items()):
            w.writerow([name, val])

    with open(os.path.join(PROJECT_DIR, "measurements.json"), "w") as f:
        json.dump({
            "measurements": measurements,
            "score": score,
            "details": details,
            "parameters": best_params,
            "pvt": {
                "worst_i_read_ua": pvt_results["worst_i_read_ua"] if pvt_results else None,
                "worst_i_leak_na": pvt_results["worst_i_leak_na"] if pvt_results else None,
                "worst_on_off_ratio": pvt_results["worst_on_off_ratio"] if pvt_results else None,
                "worst_t_read_ns": pvt_results["worst_t_read_ns"] if pvt_results else None,
                "all_pass": pvt_results["all_pass"] if pvt_results else None,
            } if pvt_results else None,
            "monte_carlo": {
                "i_read_mean_ua": mc_results["i_read_mean_ua"] if mc_results else None,
                "i_read_worst_ua": mc_results["i_read_worst_ua"] if mc_results else None,
                "i_leak_mean_na": mc_results["i_leak_mean_na"] if mc_results else None,
                "i_leak_worst_na": mc_results["i_leak_worst_na"] if mc_results else None,
                "on_off_worst": mc_results["on_off_worst"] if mc_results else None,
                "all_pass": mc_results["all_pass"] if mc_results else None,
            } if mc_results else None,
        }, f, indent=2, default=str)

    print(f"Saved: best_parameters.csv, measurements.json")


# ---------------------------------------------------------------------------
# Main — standalone validation of existing parameters
# ---------------------------------------------------------------------------

def main():
    """Validate an existing best_parameters.csv against all PVT corners + MC.

    This does NOT run optimization. The agent implements its own optimizer
    and calls evaluate_params(), run_pvt_sweep(), run_monte_carlo() etc.
    This main() is just for standalone validation.
    """
    parser = argparse.ArgumentParser(
        description="Validate bitcell parameters (no optimization -- use your own optimizer)")
    parser.add_argument("--quick", action="store_true", help="Quick validation (fewer corners)")
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
    params_path = os.path.join(PROJECT_DIR, args.params_file)
    if not os.path.exists(params_path):
        print(f"\nNo {args.params_file} found. Run your optimizer first to generate parameters.")
        print("\nAvailable utilities for your optimizer:")
        print("  from evaluate import evaluate_params, run_pvt_sweep, run_monte_carlo")
        print("  cost, meas = evaluate_params(template, param_dict)")
        sys.exit(1)

    best_params = {}
    with open(params_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            best_params[row["name"]] = float(row["value"])

    print(f"Design: {specs.get('name', 'Unknown')}")
    print(f"Parameters: {len(params)} (loaded {len(best_params)} values)")
    print()

    t0 = time.time()

    # Nominal simulation
    tmp_dir = tempfile.mkdtemp(prefix="bitcell_validate_")

    cost, measurements = evaluate_params(template, best_params, specs)

    # PVT Corner Sweep
    pvt_results = run_pvt_sweep(template, best_params, tmp_dir, quick=args.quick)

    # Monte Carlo Analysis
    mc_results = run_monte_carlo(template, best_params, tmp_dir, quick=args.quick)

    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    # Update measurements with worst-case values
    if pvt_results:
        measurements["RESULT_I_READ_UA"] = pvt_results["worst_i_read_ua"]
        measurements["RESULT_I_LEAK_NA"] = pvt_results["worst_i_leak_na"]
        measurements["RESULT_ON_OFF_RATIO"] = pvt_results["worst_on_off_ratio"]
        measurements["RESULT_T_READ_NS"] = pvt_results["worst_t_read_ns"]

    elapsed = time.time() - t0
    score, details = score_measurements(measurements, specs)

    print_report(best_params, measurements, score, details, specs,
                 pvt_results, mc_results, elapsed)

    generate_plots(pvt_results, mc_results, measurements)
    generate_progress_plot(RESULTS_FILE, PLOTS_DIR)
    save_results(best_params, measurements, score, details, pvt_results, mc_results)

    pvt_ok = pvt_results and pvt_results["all_pass"]
    mc_ok = mc_results and mc_results["all_pass"]
    print(f"Score: {score:.2f} | PVT: {'PASS' if pvt_ok else 'FAIL'} | "
          f"MC: {'PASS' if mc_ok else 'FAIL'}")

    return score


if __name__ == "__main__":
    score = main()
    sys.exit(0 if score >= 0.9 else 1)
