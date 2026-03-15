#!/usr/bin/env python3
"""Generate all verification plots for the bitcell."""

import os
import sys
import csv
import json
import tempfile
import subprocess
import re
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PLOTS_DIR = os.path.join(PROJECT_DIR, "plots")
NGSPICE = "ngspice"
os.makedirs(PLOTS_DIR, exist_ok=True)

# Load params
params = {}
with open(os.path.join(PROJECT_DIR, 'best_parameters.csv')) as f:
    reader = csv.DictReader(f)
    for row in reader:
        params[row['name']] = float(row['value'])

P = params
VS = 1.8

# Dark plot style
plt.rcParams.update({
    'figure.facecolor': '#1a1a2e', 'axes.facecolor': '#16213e',
    'axes.edgecolor': '#e94560', 'axes.labelcolor': '#eee',
    'text.color': '#eee', 'xtick.color': '#aaa', 'ytick.color': '#aaa',
    'grid.color': '#333', 'grid.alpha': 0.5, 'lines.linewidth': 1.5,
    'font.size': 10,
})


def run_spice(netlist, name):
    """Run ngspice and return output."""
    tmp = tempfile.mkdtemp(prefix="bc_plot_")
    path = os.path.join(tmp, f"{name}.cir")
    with open(path, "w") as f:
        f.write(netlist)
    try:
        r = subprocess.run([NGSPICE, "-b", path], capture_output=True, text=True,
                          timeout=60, cwd=PROJECT_DIR)
        return r.stdout + r.stderr
    except:
        return ""


def parse_wrdata(filename):
    """Parse ngspice wrdata output.
    Format: t1 sig1 t2 sig2 t3 sig3 ... (each signal has its own time column).
    Returns [time, sig1, sig2, sig3, ...] using the first time column.
    """
    filepath = os.path.join(PROJECT_DIR, filename)
    if not os.path.exists(filepath):
        return None
    rows = []
    with open(filepath) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            try:
                vals = [float(p) for p in parts]
                rows.append(vals)
            except ValueError:
                continue
    try:
        os.unlink(filepath)
    except:
        pass
    if not rows:
        return None
    arr = np.array(rows)
    ncols = arr.shape[1]
    # Extract: col 0 = time, col 1 = sig1, col 3 = sig2, col 5 = sig3, ...
    result = [arr[:, 0]]  # time from first pair
    for i in range(1, ncols, 2):
        result.append(arr[:, i])
    return result


# ============================================================
# TB1: Write & Store
# ============================================================
def plot_tb1():
    print("Generating TB1: Write & Store...")
    netlist = f"""* TB1: Write & Store
.lib "sky130_models/sky130.lib.spice" tt
Vdd vdd 0 DC {VS}
Vss vss 0 DC 0
XMPL q qb vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNL q qb vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMPR qb q vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNR qb q vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMAXL blw wwl q vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMAXR blbw wwl qb vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMRD1 bl q mid_rd vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
XMRD2 mid_rd wl vss vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
Vwl wl 0 DC 0
Vbl bl 0 DC {VS}
* Write 1: BLW=VDD, BLBW=0, WWL high for 5ns
* Then write 0: BLW=0, BLBW=VDD, WWL high at 15ns for 5ns
Vblw blw 0 PWL(0 {VS} 5n {VS} 5.1n 0 15n 0 15.1n 0 20n 0 20.1n 0)
Vblbw blbw 0 PWL(0 0 5n 0 5.1n 0 15n 0 15.1n {VS} 20n {VS} 20.1n 0)
Vwwl wwl 0 PWL(0 {VS} 5n {VS} 5.1n 0 15n 0 15.1n {VS} 20n {VS} 20.1n 0)
.options reltol=0.003 method=gear
.temp 27
.control
tran 0.05n 30n
wrdata tb1_data v(wwl) v(blw) v(blbw) v(q) v(qb)
echo "RESULT_DONE"
.endc
.end
"""
    run_spice(netlist, "tb1")
    data = parse_wrdata("tb1_data")
    if data is None:
        print("  TB1 failed!")
        return

    t = data[0] * 1e9  # Convert to ns
    wwl, blw, blbw, q, qb = data[1], data[2], data[3], data[4], data[5]

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)

    axes[0].plot(t, wwl, color='#e94560', label='WWL')
    axes[0].set_ylabel('Voltage (V)')
    axes[0].set_title('TB1: Write & Store Waveforms')
    axes[0].legend(loc='upper right', fontsize=8)
    axes[0].set_ylim(-0.1, 2.0)
    axes[0].grid(True)
    axes[0].annotate('Write 1', xy=(2.5, 1.9), fontsize=9, color='yellow', ha='center')
    axes[0].annotate('Write 0', xy=(17.5, 1.9), fontsize=9, color='yellow', ha='center')

    axes[1].plot(t, blw, color='#0f3460', label='BLW')
    axes[1].plot(t, blbw, color='#e94560', label='BLBW')
    axes[1].set_ylabel('Voltage (V)')
    axes[1].legend(loc='upper right', fontsize=8)
    axes[1].set_ylim(-0.1, 2.0)
    axes[1].grid(True)

    axes[2].plot(t, q, color='#0f0', label='Q', linewidth=2)
    axes[2].plot(t, qb, color='#ff6600', label='QB', linewidth=2)
    axes[2].set_ylabel('Voltage (V)')
    axes[2].set_xlabel('Time (ns)')
    axes[2].legend(loc='center right', fontsize=8)
    axes[2].set_ylim(-0.1, 2.0)
    axes[2].grid(True)
    axes[2].annotate('Q=1 stored', xy=(10, 1.7), fontsize=9, color='#0f0')
    axes[2].annotate('Q=0 stored', xy=(25, 0.2), fontsize=9, color='#0f0')

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'write_waveforms.png'), dpi=150)
    plt.close()
    print("  Saved write_waveforms.png")


