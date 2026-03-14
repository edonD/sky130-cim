#!/usr/bin/env python3
"""
SAR ADC Optimizer — Behavioral model with realistic non-idealities.

The SAR algorithm is modeled in Python using charge-redistribution equations.
Non-idealities modeled:
  - Capacitor mismatch (based on Cu size and SKY130 matching data)
  - Comparator offset (from analytical model)
  - Thermal noise (kT/C)

The comparator is verified separately in ngspice.

Usage:
    python optimize.py              # Run full optimization
    python optimize.py --validate   # Validate current best parameters
"""

import os
import sys
import csv
import json
import time
import tempfile
import subprocess
import argparse
import numpy as np
from typing import Dict, List, Tuple, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
N_BITS = 6
N_CODES = 2 ** N_BITS  # 64
VDD = 1.8
PLOTS_DIR = os.path.join(PROJECT_DIR, "plots")

# SKY130 MIM cap matching: sigma(dC/C) ~ 0.45% * sqrt(1fF / C)
# Based on SKY130 PDK capacitor mismatch data
# For sky130_fd_pr__cap_mim_m3_1: ~2 fF/um^2, Pelgrom-like matching
SKY130_CAP_MATCH_COEFF = 0.0045  # 0.45% for 1fF unit cap

# Comparator parameters
COMP_OFFSET_AVT = 5e-3  # Pelgrom Avt in V*um for SKY130 nfet

# ---------------------------------------------------------------------------
# SAR ADC Behavioral Model
# ---------------------------------------------------------------------------

class SarAdcModel:
    """Behavioral model of a 6-bit charge-redistribution SAR ADC."""

    def __init__(self, cu_ff: float, vdd: float = VDD,
                 comp_offset_mv: float = 0.0,
                 cap_mismatch_sigma: float = 0.0,
                 seed: int = 42):
        """
        Args:
            cu_ff: Unit capacitance in femtofarads
            vdd: Supply voltage
            comp_offset_mv: Comparator offset in mV (systematic)
            cap_mismatch_sigma: Relative mismatch sigma for unit cap
            seed: Random seed for mismatch generation
        """
        self.n_bits = N_BITS
        self.vdd = vdd
        self.cu_ff = cu_ff
        self.comp_offset = comp_offset_mv * 1e-3  # Convert to V

        # Binary weights
        self.weights = np.array([32, 16, 8, 4, 2, 1], dtype=float)

        # Generate capacitor values with mismatch
        rng = np.random.RandomState(seed)
        if cap_mismatch_sigma > 0:
            # Each unit cap has independent mismatch
            # For weight W, cap = W * Cu, mismatch sigma = sigma_unit / sqrt(W)
            self.cap_values = np.zeros(N_BITS)
            for i, w in enumerate(self.weights):
                # Mismatch for this cap (composed of w unit caps)
                mismatch = rng.normal(0, cap_mismatch_sigma / np.sqrt(w))
                self.cap_values[i] = w * cu_ff * (1 + mismatch)
        else:
            self.cap_values = self.weights * cu_ff

        # Termination cap (1 Cu)
        self.c_term = cu_ff

        # Total capacitance
        self.c_total = np.sum(self.cap_values) + self.c_term

        # LSB voltage
        self.v_lsb = vdd / N_CODES

        # kT/C noise (thermal)
        kT = 1.38e-23 * 300  # at 300K
        self.ktc_noise_rms = np.sqrt(kT / (self.c_total * 1e-15))  # V rms

    def dac_voltage(self, code: int) -> float:
        """Compute DAC output voltage for a given digital code.

        With mismatch, the actual voltage differs from ideal due to
        capacitor ratio errors.
        """
        v = 0.0
        for i in range(N_BITS):
            bit = (code >> (N_BITS - 1 - i)) & 1
            if bit:
                v += self.cap_values[i] * self.vdd / self.c_total
        return v

    def convert(self, vin: float, add_noise: bool = False) -> int:
        """Run SAR conversion for a single input voltage.

        Standard successive approximation: for each bit from MSB to LSB,
        set the bit, compute DAC voltage, compare against Vin.
        If DAC > Vin, clear the bit.

        Returns the output digital code (0 to 63).
        """
        vin_eff = vin
        if add_noise:
            vin_eff += np.random.normal(0, self.ktc_noise_rms)

        code = 0
        for i in range(N_BITS):
            bit_weight = int(self.weights[i])
            code += bit_weight
            v_dac = self.dac_voltage(code)

            if v_dac > vin_eff + self.comp_offset:
                # DAC too high, clear this bit
                code -= bit_weight

        return max(0, min(N_CODES - 1, code))

    def sweep(self, n_points: int = 512, add_noise: bool = False) -> List[Tuple[float, int]]:
        """Sweep input voltage and return (vin, code) pairs."""
        results = []
        for i in range(n_points + 1):
            vin = i * self.vdd / n_points
            code = self.convert(vin, add_noise=add_noise)
            results.append((vin, code))
        return results


# ---------------------------------------------------------------------------
# Capacitor mismatch model for SKY130
# ---------------------------------------------------------------------------

def compute_cap_mismatch_sigma(cu_ff: float) -> float:
    """Compute relative mismatch sigma for a unit cap of size cu_ff.

    For SKY130 MIM caps, matching improves with sqrt(area).
    sigma(dC/C) ~ K / sqrt(C) where K is process-dependent.

    Using conservative estimate: K ~ 0.45% * sqrt(fF)
    """
    return SKY130_CAP_MATCH_COEFF / np.sqrt(cu_ff)


