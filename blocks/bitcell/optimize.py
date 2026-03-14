#!/usr/bin/env python3
"""
CIM SRAM Bitcell Optimizer — measures all 5 specs including SNM.
Uses scipy differential_evolution for global optimization.
Targets i_read in the 5-50 uA range (CIM-appropriate).
"""

import os
import sys
import csv
import json
import time
import shutil
import tempfile
import subprocess
import re
import numpy as np
from scipy.optimize import differential_evolution

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
NGSPICE = "ngspice"

PARAM_NAMES = ['Wp', 'Lp', 'Wn', 'Ln', 'Wax', 'Wrd', 'Lrd']
PARAM_BOUNDS = [
    (0.42, 5.0),   # Wp
    (0.15, 1.0),   # Lp
    (0.42, 5.0),   # Wn
    (0.15, 1.0),   # Ln
    (0.42, 3.0),   # Wax
    (0.42, 10.0),  # Wrd
    (0.15, 1.0),   # Lrd
]


def make_read_netlist(p, corner="tt", temp=24, vs=1.8):
    return f"""* Read current measurement (weight=1)
.lib "sky130_models/sky130.lib.spice" {corner}
Vdd vdd 0 DC {vs}
Vss vss 0 DC 0
XMPL q qb vdd vdd sky130_fd_pr__pfet_01v8 W={p['Wp']}u L={p['Lp']}u nf=1
XMNL q qb vss vss sky130_fd_pr__nfet_01v8 W={p['Wn']}u L={p['Ln']}u nf=1
XMPR qb q vdd vdd sky130_fd_pr__pfet_01v8 W={p['Wp']}u L={p['Lp']}u nf=1
XMNR qb q vss vss sky130_fd_pr__nfet_01v8 W={p['Wn']}u L={p['Ln']}u nf=1
XMAXL blw wwl q vss sky130_fd_pr__nfet_01v8 W={p['Wax']}u L=0.15u nf=1
XMAXR blbw wwl qb vss sky130_fd_pr__nfet_01v8 W={p['Wax']}u L=0.15u nf=1
XMRD1 bl q mid_rd vss sky130_fd_pr__nfet_01v8 W={p['Wrd']}u L={p['Lrd']}u nf=1
XMRD2 mid_rd wl vss vss sky130_fd_pr__nfet_01v8 W={p['Wrd']}u L={p['Lrd']}u nf=1
Vblw blw 0 PWL(0 {vs} 5n {vs} 5.1n 0)
Vblbw blbw 0 PWL(0 0 5n 0 5.1n 0)
Vwwl wwl 0 PWL(0 {vs} 5n {vs} 5.1n 0)
Vwl wl 0 PWL(0 0 10n 0 10.1n {vs})
Vbl bl 0 DC {vs}
.options reltol=0.003 method=gear
.temp {temp}
.control
tran 0.05n 30n
meas tran i_read find i(Vbl) at=25n
meas tran q_val find v(q) at=9n
meas tran qb_val find v(qb) at=9n
meas tran q_read find v(q) at=25n
meas tran qb_read find v(qb) at=25n
meas tran i_10p5 find i(Vbl) at=10.5n
meas tran i_11 find i(Vbl) at=11n
meas tran i_11p5 find i(Vbl) at=11.5n
meas tran i_12 find i(Vbl) at=12n
meas tran i_13 find i(Vbl) at=13n
meas tran i_15 find i(Vbl) at=15n
meas tran i_20 find i(Vbl) at=20n
echo "RESULT_I_READ $&i_read"
echo "RESULT_Q_VAL $&q_val"
echo "RESULT_QB_VAL $&qb_val"
echo "RESULT_Q_READ $&q_read"
echo "RESULT_QB_READ $&qb_read"
echo "RESULT_I_10P5 $&i_10p5"
echo "RESULT_I_11 $&i_11"
echo "RESULT_I_11P5 $&i_11p5"
echo "RESULT_I_12 $&i_12"
echo "RESULT_I_13 $&i_13"
echo "RESULT_I_15 $&i_15"
echo "RESULT_I_20 $&i_20"
echo "RESULT_DONE"
.endc
.end
"""