# ============================================================
# TB2: Read Current (Weight=1)
# ============================================================
def plot_tb2():
    print("Generating TB2: Read Current (W=1)...")
    netlist = f"""* TB2: Read current
.lib "sky130_models/sky130.lib.spice" tt
Vdd vdd 0 DC {VS}
Vss vss 0 DC 0
XMPL q qb vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNL q qb vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMPR qb q vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNR qb q vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMAXL blw wwl q vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMAXR blbw wwl qb vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMRD1 bl q mid_rd vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
XMRD2 mid_rd wl vss vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
Vblw blw 0 PWL(0 {VS} 5n {VS} 5.1n 0)
Vblbw blbw 0 PWL(0 0 5n 0 5.1n 0)
Vwwl wwl 0 PWL(0 {VS} 5n {VS} 5.1n 0)
Vwl wl 0 PWL(0 0 10n 0 10.1n {VS})
Vbl bl 0 DC {VS}
.options reltol=0.003 method=gear
.temp 27
.control
tran 0.02n 30n
wrdata tb2_data v(wl) v(bl) i(Vbl)
echo "RESULT_DONE"
.endc
.end
"""
    run_spice(netlist, "tb2")
    data = parse_wrdata("tb2_data")
    if data is None:
        print("  TB2 failed!")
        return

    t = data[0] * 1e9
    wl, bl, ibl = data[1], data[2], data[3]
    i_ua = np.abs(ibl) * 1e6

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)

    axes[0].plot(t, wl, color='#e94560', label='WL')
    axes[0].set_ylabel('Voltage (V)')
    axes[0].set_title('TB2: Read Current (Weight=1)')
    axes[0].legend(fontsize=8)
    axes[0].grid(True)
    axes[0].annotate('WL rises', xy=(10.05, 1.0), fontsize=9, color='yellow')

    axes[1].plot(t, bl, color='#0f3460', label='BL voltage')
    axes[1].set_ylabel('BL Voltage (V)')
    axes[1].legend(fontsize=8)
    axes[1].grid(True)

    axes[2].plot(t, i_ua, color='#0f0', label='|I(BL)| (read current)', linewidth=2)
    axes[2].axhline(y=5.0, color='yellow', linestyle='--', alpha=0.7, label='Spec: 5 uA')
    axes[2].set_ylabel('Current (uA)')
    axes[2].set_xlabel('Time (ns)')
    axes[2].legend(fontsize=8)
    axes[2].grid(True)

    # Find steady state current
    steady_i = np.mean(i_ua[t > 20])
    axes[2].annotate(f'I_read = {steady_i:.1f} uA', xy=(22, steady_i + 2),
                     fontsize=10, color='#0f0', fontweight='bold')

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'read_current_w1.png'), dpi=150)
    plt.close()
    print(f"  Saved read_current_w1.png (I_read = {steady_i:.1f} uA)")