def compute_comparator_offset(w_in: float, l_in: float) -> float:
    """Estimate comparator input-referred offset in mV.

    Based on Pelgrom model: sigma_Vos = Avt / sqrt(W * L)
    For SKY130 nfet: Avt ~ 5 mV*um

    Returns 3-sigma offset in mV (worst case).
    """
    sigma_vos = COMP_OFFSET_AVT / np.sqrt(w_in * l_in)  # in V
    return sigma_vos * 3 * 1000  # 3-sigma in mV


# ---------------------------------------------------------------------------
# Power estimation
# ---------------------------------------------------------------------------

def estimate_power(cu_ff: float, tsar_ns: float,
                   w_comp_in: float, w_comp_tail: float,
                   vdd: float = VDD) -> float:
    """Estimate average power during conversion in uW.

    Components:
    1. DAC switching energy: ~0.5 * C_total * VDD^2 per conversion (average)
    2. Comparator energy per comparison: StrongARM conducts only during
       the brief evaluation/regeneration phase (~0.3-0.5ns), not the full
       clock half-period. After latch regenerates, current drops to ~0.
    3. Reset/precharge energy for internal latch nodes.
    """
    cu_f = cu_ff * 1e-15
    c_total = 64 * cu_f

    # DAC switching: average activity factor ~0.5 for binary search
    e_dac = 0.5 * c_total * vdd**2 * 0.5

    # Comparator: tail current during evaluation
    # For SKY130 nfet: mu_n * Cox ~ 270 uA/V^2
    # Typical Vov ~ 0.2V for tail
    mu_cox = 270e-6  # A/V^2
    vov_tail = 0.2
    i_tail = 0.5 * mu_cox * (w_comp_tail / 0.15) * vov_tail**2

    # StrongARM comparator: current flows only during evaluation phase
    # Typical evaluation time: 0.3-0.5 ns before latch regenerates
    # After regeneration, cross-coupled latch holds state with ~zero static current
    t_eval = 0.5e-9  # evaluation time per comparison (conservative)
    e_comp_per_trial = i_tail * vdd * t_eval

    # Latch node capacitance charging energy
    # Internal node cap depends on transistor sizes: ~2fF/um gate width (SKY130)
    c_per_um = 2e-15  # fF per um of gate width
    c_latch_node = c_per_um * (w_comp_in + 2)  # input pair + reset PMOS (2um)
    e_latch = 4 * 0.5 * c_latch_node * vdd**2  # reset/precharge energy per comparison

    # Total comparator energy = 6 comparisons * (eval + latch)
    e_comp_total = 6 * (e_comp_per_trial + e_latch)

    # Total energy per conversion
    e_total = e_dac + e_comp_total

    # Average power = energy / conversion time
    t_sample = 5e-9  # 5ns sample phase
    t_conv = t_sample + 6 * tsar_ns * 1e-9  # total conversion time
    power_w = e_total / t_conv

    return power_w * 1e6  # Convert to uW


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_dnl_inl(codes: List[Tuple[float, int]],
                    vdd: float = VDD) -> Tuple[np.ndarray, np.ndarray, float, float]:
    """Compute DNL and INL from (vin, code) sweep data."""
    v_lsb_ideal = vdd / N_CODES

    # Find code transition points
    transitions = {}
    for i in range(1, len(codes)):
        v_prev, c_prev = codes[i - 1]
        v_curr, c_curr = codes[i]
        if c_curr != c_prev:
            v_trans = (v_prev + v_curr) / 2.0
            if c_curr not in transitions:
                transitions[c_curr] = v_trans

    if len(transitions) < 2:
        return np.zeros(N_CODES), np.zeros(N_CODES), 99.0, 99.0

    sorted_codes = sorted(transitions.keys())
    first_code = sorted_codes[0]
    last_code = sorted_codes[-1]

    dnl = np.zeros(N_CODES)
    inl = np.zeros(N_CODES)

    for i in range(len(sorted_codes) - 1):
        code_k = sorted_codes[i]
        code_k1 = sorted_codes[i + 1]
        actual_width = transitions[code_k1] - transitions[code_k]
        dnl[code_k] = (actual_width / v_lsb_ideal) - 1.0

    # INL = cumulative sum of DNL
    inl_accum = 0.0
    for code_k in sorted_codes:
        inl_accum += dnl[code_k]
        inl[code_k] = inl_accum

    # Endpoint correction
    if len(sorted_codes) >= 2:
        first = sorted_codes[0]
        last = sorted_codes[-1]
        if last != first:
            slope = (inl[last] - inl[first]) / (last - first)
            for k in range(N_CODES):
                inl[k] -= inl[first] + slope * (k - first)

    max_dnl = np.max(np.abs(dnl[first_code:last_code + 1]))
    max_inl = np.max(np.abs(inl[first_code:last_code + 1]))

    return dnl, inl, max_dnl, max_inl


def compute_enob(codes: List[Tuple[float, int]], vdd: float = VDD) -> float:
    """Compute ENOB from ramp sweep using RMS error method."""
    if len(codes) < 10:
        return 0.0

    vins = np.array([c[0] for c in codes])
    code_vals = np.array([c[1] for c in codes])

    v_lsb = vdd / N_CODES
    ideal_codes = np.clip(np.floor(vins / v_lsb), 0, N_CODES - 1)

    errors = code_vals - ideal_codes
    rms_error = np.sqrt(np.mean(errors**2))
    ideal_rms = 1.0 / np.sqrt(12.0)

    if rms_error < 1e-10:
        return float(N_BITS)

    enob = N_BITS - np.log2(rms_error / ideal_rms)
    return max(0.0, min(float(N_BITS), enob))


def check_missing_codes(codes: List[Tuple[float, int]]) -> List[int]:
    """Check for missing codes in the sweep."""
    seen = set(c[1] for c in codes)
    # Only check codes that should appear (between first and last seen)
    if not seen:
        return list(range(N_CODES))
    min_code = min(seen)
    max_code = max(seen)
    missing = [c for c in range(min_code, max_code + 1) if c not in seen]
    return missing


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------