def make_leak_netlist(p, corner="tt", temp=24, vs=1.8):
    return f"""* Leakage measurement (weight=0)
.lib "sky130_models/sky130.lib.spice" {corner}
Vdd vdd 0 DC {vs}
Vss vss 0 DC 0
XMPL q qb vdd vdd sky130_fd_pr__pfet_01v8 W={p['Wp']}u L={p['Lp']}u nf=1
XMNL q qb vss vss sky130_fd_pr__nfet_01v8 W={p['Wn']}u L={p['Ln']}u nf=1
XMPR qb q vdd vdd sky130_fd_pr__pfet_01v8 W={p['Wp']}u L={p['Lp']}u nf=1
XMNR qb q vss vss sky130_fd_pr__nfet_01v8 W={p['Wn']}u L={p['Ln']}u nf=1
XMAXL blw wwl q vss sky130_fd_pr__nfet_01v8 W={p['Wax']}u L=0.15u nf=1
XMAXR blbw wwl qb vss sky130_fd_pr__nfet_01v8 W={p['Wax']}u L=0.15u nf=1
XMRD1 bl q mid_rd vss sky130_fd_pr__nfet_01v8 W={p['Wrd']}u L={p['Lrd']}u nf=1
XMRD2 mid_rd wl vss vss sky130_fd_pr__nfet_01v8 W={p['Wrd']}u L={p['Lrd']}u nf=1
Vblw blw 0 PWL(0 0 5n 0 5.1n 0)
Vblbw blbw 0 PWL(0 {vs} 5n {vs} 5.1n 0)
Vwwl wwl 0 PWL(0 {vs} 5n {vs} 5.1n 0)
Vwl wl 0 PWL(0 0 10n 0 10.1n {vs})
Vbl bl 0 DC {vs}
.options reltol=0.003 method=gear
.temp {temp}
.control
tran 0.05n 30n
meas tran i_leak find i(Vbl) at=25n
meas tran q_val find v(q) at=25n
meas tran qb_val find v(qb) at=25n
echo "RESULT_I_LEAK $&i_leak"
echo "RESULT_Q_VAL $&q_val"
echo "RESULT_QB_VAL $&qb_val"
echo "RESULT_DONE"
.endc
.end
"""


def make_snm_netlist(p, corner="tt", temp=24, vs=1.8):
    return f"""* SNM - Inverter VTC
.lib "sky130_models/sky130.lib.spice" {corner}
Vdd vdd 0 DC {vs}
Vss vss 0 DC 0
Vin in 0 DC 0
XMP out in vdd vdd sky130_fd_pr__pfet_01v8 W={p['Wp']}u L={p['Lp']}u nf=1
XMN out in vss vss sky130_fd_pr__nfet_01v8 W={p['Wn']}u L={p['Ln']}u nf=1
.options reltol=0.001
.temp {temp}
.control
dc Vin 0 {vs} 0.005
wrdata snm_vtc_data v(out)
echo "RESULT_DONE"
.endc
.end
"""


def run_ngspice(netlist, filename, tmp_dir):
    path = os.path.join(tmp_dir, filename)
    with open(path, "w") as f:
        f.write(netlist)
    try:
        r = subprocess.run(
            [NGSPICE, "-b", path], capture_output=True, text=True,
            timeout=60, cwd=PROJECT_DIR)
        return r.stdout + r.stderr
    except:
        return ""


def parse_results(output):
    m = {}
    for line in output.split("\n"):
        if "RESULT_" in line and "RESULT_DONE" not in line:
            match = re.search(r'(RESULT_\w+)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)', line)
            if match:
                m[match.group(1)] = float(match.group(2))
    return m