# ============================================================
# TB3: Leakage (Weight=0)
# ============================================================
def plot_tb3():
    print("Generating TB3: Leakage (W=0)...")
    netlist = f"""* TB3: Leakage
.lib "sky130_models/sky130.lib.spice" tt
Vdd vdd 0 DC {VS}
Vss vss 0 DC 0
XMPL q qb vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNL q qb vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMPR qb q vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNR qb q vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMAXL blw wwl q vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMAXR blbw wwl qb vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMRD1 bl q mid_rd vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
XMRD2 mid_rd wl vss vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
Vblw blw 0 PWL(0 0 5n 0 5.1n 0)
Vblbw blbw 0 PWL(0 {VS} 5n {VS} 5.1n 0)
Vwwl wwl 0 PWL(0 {VS} 5n {VS} 5.1n 0)
Vwl wl 0 PWL(0 0 10n 0 10.1n {VS})
Vbl bl 0 DC {VS}
.options reltol=0.003 method=gear
.temp 27
.control
tran 0.02n 30n
wrdata tb3_data v(wl) v(bl) i(Vbl)
echo "RESULT_DONE"
.endc
.end
"""
    run_spice(netlist, "tb3")
    data = parse_wrdata("tb3_data")
    if data is None:
        print("  TB3 failed!")
        return

    t = data[0] * 1e9
    wl, bl, ibl = data[1], data[2], data[3]
    i_na = np.abs(ibl) * 1e9

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)

    axes[0].plot(t, wl, color='#e94560', label='WL')
    axes[0].set_ylabel('Voltage (V)')
    axes[0].set_title('TB3: Leakage Current (Weight=0)')
    axes[0].legend(fontsize=8)
    axes[0].grid(True)

    axes[1].plot(t, bl, color='#0f3460', label='BL voltage')
    axes[1].set_ylabel('BL Voltage (V)')
    axes[1].legend(fontsize=8)
    axes[1].grid(True)

    axes[2].plot(t, i_na, color='#ff6600', label='|I(BL)| (leakage)', linewidth=2)
    axes[2].axhline(y=100.0, color='yellow', linestyle='--', alpha=0.7, label='Spec: 100 nA')
    axes[2].set_ylabel('Current (nA)')
    axes[2].set_xlabel('Time (ns)')
    axes[2].legend(fontsize=8)
    axes[2].grid(True)

    steady_i = np.mean(i_na[t > 20])
    axes[2].annotate(f'I_leak = {steady_i:.3f} nA', xy=(22, max(steady_i + 5, 10)),
                     fontsize=10, color='#ff6600', fontweight='bold')

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'read_current_w0.png'), dpi=150)
    plt.close()
    print(f"  Saved read_current_w0.png (I_leak = {steady_i:.3f} nA)")