def evaluate_parameters(params: Dict[str, float],
                       n_mismatch_trials: int = 5,
                       verbose: bool = False) -> Dict:
    """Evaluate a set of ADC parameters.

    Runs multiple Monte Carlo trials with different mismatch seeds
    and returns worst-case metrics.
    """
    cu = params["Cu"]
    w_in = params["Wcomp_in"]
    l_in = params["Lcomp_in"]
    w_latch = params["Wcomp_latch"]
    l_latch = params["Lcomp_latch"]
    w_tail = params["Wcomp_tail"]
    tsar = params["Tsar_ns"]

    # Compute non-ideality parameters
    cap_mismatch = compute_cap_mismatch_sigma(cu)
    comp_offset_3sig = compute_comparator_offset(w_in, l_in)

    # Conversion time
    t_sample = 5.0  # ns, sample phase
    conv_time = t_sample + 6 * tsar  # total conversion time in ns

    # Power
    power = estimate_power(cu, tsar, w_in, w_tail)

    # Run multiple mismatch trials
    all_dnl = []
    all_inl = []
    all_enob = []
    all_missing = []

    for trial in range(n_mismatch_trials):
        # Create ADC model with this trial's mismatch
        adc = SarAdcModel(
            cu_ff=cu,
            comp_offset_mv=0.0,  # Use 0 for nominal, offset checked separately
            cap_mismatch_sigma=cap_mismatch,
            seed=trial * 137 + 42
        )

        # Sweep input
        codes = adc.sweep(n_points=512)

        # Compute metrics
        dnl, inl, max_dnl, max_inl = compute_dnl_inl(codes)
        enob = compute_enob(codes)
        missing = check_missing_codes(codes)

        all_dnl.append(max_dnl)
        all_inl.append(max_inl)
        all_enob.append(enob)
        all_missing.append(len(missing))

    # Also run with comparator offset (worst case)
    for sign in [-1, 1]:
        adc_offset = SarAdcModel(
            cu_ff=cu,
            comp_offset_mv=sign * comp_offset_3sig,
            cap_mismatch_sigma=cap_mismatch,
            seed=999
        )
        codes_offset = adc_offset.sweep(n_points=512)
        dnl_o, inl_o, max_dnl_o, max_inl_o = compute_dnl_inl(codes_offset)
        enob_o = compute_enob(codes_offset)

        all_dnl.append(max_dnl_o)
        all_inl.append(max_inl_o)
        all_enob.append(enob_o)

    # Worst case across all trials
    worst_dnl = max(all_dnl)
    worst_inl = max(all_inl)
    worst_enob = min(all_enob)
    max_missing = max(all_missing)

    measurements = {
        "RESULT_DNL_LSB": worst_dnl,
        "RESULT_INL_LSB": worst_inl,
        "RESULT_ENOB": worst_enob,
        "RESULT_CONVERSION_TIME_NS": conv_time,
        "RESULT_POWER_UW": power,
        "cap_mismatch_sigma_pct": cap_mismatch * 100,
        "comp_offset_3sig_mv": comp_offset_3sig,
        "missing_codes": max_missing,
        "ktc_noise_uv": SarAdcModel(cu).ktc_noise_rms * 1e6,
    }

    if verbose:
        print(f"  Cu={cu:.0f}fF, Tsar={tsar:.1f}ns")
        print(f"  Cap mismatch sigma: {cap_mismatch*100:.3f}%")
        print(f"  Comp offset (3σ): {comp_offset_3sig:.2f} mV")
        print(f"  DNL={worst_dnl:.3f} LSB, INL={worst_inl:.3f} LSB, ENOB={worst_enob:.2f}")
        print(f"  Conv time={conv_time:.0f} ns, Power={power:.2f} uW")
        print(f"  Missing codes (worst trial): {max_missing}")

    return measurements


# ---------------------------------------------------------------------------
# Cost function
# ---------------------------------------------------------------------------

def compute_cost(measurements: Dict) -> float:
    """Cost function for optimization (lower is better)."""
    cost = 0.0

    dnl = measurements.get("RESULT_DNL_LSB", 99.0)
    if dnl < 0.5:
        cost -= (0.5 - dnl) / 0.5 * 30
    else:
        cost += ((dnl - 0.5) / 0.5) ** 2 * 300

    inl = measurements.get("RESULT_INL_LSB", 99.0)
    if inl < 1.0:
        cost -= (1.0 - inl) / 1.0 * 25
    else:
        cost += ((inl - 1.0) / 1.0) ** 2 * 250

    enob = measurements.get("RESULT_ENOB", 0.0)
    if enob > 5.0:
        cost -= (enob - 5.0) / 1.0 * 20
    else:
        cost += ((5.0 - enob) / 1.0) ** 2 * 200

    conv_time = measurements.get("RESULT_CONVERSION_TIME_NS", 999.0)
    if conv_time < 200.0:
        cost -= (200.0 - conv_time) / 200.0 * 15
    else:
        cost += ((conv_time - 200.0) / 200.0) ** 2 * 150

    power = measurements.get("RESULT_POWER_UW", 999.0)
    if power < 50.0:
        cost -= (50.0 - power) / 50.0 * 10
    else:
        cost += ((power - 50.0) / 50.0) ** 2 * 100

    return cost


