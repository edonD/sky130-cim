#!/usr/bin/env python3
"""Generate all verification plots for PWM driver."""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from evaluate import load_parameters, build_netlist, run_ngspice, parse_meas, measure_code, SUPPLY

os.makedirs("plots", exist_ok=True)
params = load_parameters()

# ============================================================
# TB1: All 16 codes — waveforms + linearity
# ============================================================
print("=== TB1: All 16 Codes ===")
pulse_widths = {}
rise_times = {}
fall_times = {}
waveforms = {}

for code in range(16):
    netlist = build_netlist(params, code)
    netlist = netlist.replace(
        "wrdata pwm_results.txt v(wl) v(pwm_in) v(clk)",
        f"wrdata pwm_waveform_{code}.dat v(wl) v(pwm_in) v(clk)")
    output = run_ngspice(netlist, tag=f"tb1_{code}")
    pw = parse_meas(output, "pw_rise")
    tr = parse_meas(output, "t_rise")
    tf = parse_meas(output, "t_fall")
    if code > 0 and pw:
        pulse_widths[code] = pw * 1e9
    if tr: rise_times[code] = tr * 1e9
    if tf: fall_times[code] = tf * 1e9
    datfile = f"pwm_waveform_{code}.dat"
    if os.path.exists(datfile):
        try:
            data = np.loadtxt(datfile)
            if data.ndim == 2 and data.shape[1] >= 2:
                waveforms[code] = data
        except: pass
    print(f"  Code {code:2d}: pw={pw*1e9:.2f}ns" if pw else f"  Code {code:2d}: pw=None")

# All codes waveform
fig, ax = plt.subplots(figsize=(12, 6))
colors = plt.cm.viridis(np.linspace(0, 1, 16))
for code in range(16):
    if code in waveforms:
        data = waveforms[code]
        t_ns = data[:, 0] * 1e9
        vwl = data[:, 1]
        mask = t_ns < 100
        ax.plot(t_ns[mask], vwl[mask], color=colors[code], label=f"Code {code}", linewidth=0.8)
ax.set_xlabel("Time (ns)"); ax.set_ylabel("Voltage (V)")
ax.set_title("PWM Driver Output — All 16 Input Codes")
ax.legend(fontsize=7, ncol=4, loc="upper right"); ax.grid(True, alpha=0.3)
ax.set_xlim(0, 100); ax.set_ylim(-0.1, 2.0)
plt.tight_layout(); plt.savefig("plots/pwm_all_codes.png", dpi=150); plt.close()

# Linearity
codes_list = sorted(pulse_widths.keys())
measured = [pulse_widths[c] for c in codes_list]
codes_arr = np.array(codes_list, dtype=float)
widths_arr = np.array(measured)
t_lsb_fit = np.sum(codes_arr * widths_arr) / np.sum(codes_arr**2)
ideal = codes_arr * t_lsb_fit

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 8))
ax1.plot(codes_list, measured, "bo-", label="Measured", markersize=6)
ax1.plot(codes_list, ideal, "r--", label=f"Ideal (T_LSB={t_lsb_fit:.3f} ns)")
ax1.set_xlabel("Input Code"); ax1.set_ylabel("Pulse Width (ns)")
ax1.set_title("PWM Driver Linearity"); ax1.legend(); ax1.grid(True, alpha=0.3)
errors = widths_arr - ideal
ax2.bar(codes_list, errors, color="orange", edgecolor="darkorange")
ax2.set_xlabel("Input Code"); ax2.set_ylabel("Error (ns)")
ax2.set_title(f"Pulse Width Error (Max = {np.max(np.abs(errors)):.4f} ns)")
ax2.grid(True, alpha=0.3)
plt.tight_layout(); plt.savefig("plots/pwm_linearity.png", dpi=150); plt.close()