# ============================================================
# TB5: SNM Butterfly Curve
# ============================================================
def plot_tb5():
    print("Generating TB5: SNM Butterfly Curve...")
    netlist = f"""* SNM VTC
.lib "sky130_models/sky130.lib.spice" tt
Vdd vdd 0 DC {VS}
Vss vss 0 DC 0
Vin in 0 DC 0
XMP out in vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMN out in vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
.options reltol=0.001
.temp 27
.control
dc Vin 0 {VS} 0.002
wrdata tb5_vtc v(out)
echo "RESULT_DONE"
.endc
.end
"""
    run_spice(netlist, "tb5")
    data = parse_wrdata("tb5_vtc")
    if data is None:
        print("  TB5 failed!")
        return

    vin = data[0]
    vout = data[1]

    # Compute butterfly curve
    f_inv = np.interp(vin, vout[::-1], vin[::-1])
    gap = vout - f_inv

    margin = max(5, len(vin) // 10)
    trip_idx = margin + np.argmin(np.abs(gap[margin:-margin]))

    upper = gap[:trip_idx]
    lower = gap[trip_idx:]
    snm_upper = np.max(upper) if np.any(upper > 0) else 0
    snm_lower = np.max(-lower) if np.any(lower < 0) else 0
    snm_v = min(snm_upper, snm_lower) / np.sqrt(2)
    snm_mv = snm_v * 1000

    fig, ax = plt.subplots(figsize=(8, 8))

    # Plot VTC and its mirror
    ax.plot(vin, vout, color='#0f0', linewidth=2, label='VTC: $V_{out}$ = f($V_{in}$)')
    ax.plot(vout, vin, color='#e94560', linewidth=2, label='Mirror: $V_{in}$ = f($V_{out}$)')
    ax.plot([0, VS], [0, VS], '--', color='#666', alpha=0.5, label='y = x')

    # Draw SNM square in upper eye
    # Find max gap location for upper eye
    if snm_upper > 0:
        max_idx = np.argmax(upper)
        x0 = vin[max_idx]
        y0 = f_inv[max_idx]
        side = snm_v * np.sqrt(2)
        rect = plt.Rectangle((x0, y0), side / np.sqrt(2), side / np.sqrt(2),
                              linewidth=2, edgecolor='yellow', facecolor='yellow',
                              alpha=0.15, label=f'SNM = {snm_mv:.0f} mV')
        ax.add_patch(rect)

    ax.set_xlabel('$V_A$ (V)')
    ax.set_ylabel('$V_B$ (V)')
    ax.set_title(f'TB5: SNM Butterfly Curve — SNM = {snm_mv:.0f} mV')
    ax.set_xlim(-0.05, VS + 0.05)
    ax.set_ylim(-0.05, VS + 0.05)
    ax.set_aspect('equal')
    ax.legend(fontsize=9, loc='center right')
    ax.grid(True)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'snm_butterfly.png'), dpi=150)
    plt.close()
    print(f"  Saved snm_butterfly.png (SNM = {snm_mv:.0f} mV)")


# ============================================================
# TB6: Read Disturb
# ============================================================
def plot_tb6():
    print("Generating TB6: Read Disturb...")
    # 100 read cycles (1000 is too many for transient sim)
    n_cycles = 100
    period = 2  # ns per cycle (1ns high, 1ns low)
    total_time = 10 + n_cycles * period  # 10ns for write, then cycles

    # Build WL PWL string for cycling
    wl_points = [f"0 0 10n 0"]
    for i in range(n_cycles):
        t_start = 10 + i * period
        t_high = t_start + 0.1
        t_fall = t_start + period / 2
        t_fall_done = t_fall + 0.1
        wl_points.append(f"{t_high}n {VS} {t_fall}n {VS} {t_fall_done}n 0")

    wl_pwl = " ".join(wl_points)

    netlist = f"""* TB6: Read Disturb
.lib "sky130_models/sky130.lib.spice" tt
Vdd vdd 0 DC {VS}
Vss vss 0 DC 0
XMPL q qb vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNL q qb vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMPR qb q vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNR qb q vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMAXL blw wwl q vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMAXR blbw wwl qb vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMRD1 bl q mid_rd vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
XMRD2 mid_rd wl vss vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
Vblw blw 0 PWL(0 {VS} 5n {VS} 5.1n 0)
Vblbw blbw 0 PWL(0 0 5n 0 5.1n 0)
Vwwl wwl 0 PWL(0 {VS} 5n {VS} 5.1n 0)
Vwl wl 0 PWL({wl_pwl})
Vbl bl 0 DC {VS}
.options reltol=0.003 method=gear
.temp 27
.control
tran 0.1n {total_time}n
wrdata tb6_data v(q) v(qb) v(wl)
echo "RESULT_DONE"
.endc
.end
"""
    run_spice(netlist, "tb6")
    data = parse_wrdata("tb6_data")
    if data is None:
        print("  TB6 failed!")
        return

    t = data[0] * 1e9
    q, qb, wl = data[1], data[2], data[3]

    fig, axes = plt.subplots(2, 1, figsize=(14, 6), sharex=True)

    axes[0].plot(t, q, color='#0f0', label='Q', linewidth=1)
    axes[0].plot(t, qb, color='#ff6600', label='QB', linewidth=1)
    axes[0].set_ylabel('Voltage (V)')
    axes[0].set_title(f'TB6: Read Disturb — {n_cycles} Read Cycles')
    axes[0].legend(fontsize=8)
    axes[0].set_ylim(-0.1, 2.0)
    axes[0].grid(True)

    # Check if cell held
    q_final = q[-1]
    qb_final = qb[-1]
    held = q_final > 0.8 * VS and qb_final < 0.2 * VS
    axes[0].annotate(f'Cell {"HELD" if held else "FLIPPED"}: Q={q_final:.2f}V, QB={qb_final:.2f}V',
                     xy=(t[-1] * 0.7, 1.5), fontsize=10,
                     color='#0f0' if held else '#e94560', fontweight='bold')

    axes[1].plot(t, wl, color='#e94560', label='WL', linewidth=0.5)
    axes[1].set_ylabel('WL (V)')
    axes[1].set_xlabel('Time (ns)')
    axes[1].legend(fontsize=8)
    axes[1].grid(True)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'read_disturb.png'), dpi=150)
    plt.close()
    print(f"  Saved read_disturb.png (Cell {'held' if held else 'FLIPPED'})")