def score_params(measurements: Dict) -> Tuple[float, Dict]:
    """Score measurements against specs. Returns (score, details)."""
    specs = {
        "dnl_lsb": {"target": "<0.5", "weight": 30},
        "inl_lsb": {"target": "<1.0", "weight": 25},
        "enob": {"target": ">5.0", "weight": 20},
        "conversion_time_ns": {"target": "<200", "weight": 15},
        "power_uw": {"target": "<50", "weight": 10},
    }

    total_weight = 0
    weighted_score = 0
    details = {}

    for spec_name, spec_def in specs.items():
        weight = spec_def["weight"]
        total_weight += weight

        measured = measurements.get(f"RESULT_{spec_name.upper()}", None)
        target_str = spec_def["target"]

        if measured is None:
            details[spec_name] = {"measured": None, "target": target_str, "met": False, "score": 0}
            continue

        if target_str.startswith("<"):
            threshold = float(target_str[1:])
            met = measured <= threshold
            spec_score = 1.0 if met else max(0, threshold / measured)
        elif target_str.startswith(">"):
            threshold = float(target_str[1:])
            met = measured >= threshold
            spec_score = 1.0 if met else max(0, measured / threshold)
        else:
            met = False
            spec_score = 0

        weighted_score += weight * spec_score
        details[spec_name] = {
            "measured": measured, "target": target_str, "met": met, "score": spec_score
        }

    overall = weighted_score / total_weight if total_weight > 0 else 0
    return overall, details


# ---------------------------------------------------------------------------
# Comparator verification via ngspice
# ---------------------------------------------------------------------------

def verify_comparator_ngspice(params: Dict[str, float], verbose: bool = True) -> Dict:
    """Run a quick ngspice simulation to verify comparator works."""

    netlist = f"""* StrongARM Comparator Verification
.lib "sky130_models/sky130.lib.spice" tt

.subckt strongarm_comp inp inm outp outn clk vdd vss
XMtail ntail clk vss vss sky130_fd_pr__nfet_01v8 W={params['Wcomp_tail']}u L=0.15u nf=1
XM1 d1 inp ntail vss sky130_fd_pr__nfet_01v8 W={params['Wcomp_in']}u L={params['Lcomp_in']}u nf=1
XM2 d2 inm ntail vss sky130_fd_pr__nfet_01v8 W={params['Wcomp_in']}u L={params['Lcomp_in']}u nf=1
XMr1 d1 clk vdd vdd sky130_fd_pr__pfet_01v8 W=2u L=0.15u nf=1
XMr2 d2 clk vdd vdd sky130_fd_pr__pfet_01v8 W=2u L=0.15u nf=1
XMr3 outn clk vdd vdd sky130_fd_pr__pfet_01v8 W=2u L=0.15u nf=1
XMr4 outp clk vdd vdd sky130_fd_pr__pfet_01v8 W=2u L=0.15u nf=1
XMp1 outp outn vdd vdd sky130_fd_pr__pfet_01v8 W={params['Wcomp_latch']}u L={params['Lcomp_latch']}u nf=1
XMp2 outn outp vdd vdd sky130_fd_pr__pfet_01v8 W={params['Wcomp_latch']}u L={params['Lcomp_latch']}u nf=1
XMn1 outp outn d1 vss sky130_fd_pr__nfet_01v8 W={params['Wcomp_latch']}u L={params['Lcomp_latch']}u nf=1
XMn2 outn outp d2 vss sky130_fd_pr__nfet_01v8 W={params['Wcomp_latch']}u L={params['Lcomp_latch']}u nf=1
.ends strongarm_comp

Vdd vdd 0 DC 1.8
Vss vss 0 DC 0
Vinp inp 0 DC 0.91
Vinm inm 0 DC 0.90
Vclk clk 0 PULSE(0 1.8 1n 0.1n 0.1n 10n 20n)

Xcomp inp inm outp outn clk vdd vss strongarm_comp

.tran 0.01n 40n

.control
run
let delay_p = -1
let delay_n = -1

* Check if comparator resolves
meas tran vop_final find v(outp) at=10n
meas tran von_final find v(outn) at=10n
meas tran t_resolve_p trig v(clk) val=0.9 rise=1 targ v(outp) val=0.9 cross=1
meas tran t_resolve_n trig v(clk) val=0.9 rise=1 targ v(outn) val=0.9 fall=1

echo "COMP_OUTP $&vop_final"
echo "COMP_OUTN $&von_final"
echo "COMP_RESOLVE_P $&t_resolve_p"
echo "COMP_RESOLVE_N $&t_resolve_n"

* Check with 1mV difference
alter vinp dc = 0.901
alter vinm dc = 0.900
run
meas tran vop_1mv find v(outp) at=10n
meas tran von_1mv find v(outn) at=10n
echo "COMP_1MV_OUTP $&vop_1mv"
echo "COMP_1MV_OUTN $&von_1mv"

echo "COMP_DONE"
.endc

.end
"""

    tmp_dir = tempfile.mkdtemp(prefix="comp_verify_")
    netlist_path = os.path.join(tmp_dir, "comp_test.cir")

    with open(netlist_path, "w") as f:
        f.write(netlist)

    try:
        result = subprocess.run(
            ["ngspice", "-b", netlist_path],
            capture_output=True, text=True, timeout=60,
            cwd=PROJECT_DIR
        )
        output = result.stdout + result.stderr
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            os.unlink(netlist_path)
            os.rmdir(tmp_dir)
        except:
            pass

    if "COMP_DONE" not in output:
        if verbose:
            print("  Comparator verification FAILED (no COMP_DONE)")
            print(f"  Output tail: {output[-500:]}")
        return {"error": "simulation_failed"}

    results = {}
    import re
    for key in ["COMP_OUTP", "COMP_OUTN", "COMP_RESOLVE_P", "COMP_RESOLVE_N",
                "COMP_1MV_OUTP", "COMP_1MV_OUTN"]:
        match = re.search(rf'{key}\s+([\d.eE+-]+)', output)
        if match:
            results[key] = float(match.group(1))

    if verbose:
        print(f"  Comparator outputs (10mV diff): outp={results.get('COMP_OUTP', 'N/A'):.3f}V, "
              f"outn={results.get('COMP_OUTN', 'N/A'):.3f}V")
        resolve_p = results.get('COMP_RESOLVE_P', None)
        if resolve_p and resolve_p > 0:
            print(f"  Resolution time: {resolve_p*1e9:.2f} ns")

    return results