# Error detail
full_scale = 15 * t_lsb_fit
max_error_pct = np.max(np.abs(errors)) / full_scale * 100
dnl_like = np.diff(widths_arr) / t_lsb_fit - 1
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 8))
ax1.bar(codes_list, errors, color="steelblue"); ax1.set_xlabel("Input Code")
ax1.set_ylabel("Error (ns)"); ax1.set_title(f"Max Error: {max_error_pct:.4f}%"); ax1.grid(True, alpha=0.3)
ax2.bar(codes_list[1:], dnl_like, color="salmon")
ax2.axhline(y=0.5, color="red", linestyle="--"); ax2.axhline(y=-0.5, color="red", linestyle="--")
ax2.set_xlabel("Input Code"); ax2.set_ylabel("DNL (LSB)"); ax2.set_title("Step Size Error"); ax2.grid(True, alpha=0.3)
plt.tight_layout(); plt.savefig("plots/pwm_error.png", dpi=150); plt.close()

# Edges
if 7 in waveforms:
    data = waveforms[7]; t_ns = data[:, 0] * 1e9; vwl = data[:, 1]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    rising_idx = np.where(np.diff(vwl > 0.9) > 0)[0]
    if len(rising_idx) > 0:
        c = rising_idx[0]; lo, hi = max(0,c-50), min(len(t_ns),c+50)
        ax1.plot(t_ns[lo:hi], vwl[lo:hi], "b-", linewidth=2)
        ax1.axhline(y=0.18, color="gray", linestyle="--"); ax1.axhline(y=1.62, color="gray", linestyle="--")
        ax1.set_xlabel("Time (ns)"); ax1.set_ylabel("V"); ax1.set_title(f"Rising Edge: tr={rise_times.get(7,0):.3f}ns"); ax1.grid(True, alpha=0.3)
    falling_idx = np.where(np.diff(vwl > 0.9) < 0)[0]
    if len(falling_idx) > 0:
        c = falling_idx[0]; lo, hi = max(0,c-50), min(len(t_ns),c+50)
        ax2.plot(t_ns[lo:hi], vwl[lo:hi], "r-", linewidth=2)
        ax2.axhline(y=0.18, color="gray", linestyle="--"); ax2.axhline(y=1.62, color="gray", linestyle="--")
        ax2.set_xlabel("Time (ns)"); ax2.set_ylabel("V"); ax2.set_title(f"Falling Edge: tf={fall_times.get(7,0):.3f}ns"); ax2.grid(True, alpha=0.3)
    plt.tight_layout(); plt.savefig("plots/pwm_edges.png", dpi=150); plt.close()

# Drive strength
loads = [10, 25, 50, 100, 200, 300, 500]
rt_load = []
for load in loads:
    nl = build_netlist(params, 7)
    nl = nl.replace("Cload wl 0 100f", f"Cload wl 0 {load}f")
    out = run_ngspice(nl, tag=f"load_{load}")
    tr = parse_meas(out, "t_rise")
    rt_load.append(tr * 1e9 if tr else None)

fig, ax = plt.subplots(figsize=(8, 5))
valid = [(l, r) for l, r in zip(loads, rt_load) if r]
if valid:
    lv, rv = zip(*valid)
    ax.plot(lv, rv, "bo-", linewidth=2, markersize=8)
    ax.axhline(y=0.5, color="red", linestyle="--", label="Spec (0.5ns)")
    ax.axvline(x=100, color="green", linestyle="--", alpha=0.5, label="Nominal (100fF)")
ax.set_xlabel("Load (fF)"); ax.set_ylabel("Rise Time (ns)")
ax.set_title("Drive Strength"); ax.legend(); ax.grid(True, alpha=0.3)
plt.tight_layout(); plt.savefig("plots/pwm_drive_strength.png", dpi=150); plt.close()

# Clean up dat files
for f in os.listdir('.'):
    if f.startswith('pwm_waveform_') and f.endswith('.dat'):
        os.remove(f)

print("All plots generated!")