# ============================================================
# TB8: Pulse-Width Modulation Response
# ============================================================
def plot_tb8():
    print("Generating TB8: PWM Response...")
    pulse_widths = [1, 2, 5, 10, 20]
    charges = []

    for pw in pulse_widths:
        netlist = f"""* TB8: PWM pulse width={pw}ns
.lib "sky130_models/sky130.lib.spice" tt
Vdd vdd 0 DC {VS}
Vss vss 0 DC 0
XMPL q qb vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNL q qb vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMPR qb q vdd vdd sky130_fd_pr__pfet_01v8 W={P['Wp']}u L={P['Lp']}u nf=1
XMNR qb q vss vss sky130_fd_pr__nfet_01v8 W={P['Wn']}u L={P['Ln']}u nf=1
XMAXL blw wwl q vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMAXR blbw wwl qb vss sky130_fd_pr__nfet_01v8 W={P['Wax']}u L=0.15u nf=1
XMRD1 bl q mid_rd vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
XMRD2 mid_rd wl vss vss sky130_fd_pr__nfet_01v8 W={P['Wrd']}u L={P['Lrd']}u nf=1
Vblw blw 0 PWL(0 {VS} 5n {VS} 5.1n 0)
Vblbw blbw 0 PWL(0 0 5n 0 5.1n 0)
Vwwl wwl 0 PWL(0 {VS} 5n {VS} 5.1n 0)
Vwl wl 0 PWL(0 0 10n 0 10.1n {VS} {10+pw}n {VS} {10+pw+0.1}n 0)
Vbl bl 0 DC {VS}
.options reltol=0.003 method=gear
.temp 27
.control
tran 0.05n {15+pw+5}n
wrdata tb8_pw{pw} i(Vbl)
echo "RESULT_DONE"
.endc
.end
"""
        run_spice(netlist, f"tb8_pw{pw}")
        data = parse_wrdata(f"tb8_pw{pw}")
        if data is None:
            charges.append(0)
            continue

        t = data[0]
        current = np.abs(data[1])
        # Integrate current during pulse (10ns to 10+pw ns)
        mask = (t >= 10e-9) & (t <= (10 + pw) * 1e-9)
        if np.any(mask):
            charge = np.trapz(current[mask], t[mask])
            charges.append(charge * 1e15)  # Convert to fC
        else:
            charges.append(0)

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(pulse_widths, charges, 'o-', color='#0f0', linewidth=2, markersize=8)

    # Fit line for linearity check
    if len(charges) >= 2 and all(c > 0 for c in charges):
        coeffs = np.polyfit(pulse_widths, charges, 1)
        fit_x = np.linspace(0, max(pulse_widths), 100)
        fit_y = np.polyval(coeffs, fit_x)
        ax.plot(fit_x, fit_y, '--', color='yellow', alpha=0.7, label=f'Linear fit (slope={coeffs[0]:.2f} fC/ns)')

        # R^2
        y_pred = np.polyval(coeffs, pulse_widths)
        ss_res = np.sum((np.array(charges) - y_pred) ** 2)
        ss_tot = np.sum((np.array(charges) - np.mean(charges)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        ax.annotate(f'R² = {r2:.4f}', xy=(0.05, 0.95), xycoords='axes fraction',
                    fontsize=12, color='yellow', fontweight='bold')

    ax.set_xlabel('Pulse Width (ns)')
    ax.set_ylabel('Charge Deposited (fC)')
    ax.set_title('TB8: Charge vs Pulse Width — CIM Linearity Test')
    ax.legend(fontsize=9)
    ax.grid(True)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'charge_vs_pulsewidth.png'), dpi=150)
    plt.close()
    print(f"  Saved charge_vs_pulsewidth.png")