# ---------------------------------------------------------------------------
# Optimization
# ---------------------------------------------------------------------------

def objective(x: np.ndarray, param_names: list, param_bounds: list) -> float:
    """Objective function for optimizer."""
    params = {}
    for i, name in enumerate(param_names):
        _, _, scale = param_bounds[i]
        if scale == "log":
            params[name] = 10 ** x[i]
        else:
            params[name] = x[i]

    measurements = evaluate_parameters(params, n_mismatch_trials=3)
    cost = compute_cost(measurements)
    return cost


def run_optimization(n_iterations: int = 100, method: str = "differential_evolution"):
    """Run parameter optimization."""

    param_defs = [
        ("Cu", 10, 500, "log"),
        ("Wcomp_in", 10, 100, "log"),
        ("Lcomp_in", 0.5, 2, "log"),
        ("Wcomp_latch", 0.5, 10, "log"),
        ("Lcomp_latch", 0.15, 1, "log"),
        ("Wcomp_tail", 5, 50, "log"),
        ("Tsar_ns", 5, 50, "log"),
    ]

    param_names = [p[0] for p in param_defs]
    param_bounds_raw = [(p[1], p[2], p[3]) for p in param_defs]

    # Convert bounds to optimizer space (log scale)
    bounds = []
    for lo, hi, scale in param_bounds_raw:
        if scale == "log":
            bounds.append((np.log10(lo), np.log10(hi)))
        else:
            bounds.append((lo, hi))

    print(f"Starting optimization with {method}...")
    print(f"Parameters: {param_names}")
    print(f"Bounds: {bounds}")
    print()

    # First, try the proven comparator values as starting point
    x0 = []
    default_params = {
        "Cu": 100, "Wcomp_in": 50, "Lcomp_in": 1.0,
        "Wcomp_latch": 1.0, "Lcomp_latch": 0.5,
        "Wcomp_tail": 25, "Tsar_ns": 20
    }

    for name, lo, hi, scale in param_defs:
        val = default_params[name]
        if scale == "log":
            x0.append(np.log10(val))
        else:
            x0.append(val)

    # Evaluate starting point
    print("=" * 60)
    print("Evaluating starting point (proven comparator values)...")
    meas0 = evaluate_parameters(default_params, n_mismatch_trials=5, verbose=True)
    cost0 = compute_cost(meas0)
    score0, details0 = score_params(meas0)
    print(f"Starting cost: {cost0:.2f}, score: {score0:.3f}")
    print()

    best_params = dict(default_params)
    best_cost = cost0
    best_meas = meas0
    best_score = score0

    # Check if starting point already passes
    all_pass = all(d.get("met", False) for d in details0.values())
    if all_pass:
        print("Starting point already passes all specs!")

    # Use scipy differential evolution
    from scipy.optimize import differential_evolution

    def obj_func(x):
        params = {}
        for i, name in enumerate(param_names):
            _, _, _, scale = param_defs[i]
            if scale == "log":
                params[name] = 10 ** x[i]
            else:
                params[name] = x[i]

        measurements = evaluate_parameters(params, n_mismatch_trials=3)
        cost = compute_cost(measurements)
        score, _ = score_params(measurements)

        # Track best
        nonlocal best_cost, best_params, best_meas, best_score
        if cost < best_cost:
            best_cost = cost
            best_params = dict(params)
            best_meas = dict(measurements)
            best_score = score
            print(f"  NEW BEST: cost={cost:.2f}, score={score:.3f}, "
                  f"DNL={measurements['RESULT_DNL_LSB']:.3f}, "
                  f"INL={measurements['RESULT_INL_LSB']:.3f}, "
                  f"ENOB={measurements['RESULT_ENOB']:.2f}, "
                  f"time={measurements['RESULT_CONVERSION_TIME_NS']:.0f}ns, "
                  f"power={measurements['RESULT_POWER_UW']:.1f}uW")

        return cost

    print(f"Running differential evolution ({n_iterations} max iterations)...")
    result = differential_evolution(
        obj_func, bounds,
        x0=x0,
        maxiter=n_iterations,
        popsize=15,
        mutation=(0.5, 1.5),
        recombination=0.8,
        seed=42,
        tol=0.01,
        disp=True,
        polish=True,
        workers=1,  # Can't parallelize due to shared state
    )

    print(f"\nOptimization complete!")
    print(f"Best cost: {best_cost:.2f}")
    print(f"Best score: {best_score:.3f}")

    return best_params, best_meas, best_score


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def generate_all_plots(params: Dict[str, float]):
    """Generate all verification plots for the ADC."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    os.makedirs(PLOTS_DIR, exist_ok=True)

    cu = params["Cu"]
    cap_mismatch = compute_cap_mismatch_sigma(cu)
    comp_offset = compute_comparator_offset(params["Wcomp_in"], params["Lcomp_in"])

    # Use a dark theme
    dark_theme = {
        'figure.facecolor': '#1a1a2e', 'axes.facecolor': '#16213e',
        'axes.edgecolor': '#e94560', 'axes.labelcolor': '#eee',
        'text.color': '#eee', 'xtick.color': '#aaa', 'ytick.color': '#aaa',
        'grid.color': '#333', 'grid.alpha': 0.5, 'lines.linewidth': 1.5,
    }
    plt.rcParams.update(dark_theme)

    # --- 1. Transfer Curve (TB1) ---
    adc = SarAdcModel(cu_ff=cu, cap_mismatch_sigma=cap_mismatch, seed=42)
    codes = adc.sweep(n_points=1024)
    vins = [c[0] for c in codes]
    code_vals = [c[1] for c in codes]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.step(vins, code_vals, where='mid', color='#00d2ff', linewidth=1, label='Actual')
    ideal = [min(63, max(0, int(v / (VDD / 64)))) for v in vins]
    ax.plot(vins, ideal, '--', color='#e94560', alpha=0.5, linewidth=0.8, label='Ideal')
    ax.set_xlabel('Input Voltage (V)')
    ax.set_ylabel('Output Code')
    ax.set_title('SAR ADC Transfer Curve (6-bit)')
    ax.legend()
    ax.grid(True)
    ax.set_xlim(0, VDD)
    ax.set_ylim(-1, 65)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'adc_transfer_curve.png'), dpi=150)
    plt.close()
    print("  Saved: plots/adc_transfer_curve.png")

    # --- 2. DNL (TB2) ---
    dnl, inl, max_dnl, max_inl = compute_dnl_inl(codes)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(range(N_CODES), dnl, color='#00d2ff', alpha=0.8, width=1.0)
    ax.axhline(y=0.5, color='#e94560', linestyle='--', linewidth=1.5, label='Spec: +0.5 LSB')
    ax.axhline(y=-0.5, color='#e94560', linestyle='--', linewidth=1.5, label='Spec: -0.5 LSB')
    ax.set_xlabel('Code')
    ax.set_ylabel('DNL (LSB)')
    ax.set_title(f'Differential Non-Linearity — Worst Case: {max_dnl:.3f} LSB')
    ax.legend(fontsize=9)
    ax.grid(True)
    ax.set_xlim(-1, N_CODES)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'adc_dnl.png'), dpi=150)
    plt.close()
    print("  Saved: plots/adc_dnl.png")

    # --- 3. INL (TB3) ---
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(range(N_CODES), inl, color='#00ff88', linewidth=1.5)
    ax.axhline(y=1.0, color='#e94560', linestyle='--', linewidth=1.5, label='Spec: +1.0 LSB')
    ax.axhline(y=-1.0, color='#e94560', linestyle='--', linewidth=1.5, label='Spec: -1.0 LSB')
    ax.set_xlabel('Code')
    ax.set_ylabel('INL (LSB)')
    ax.set_title(f'Integral Non-Linearity — Worst Case: {max_inl:.3f} LSB')
    ax.legend(fontsize=9)
    ax.grid(True)
    ax.set_xlim(-1, N_CODES)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'adc_inl.png'), dpi=150)
    plt.close()
    print("  Saved: plots/adc_inl.png")

    # --- 4. Code Histogram (TB4 missing codes check) ---
    # Use a ramp with many points to get code density
    codes_dense = adc.sweep(n_points=4096)
    code_counts = np.zeros(N_CODES)
    for _, c in codes_dense:
        if 0 <= c < N_CODES:
            code_counts[c] += 1

    missing = [i for i in range(N_CODES) if code_counts[i] == 0]

    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ['#e94560' if code_counts[i] == 0 else '#00d2ff' for i in range(N_CODES)]
    ax.bar(range(N_CODES), code_counts, color=colors, width=1.0)
    ax.set_xlabel('Output Code')
    ax.set_ylabel('Count')
    title = 'Code Histogram (Ramp Input)'
    if missing:
        title += f' — MISSING CODES: {missing}'
    else:
        title += ' — No Missing Codes'
    ax.set_title(title)
    ax.grid(True)
    ax.set_xlim(-1, N_CODES)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'adc_code_histogram.png'), dpi=150)
    plt.close()
    print("  Saved: plots/adc_code_histogram.png")

    # --- 5. DNL+INL combined (for main plots) ---
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))

    ax1.bar(range(N_CODES), dnl, color='#00d2ff', alpha=0.8, width=1.0)
    ax1.axhline(y=0.5, color='#e94560', linestyle='--', label='+0.5 LSB spec')
    ax1.axhline(y=-0.5, color='#e94560', linestyle='--', label='-0.5 LSB spec')
    ax1.set_xlabel('Code')
    ax1.set_ylabel('DNL (LSB)')
    ax1.set_title(f'DNL — Worst: {max_dnl:.3f} LSB')
    ax1.legend(fontsize=8)
    ax1.grid(True)

    ax2.plot(range(N_CODES), inl, color='#00ff88', linewidth=1.5)
    ax2.axhline(y=1.0, color='#e94560', linestyle='--', label='+1.0 LSB spec')
    ax2.axhline(y=-1.0, color='#e94560', linestyle='--', label='-1.0 LSB spec')
    ax2.set_xlabel('Code')
    ax2.set_ylabel('INL (LSB)')
    ax2.set_title(f'INL — Worst: {max_inl:.3f} LSB')
    ax2.legend(fontsize=8)
    ax2.grid(True)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'dnl_inl.png'), dpi=150)
    plt.close()
    print("  Saved: plots/dnl_inl.png")

    # --- 6. Monte Carlo DNL/INL distribution ---
    mc_dnl = []
    mc_inl = []
    mc_enob = []
    n_mc = 50

    for trial in range(n_mc):
        adc_mc = SarAdcModel(
            cu_ff=cu,
            cap_mismatch_sigma=cap_mismatch,
            seed=trial * 37 + 1
        )
        codes_mc = adc_mc.sweep(n_points=512)
        _, _, md, mi = compute_dnl_inl(codes_mc)
        me = compute_enob(codes_mc)
        mc_dnl.append(md)
        mc_inl.append(mi)
        mc_enob.append(me)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4))

    axes[0].hist(mc_dnl, bins=20, color='#00d2ff', alpha=0.8, edgecolor='#16213e')
    axes[0].axvline(x=0.5, color='#e94560', linestyle='--', linewidth=2, label='Spec: 0.5 LSB')
    axes[0].set_xlabel('Worst-Case DNL (LSB)')
    axes[0].set_ylabel('Count')
    axes[0].set_title('Monte Carlo DNL Distribution')
    axes[0].legend(fontsize=8)

    axes[1].hist(mc_inl, bins=20, color='#00ff88', alpha=0.8, edgecolor='#16213e')
    axes[1].axvline(x=1.0, color='#e94560', linestyle='--', linewidth=2, label='Spec: 1.0 LSB')
    axes[1].set_xlabel('Worst-Case INL (LSB)')
    axes[1].set_ylabel('Count')
    axes[1].set_title('Monte Carlo INL Distribution')
    axes[1].legend(fontsize=8)

    axes[2].hist(mc_enob, bins=20, color='#ffcc00', alpha=0.8, edgecolor='#16213e')
    axes[2].axvline(x=5.0, color='#e94560', linestyle='--', linewidth=2, label='Spec: 5.0 bits')
    axes[2].set_xlabel('ENOB (bits)')
    axes[2].set_ylabel('Count')
    axes[2].set_title('Monte Carlo ENOB Distribution')
    axes[2].legend(fontsize=8)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'monte_carlo_distribution.png'), dpi=150)
    plt.close()
    print("  Saved: plots/monte_carlo_distribution.png")

    # --- 7. SAR Conversion Waveform (TB6) ---
    # Simulate one conversion step by step
    vin_test = 0.7  # Test voltage
    adc_wave = SarAdcModel(cu_ff=cu, cap_mismatch_sigma=0, seed=0)

    tsar_ns = params["Tsar_ns"]
    times = []
    v_dac_trace = []
    clk_trace = []
    bit_traces = {f"b{i}": [] for i in range(N_BITS)}

    code = 0
    bits = np.zeros(N_BITS, dtype=int)

    # Record sample phase
    for sub_t in np.linspace(0, 5, 20):  # 5ns sample
        times.append(sub_t)
        v_dac_trace.append(0)
        clk_trace.append(0)
        for b in range(N_BITS):
            bit_traces[f"b{b}"].append(0)

    # Bit trials
    for bit_idx in range(N_BITS):
        t_start = 5 + bit_idx * tsar_ns
        bit_weight = int(adc_wave.weights[bit_idx])

        # Set bit
        code += bit_weight
        bits[bit_idx] = 1
        v_dac = adc_wave.dac_voltage(code)

        # Record with bit set
        for sub_t in np.linspace(t_start, t_start + tsar_ns * 0.4, 10):
            times.append(sub_t)
            v_dac_trace.append(v_dac)
            clk_trace.append(VDD)
            for b in range(N_BITS):
                bit_traces[f"b{b}"].append(VDD if bits[b] else 0)

        # Comparator decides
        if v_dac > vin_test:
            code -= bit_weight
            bits[bit_idx] = 0

        v_dac_after = adc_wave.dac_voltage(code)

        # Record after decision
        for sub_t in np.linspace(t_start + tsar_ns * 0.5, t_start + tsar_ns, 10):
            times.append(sub_t)
            v_dac_trace.append(v_dac_after)
            clk_trace.append(0)
            for b in range(N_BITS):
                bit_traces[f"b{b}"].append(VDD if bits[b] else 0)

    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)

    axes[0].plot(times, v_dac_trace, color='#00d2ff', linewidth=1.5)
    axes[0].axhline(y=vin_test, color='#e94560', linestyle='--', alpha=0.5, label=f'Vin={vin_test}V')
    axes[0].set_ylabel('DAC Output (V)')
    axes[0].set_title(f'SAR Conversion Waveform — Vin={vin_test}V')
    axes[0].legend(fontsize=8)
    axes[0].grid(True)

    axes[1].plot(times, clk_trace, color='#ffcc00', linewidth=1)
    axes[1].set_ylabel('SAR Clock (V)')
    axes[1].set_ylim(-0.2, 2.0)
    axes[1].grid(True)

    for b in range(3):  # MSB bits
        axes[2].plot(times, bit_traces[f"b{b}"], label=f'Bit {5-b} (w={int(adc_wave.weights[b])})',
                     linewidth=1.2, alpha=0.8)
    axes[2].set_ylabel('MSB Bits (V)')
    axes[2].legend(fontsize=7)
    axes[2].set_ylim(-0.2, 2.0)
    axes[2].grid(True)

    for b in range(3, 6):  # LSB bits
        axes[3].plot(times, bit_traces[f"b{b}"], label=f'Bit {5-b} (w={int(adc_wave.weights[b])})',
                     linewidth=1.2, alpha=0.8)
    axes[3].set_ylabel('LSB Bits (V)')
    axes[3].set_xlabel('Time (ns)')
    axes[3].legend(fontsize=7)
    axes[3].set_ylim(-0.2, 2.0)
    axes[3].grid(True)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'adc_timing.png'), dpi=150)
    plt.close()
    print("  Saved: plots/adc_timing.png")

    # --- 8. Transfer curve (zoomed to show steps clearly) ---
    fig, ax = plt.subplots(figsize=(10, 6))
    # Zoom to show individual steps
    zoom_start = 0.3
    zoom_end = 0.6
    zoom_codes = [(v, c) for v, c in codes if zoom_start <= v <= zoom_end]
    if zoom_codes:
        ax.step([c[0] for c in zoom_codes], [c[1] for c in zoom_codes],
                where='mid', color='#00d2ff', linewidth=1.5, label='Actual')
        ideal_zoom = [min(63, max(0, int(v / (VDD / 64)))) for v, _ in zoom_codes]
        ax.plot([c[0] for c in zoom_codes], ideal_zoom, '--', color='#e94560',
                alpha=0.5, linewidth=0.8, label='Ideal')
        ax.set_xlabel('Input Voltage (V)')
        ax.set_ylabel('Output Code')
        ax.set_title('SAR ADC Transfer Curve (Zoomed)')
        ax.legend()
        ax.grid(True)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'transfer_curve_zoomed.png'), dpi=150)
    plt.close()
    print("  Saved: plots/transfer_curve_zoomed.png")

    return {
        "max_dnl": max_dnl,
        "max_inl": max_inl,
        "missing_codes": missing,
        "mc_dnl_mean": np.mean(mc_dnl),
        "mc_dnl_max": np.max(mc_dnl),
        "mc_inl_mean": np.mean(mc_inl),
        "mc_inl_max": np.max(mc_inl),
        "mc_enob_mean": np.mean(mc_enob),
        "mc_enob_min": np.min(mc_enob),
    }


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

def save_best(params: Dict, measurements: Dict, score: float, details: Dict):
    """Save best parameters and measurements."""
    os.makedirs(PLOTS_DIR, exist_ok=True)

    with open(os.path.join(PROJECT_DIR, "best_parameters.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "value"])
        for name, val in sorted(params.items()):
            w.writerow([name, val])

    with open(os.path.join(PROJECT_DIR, "measurements.json"), "w") as f:
        json.dump({
            "measurements": measurements,
            "score": score,
            "details": details,
            "parameters": params,
        }, f, indent=2, default=str)

    print(f"Saved: best_parameters.csv, measurements.json")


def update_results_tsv(step: int, commit: str, score: float,
                       specs_met: int, notes: str):
    """Append to results.tsv."""
    tsv_path = os.path.join(PROJECT_DIR, "results.tsv")
    if not os.path.exists(tsv_path):
        with open(tsv_path, "w") as f:
            f.write("step\tcommit\tscore\tspecs_met\tnotes\n")

    with open(tsv_path, "a") as f:
        f.write(f"{step}\t{commit}\t{score:.3f}\t{specs_met}\t{notes}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SAR ADC Optimizer")
    parser.add_argument("--validate", action="store_true", help="Validate current best params")
    parser.add_argument("--iterations", type=int, default=50, help="Max optimizer iterations")
    parser.add_argument("--plots-only", action="store_true", help="Just generate plots")
    parser.add_argument("--verify-comp", action="store_true", help="Just verify comparator")
    args = parser.parse_args()

    if args.verify_comp:
        print("Verifying comparator in ngspice...")
        params = {"Wcomp_in": 50, "Lcomp_in": 1.0, "Wcomp_latch": 1.0,
                  "Lcomp_latch": 0.5, "Wcomp_tail": 25}
        results = verify_comparator_ngspice(params, verbose=True)
        return

    if args.plots_only:
        # Load existing best params
        params = {}
        with open(os.path.join(PROJECT_DIR, "best_parameters.csv")) as f:
            reader = csv.DictReader(f)
            for row in reader:
                params[row["name"]] = float(row["value"])
        print("Generating plots...")
        generate_all_plots(params)
        return

    if args.validate:
        # Load and validate existing best params
        params = {}
        with open(os.path.join(PROJECT_DIR, "best_parameters.csv")) as f:
            reader = csv.DictReader(f)
            for row in reader:
                params[row["name"]] = float(row["value"])

        print("Validating current best parameters...")
        measurements = evaluate_parameters(params, n_mismatch_trials=10, verbose=True)
        score, details = score_params(measurements)

        print(f"\nScore: {score:.3f}")
        for name, d in details.items():
            status = "PASS" if d["met"] else "FAIL"
            print(f"  {name:<25} {d['target']:>8}  measured={d['measured']:.3f}  {status}")

        return

    # Full optimization
    print("=" * 60)
    print("  SAR ADC Parameter Optimization")
    print("=" * 60)

    t0 = time.time()
    best_params, best_meas, best_score = run_optimization(n_iterations=args.iterations)
    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"  OPTIMIZATION COMPLETE — {elapsed:.0f}s")
    print(f"{'=' * 60}")

    # Final evaluation with more MC trials
    print("\nFinal evaluation (10 MC trials)...")
    final_meas = evaluate_parameters(best_params, n_mismatch_trials=10, verbose=True)
    final_score, final_details = score_params(final_meas)

    print(f"\nFinal Score: {final_score:.3f}")
    specs_met = 0
    for name, d in final_details.items():
        status = "PASS" if d["met"] else "FAIL"
        if d["met"]:
            specs_met += 1
        measured = d.get("measured", "N/A")
        if isinstance(measured, float):
            print(f"  {name:<25} target={d['target']:>8}  measured={measured:.3f}  {status}")
        else:
            print(f"  {name:<25} target={d['target']:>8}  measured={measured}  {status}")

    print(f"\nBest Parameters:")
    for name, val in sorted(best_params.items()):
        print(f"  {name:<20} = {val:.4f}")

    # Save results
    save_best(best_params, final_meas, final_score, final_details)

    # Generate plots
    print("\nGenerating plots...")
    plot_stats = generate_all_plots(best_params)

    # Verify comparator
    print("\nVerifying comparator in ngspice...")
    comp_results = verify_comparator_ngspice(best_params, verbose=True)

    print(f"\n{'=' * 60}")
    print(f"  ALL DONE — Score: {final_score:.3f}, Specs met: {specs_met}/5")
    print(f"{'=' * 60}")

    return final_score, best_params, final_meas


if __name__ == "__main__":
    main()