def compute_snm():
    """Compute SNM using correct butterfly curve method."""
    vtc_file = os.path.join(PROJECT_DIR, "snm_vtc_data")
    if not os.path.exists(vtc_file):
        return 0.0
    try:
        vin, vout = [], []
        with open(vtc_file) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        vin.append(float(parts[0]))
                        vout.append(float(parts[1]))
                    except ValueError:
                        continue
        if len(vin) < 20:
            return 0.0

        vin = np.array(vin)
        vout = np.array(vout)

        # Butterfly curve: gap = f(x) - f^(-1)(x)
        # f^(-1)(x): for each x, find y such that f(y) = x
        # Since VTC is monotonically decreasing, reverse arrays for interp
        f_inv = np.interp(vin, vout[::-1], vin[::-1])
        gap = vout - f_inv

        # Find trip point (gap crosses zero, away from endpoints)
        margin = max(5, len(vin) // 10)
        trip_idx = margin + np.argmin(np.abs(gap[margin:-margin]))

        # Upper eye: gap > 0 (x < trip point)
        upper = gap[:trip_idx]
        snm_upper = np.max(upper) if len(upper) > 0 and np.any(upper > 0) else 0

        # Lower eye: gap < 0 (x > trip point)
        lower = gap[trip_idx:]
        snm_lower = np.max(-lower) if len(lower) > 0 and np.any(lower < 0) else 0

        # SNM = side of inscribed square = diagonal / sqrt(2)
        snm_v = min(snm_upper, snm_lower) / np.sqrt(2)
        return snm_v * 1000  # mV
    except Exception:
        return 0.0
    finally:
        try:
            os.unlink(vtc_file)
        except:
            pass


def measure_all(p, corner="tt", temp=24, vs=1.8):
    """Measure all 5 specs."""
    tmp_dir = tempfile.mkdtemp(prefix="bc_")
    result = {"i_read_ua": 0, "i_leak_na": 1e6, "on_off_ratio": 0,
              "snm_mv": 0, "t_read_ns": 999, "storage_ok": False,
              "read_disturb_ok": False, "error": None}

    # 1. Read current
    out = run_ngspice(make_read_netlist(p, corner, temp, vs), "read.cir", tmp_dir)
    if "RESULT_DONE" not in out:
        result["error"] = "read_fail"
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return result

    m = parse_results(out)
    i_read_raw = m.get("RESULT_I_READ", 0)
    i_read_ua = abs(i_read_raw) * 1e6
    result["i_read_ua"] = i_read_ua

    q_val = m.get("RESULT_Q_VAL", 0)
    qb_val = m.get("RESULT_QB_VAL", vs)
    result["storage_ok"] = (q_val > 0.8 * vs and qb_val < 0.2 * vs)
    result["q_val"] = q_val
    result["qb_val"] = qb_val

    q_read = m.get("RESULT_Q_READ", 0)
    qb_read = m.get("RESULT_QB_READ", vs)
    result["read_disturb_ok"] = (q_read > 0.7 * vs and qb_read < 0.3 * vs)

    # Compute t_read from sampled currents (WL rises at 10ns)
    i_steady = abs(i_read_raw)
    if i_steady > 1e-12:
        i_target = 0.9 * i_steady
        times = [10.5, 11, 11.5, 12, 13, 15, 20]
        keys = ["RESULT_I_10P5", "RESULT_I_11", "RESULT_I_11P5",
                "RESULT_I_12", "RESULT_I_13", "RESULT_I_15", "RESULT_I_20"]
        currents = [abs(m.get(k, 0)) for k in keys]

        t_read_ns = 999.0
        for i, (t, curr) in enumerate(zip(times, currents)):
            if curr >= i_target:
                if i == 0:
                    t_read_ns = t - 10.0
                else:
                    t_prev = times[i - 1]
                    c_prev = currents[i - 1]
                    frac = (i_target - c_prev) / (curr - c_prev) if curr != c_prev else 0
                    t_read_ns = t_prev + frac * (t - t_prev) - 10.0
                break
        result["t_read_ns"] = max(0.01, t_read_ns)

    # 2. Leakage
    out = run_ngspice(make_leak_netlist(p, corner, temp, vs), "leak.cir", tmp_dir)
    if "RESULT_DONE" in out:
        ml = parse_results(out)
        i_leak_raw = ml.get("RESULT_I_LEAK", 0)
        result["i_leak_na"] = abs(i_leak_raw) * 1e9
        q_l = ml.get("RESULT_Q_VAL", vs)
        qb_l = ml.get("RESULT_QB_VAL", 0)
        if not (q_l < 0.3 * vs and qb_l > 0.7 * vs):
            result["i_leak_na"] = 1e6

    # ON/OFF
    if result["i_leak_na"] > 0:
        result["on_off_ratio"] = (result["i_read_ua"] * 1000) / result["i_leak_na"]

    # 3. SNM
    out = run_ngspice(make_snm_netlist(p, corner, temp, vs), "snm.cir", tmp_dir)
    if "RESULT_DONE" in out:
        result["snm_mv"] = compute_snm()

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return result


# Global tracking
best_cost = float('inf')
best_params = None
best_results = None
eval_count = 0


def cost_fn(p_dict):
    """Cost function. Lower is better. Targets i_read in 5-50 uA range."""
    r = measure_all(p_dict)
    if r["error"] or not r["storage_ok"]:
        return 10000.0

    cost = 0.0

    # I_read > 5 uA, ideal range 8-30 uA for CIM
    ir = r["i_read_ua"]
    if ir < 5.0:
        cost += ((5.0 - ir) / 5.0) ** 2 * 300
    elif ir > 50.0:
        # Penalize excessive current (bad for CIM power)
        cost += ((ir - 50.0) / 50.0) ** 2 * 50
    else:
        # Reward being in the sweet spot
        cost -= 30
        if 8.0 <= ir <= 30.0:
            cost -= 10  # Extra bonus for ideal range

    # I_leak < 100 nA
    il = r["i_leak_na"]
    if il <= 100.0:
        cost -= 25
    else:
        cost += ((il - 100.0) / 100.0) ** 2 * 250

    # ON/OFF > 100
    ratio = r["on_off_ratio"]
    if ratio >= 100:
        cost -= min(np.log10(max(ratio, 1)), 8) * 2.5
    else:
        cost += ((100 - ratio) / 100) ** 2 * 200

    # SNM > 100 mV
    snm = r["snm_mv"]
    if snm >= 100:
        cost -= min((snm - 100) / 100, 5) * 3
    else:
        cost += ((100 - max(snm, 0)) / 100) ** 2 * 150

    # T_read < 5 ns
    tr = r["t_read_ns"]
    if tr <= 5.0:
        cost -= 10
    else:
        cost += ((tr - 5.0) / 5.0) ** 2 * 100

    # Read disturb penalty
    if not r.get("read_disturb_ok", True):
        cost += 500

    # Cell ratio: Wn > Wax for write margin and read stability
    if p_dict["Wn"] <= p_dict["Wax"]:
        cost += 50 * (p_dict["Wax"] / p_dict["Wn"])

    # Prefer smaller area (smaller W*L total)
    total_area = (p_dict["Wp"] * p_dict["Lp"] * 2 +  # 2 PMOS
                  p_dict["Wn"] * p_dict["Ln"] * 2 +  # 2 NMOS
                  p_dict["Wax"] * 0.15 * 2 +          # 2 access
                  p_dict["Wrd"] * p_dict["Lrd"] * 2)  # 2 read port
    cost += total_area * 0.5  # Mild area penalty

    return cost


def objective(x):
    global best_cost, best_params, best_results, eval_count
    eval_count += 1

    p = dict(zip(PARAM_NAMES, x))
    c = cost_fn(p)

    if c < best_cost:
        r = measure_all(p)  # Re-measure for reporting
        best_cost = c
        best_params = dict(p)
        best_results = dict(r)
        save_best(p, r)
        specs_pass = check_specs(r)
        print(f"  [{eval_count:3d}] NEW BEST cost={c:.1f} | "
              f"Ir={r['i_read_ua']:.2f}uA Il={r['i_leak_na']:.2f}nA "
              f"ratio={r['on_off_ratio']:.0f} SNM={r['snm_mv']:.0f}mV "
              f"tr={r['t_read_ns']:.2f}ns {'ALL PASS' if specs_pass else ''}")
    elif eval_count % 25 == 0:
        print(f"  [{eval_count:3d}] cost={c:.1f} (best={best_cost:.1f})")

    return c


def check_specs(r):
    return (r["i_read_ua"] >= 5.0 and r["i_leak_na"] <= 100.0 and
            r["on_off_ratio"] >= 100 and r["snm_mv"] >= 100 and
            r["t_read_ns"] <= 5.0 and r["storage_ok"])


def save_best(p, r):
    with open(os.path.join(PROJECT_DIR, "best_parameters.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "value"])
        for n in PARAM_NAMES:
            w.writerow([n, p[n]])

    meas = {
        "i_read_ua": r["i_read_ua"], "i_leak_na": r["i_leak_na"],
        "on_off_ratio": r["on_off_ratio"], "snm_mv": r["snm_mv"],
        "t_read_ns": r["t_read_ns"], "parameters": p,
        "storage_ok": r["storage_ok"], "read_disturb_ok": r.get("read_disturb_ok", False),
    }
    with open(os.path.join(PROJECT_DIR, "measurements.json"), "w") as f:
        json.dump(meas, f, indent=2, default=str)


def print_status(r, prefix="  "):
    def s(val, spec, d):
        return "PASS" if (val >= spec if d == ">" else val <= spec) else "FAIL"
    print(f"{prefix}I_read:  {r['i_read_ua']:>10.3f} uA  (> 5)    [{s(r['i_read_ua'], 5, '>')}]")
    print(f"{prefix}I_leak:  {r['i_leak_na']:>10.3f} nA  (< 100)  [{s(r['i_leak_na'], 100, '<')}]")
    print(f"{prefix}ON/OFF:  {r['on_off_ratio']:>10.1f}     (> 100)  [{s(r['on_off_ratio'], 100, '>')}]")
    print(f"{prefix}SNM:     {r['snm_mv']:>10.1f} mV  (> 100)  [{s(r['snm_mv'], 100, '>')}]")
    print(f"{prefix}T_read:  {r['t_read_ns']:>10.3f} ns  (< 5)    [{s(r['t_read_ns'], 5, '<')}]")
    print(f"{prefix}Storage: {'OK' if r['storage_ok'] else 'FAIL'}  "
          f"Read disturb: {'OK' if r.get('read_disturb_ok', False) else 'FAIL'}")


def main():
    global best_cost, best_params, best_results, eval_count

    print("=" * 60)
    print("  CIM SRAM Bitcell Optimizer — All 5 Specs")
    print("  Target: i_read 5-50 uA for CIM operation")
    print("=" * 60)
    t0 = time.time()

    # Expert designs targeting moderate read current
    # Key sizing rules:
    #   - Wn > Wax (cell ratio for read stability)
    #   - Wp ~ Wn/2 (pull-up ratio for write margin)
    #   - Wrd*Lrd controls read current (larger Lrd = less current)
    designs = [
        # Moderate read port, short core for good SNM
        {"Wp": 0.55, "Lp": 0.15, "Wn": 0.84, "Ln": 0.15, "Wax": 0.42,
         "Wrd": 0.84, "Lrd": 0.50},
        # Slightly longer read port for lower current
        {"Wp": 0.55, "Lp": 0.15, "Wn": 0.84, "Ln": 0.15, "Wax": 0.42,
         "Wrd": 0.84, "Lrd": 0.80},
        # Min-width read port, long channel
        {"Wp": 0.55, "Lp": 0.15, "Wn": 0.84, "Ln": 0.15, "Wax": 0.42,
         "Wrd": 0.42, "Lrd": 0.50},
        # Balanced: moderate everything
        {"Wp": 0.55, "Lp": 0.15, "Wn": 1.0, "Ln": 0.15, "Wax": 0.50,
         "Wrd": 1.0, "Lrd": 0.40},
        # Strong pull-down, small read port
        {"Wp": 0.55, "Lp": 0.15, "Wn": 1.5, "Ln": 0.15, "Wax": 0.55,
         "Wrd": 0.55, "Lrd": 0.50},
        # Previous optimizer's direction: long L, min Wrd
        {"Wp": 0.55, "Lp": 0.15, "Wn": 0.84, "Ln": 0.15, "Wax": 0.42,
         "Wrd": 0.42, "Lrd": 1.0},
        # Wider read port but longer channel
        {"Wp": 0.55, "Lp": 0.15, "Wn": 1.0, "Ln": 0.15, "Wax": 0.50,
         "Wrd": 1.5, "Lrd": 0.80},
        # Compact design
        {"Wp": 0.42, "Lp": 0.15, "Wn": 0.84, "Ln": 0.15, "Wax": 0.42,
         "Wrd": 0.60, "Lrd": 0.60},
    ]

    print("\n--- Phase 1: Expert designs ---")
    for i, p in enumerate(designs):
        r = measure_all(p)
        c = cost_fn(p)
        print(f"\nDesign {i}: cost={c:.1f}")
        print_status(r, "  ")
        if c < best_cost:
            best_cost = c
            best_params = dict(p)
            best_results = dict(r)
            save_best(p, r)
            print("  >>> New best!")
        if check_specs(r):
            print("  >>> ALL SPECS PASS!")

    eval_count = len(designs)
    print(f"\n  Best after expert designs: cost={best_cost:.1f}")
    if best_results:
        print_status(best_results, "  ")

    # Phase 2: Differential Evolution
    print(f"\n--- Phase 2: Differential Evolution ---")

    init_pop = []
    for d in designs:
        init_pop.append([d[n] for n in PARAM_NAMES])
    pop_size = 15
    rng = np.random.RandomState(42)
    while len(init_pop) < pop_size:
        x = []
        for lo, hi in PARAM_BOUNDS:
            x.append(np.exp(rng.uniform(np.log(lo), np.log(hi))))
        init_pop.append(x)
    init_pop = np.array(init_pop[:pop_size])

    result = differential_evolution(
        objective, PARAM_BOUNDS,
        maxiter=30, popsize=pop_size,
        seed=42, tol=0.01,
        mutation=(0.5, 1.5), recombination=0.8,
        init=init_pop, disp=False, workers=1,
    )

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  Optimization complete: {eval_count} evals in {elapsed:.0f}s")
    print(f"  Best cost: {best_cost:.1f}")
    print(f"\n  Parameters:")
    for n in PARAM_NAMES:
        print(f"    {n:>4s} = {best_params[n]:.4f} um")
    print(f"\n  Results:")
    print_status(best_results, "  ")
    all_pass = check_specs(best_results)
    print(f"\n  {'ALL SPECS PASS!' if all_pass else 'Some specs failing'}")
    print(f"{'='*60}")

    return best_params, best_results


if __name__ == "__main__":
    best_p, best_r = main()