# ============================================================
# PVT Corner Plot
# ============================================================
def plot_pvt():
    print("Generating PVT corner plot...")
    pvt_file = os.path.join(PROJECT_DIR, 'pvt_results.json')
    if not os.path.exists(pvt_file):
        print("  No pvt_results.json found, skipping")
        return

    with open(pvt_file) as f:
        pvt = json.load(f)

    results = pvt['results']
    labels = [f"{r['corner']}\n{r['temp']}C\n{r['supply']}V" for r in results]

    fig, axes = plt.subplots(2, 2, figsize=(18, 10))

    # I_read
    vals = [r['i_read_ua'] for r in results]
    colors = ['#0f0' if v >= 5 else '#e94560' for v in vals]
    axes[0, 0].bar(range(len(vals)), vals, color=colors, alpha=0.8)
    axes[0, 0].axhline(y=5.0, color='yellow', linestyle='--', label='Spec: > 5 uA')
    axes[0, 0].set_ylabel('I_read (uA)')
    axes[0, 0].set_title('Read Current across PVT')
    axes[0, 0].legend(fontsize=8)
    axes[0, 0].grid(True)

    # I_leak
    vals = [r['i_leak_na'] for r in results]
    colors = ['#0f0' if v <= 100 else '#e94560' for v in vals]
    axes[0, 1].bar(range(len(vals)), vals, color=colors, alpha=0.8)
    axes[0, 1].axhline(y=100, color='yellow', linestyle='--', label='Spec: < 100 nA')
    axes[0, 1].set_ylabel('I_leak (nA)')
    axes[0, 1].set_title('Leakage Current across PVT')
    axes[0, 1].legend(fontsize=8)
    axes[0, 1].grid(True)

    # ON/OFF
    vals = [r['on_off_ratio'] for r in results]
    colors = ['#0f0' if v >= 100 else '#e94560' for v in vals]
    axes[1, 0].bar(range(len(vals)), np.log10(np.maximum(vals, 1)), color=colors, alpha=0.8)
    axes[1, 0].axhline(y=2.0, color='yellow', linestyle='--', label='Spec: > 100 (10²)')
    axes[1, 0].set_ylabel('log₁₀(ON/OFF)')
    axes[1, 0].set_title('ON/OFF Ratio across PVT (log scale)')
    axes[1, 0].legend(fontsize=8)
    axes[1, 0].grid(True)

    # SNM
    vals = [r['snm_mv'] for r in results]
    colors = ['#0f0' if v >= 100 else '#e94560' for v in vals]
    axes[1, 1].bar(range(len(vals)), vals, color=colors, alpha=0.8)
    axes[1, 1].axhline(y=100, color='yellow', linestyle='--', label='Spec: > 100 mV')
    axes[1, 1].set_ylabel('SNM (mV)')
    axes[1, 1].set_title('Static Noise Margin across PVT')
    axes[1, 1].legend(fontsize=8)
    axes[1, 1].grid(True)

    for ax in axes.flat:
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, fontsize=3.5, rotation=45)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, 'pvt_corners.png'), dpi=150)
    plt.close()
    print("  Saved pvt_corners.png")


# ============================================================
# Run all
# ============================================================
if __name__ == "__main__":
    plot_tb1()
    plot_tb2()
    plot_tb3()
    plot_tb5()
    plot_tb6()
    plot_tb8()
    plot_pvt()
    print("\nAll plots generated!")
