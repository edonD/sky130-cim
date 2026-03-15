#!/usr/bin/env python3
"""
Generate all verification plots for CIM Tile Integration.

Integration Testbenches:
  TB1: End-to-End MVM waveforms
  TB2: MNIST Single Digit
  TB3: MNIST Accuracy + Confusion Matrix
  TB4: MNIST Examples Grid
  TB5: Analog vs Digital Comparison
"""

import numpy as np
import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

BLOCK_DIR = Path(__file__).parent.resolve()
PLOTS_DIR = BLOCK_DIR / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BLOCK_DIR))
from evaluate import (
    load_upstream_measurements, CIMTileBehavioral,
    mnist_inference_ideal
)
from train_mnist import load_mnist

# Consistent style
plt.rcParams.update({
    'font.size': 10,
    'axes.titlesize': 12,
    'axes.labelsize': 10,
    'figure.dpi': 150,
})


def load_everything():
    """Load measurements, tile model, weights, and MNIST data."""
    measurements = load_upstream_measurements()
    tile = CIMTileBehavioral(measurements, max_input_value=1)
    w1 = np.load(str(BLOCK_DIR / "w1.npy"))
    w2 = np.load(str(BLOCK_DIR / "w2.npy"))
    _, _, X_test, y_test = load_mnist(str(BLOCK_DIR / "mnist_data"))
    return measurements, tile, w1, w2, X_test, y_test


# =========================================================================
# TB1: End-to-End MVM Waveforms
# =========================================================================
def plot_e2e_waveforms(tile, w1):
    """Show the complete signal chain for a single MVM operation."""
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)

    # Use first chunk of w1 with a sample input
    W = w1[:8, :8]  # 8x8 submatrix for clarity
    w_pos = (W + 1) / 2
    x_hw = np.array([1, 0, 1, 1, 0, 1, 0, 1], dtype=float)  # binary input

    # Simulate timing
    t_pre = 5  # ns
    t_lsb = tile.t_lsb_ns
    t_settle = 20
    t_convert = tile.adc_conv_time_ns
    total = t_pre + t_lsb + t_settle + t_convert

    t = np.linspace(0, total, 2000)

    # Phase 1: Precharge (rst high)
    rst = np.where(t < t_pre, 1.8, 0.0)

    # Phase 2: Wordline pulses (one T_LSB for active inputs)
    wl = np.zeros((8, len(t)))
    for i in range(8):
        if x_hw[i] > 0:
            wl[i] = np.where((t >= t_pre) & (t < t_pre + t_lsb), 1.8, 0.0)

    # Phase 3: Bitline voltages
    bl = np.ones((8, len(t))) * 1.8
    for j in range(8):
        dot_j = x_hw @ w_pos[:, j]
        discharge = dot_j * tile.v_step_per_unit
        for k in range(len(t)):
            if t[k] >= t_pre:
                progress = min((t[k] - t_pre) / t_lsb, 1.0)
                bl[j, k] = 1.8 - discharge * progress
            if t[k] >= t_pre + t_lsb:
                bl[j, k] = 1.8 - discharge

    # Phase 4: ADC conversion
    adc_start = t_pre + t_lsb + t_settle
    adc_clk = np.zeros(len(t))
    for cyc in range(6):
        t_cyc_start = adc_start + cyc * (t_convert / 6)
        t_cyc_mid = t_cyc_start + (t_convert / 12)
        adc_clk += np.where((t >= t_cyc_start) & (t < t_cyc_mid), 1.8, 0.0)

    # Plot 1: Control signals
    ax = axes[0]
    ax.plot(t, rst, 'b-', linewidth=1.5, label='RST (precharge)')
    ax.plot(t, adc_clk, 'r-', linewidth=1, alpha=0.7, label='ADC CLK')
    ax.set_ylabel('Voltage (V)')
    ax.set_title('TB1: End-to-End MVM Signal Chain (8x8)')
    ax.legend(loc='upper right', fontsize=8)
    ax.set_ylim(-0.2, 2.2)
    ax.axvline(t_pre, color='gray', linestyle='--', alpha=0.3)
    ax.axvline(t_pre + t_lsb, color='gray', linestyle='--', alpha=0.3)
    ax.axvline(adc_start, color='gray', linestyle='--', alpha=0.3)
    ax.text(t_pre/2, 2.0, 'PRE', ha='center', fontsize=8)
    ax.text(t_pre + t_lsb/2, 2.0, 'COMP', ha='center', fontsize=8)
    ax.text(t_pre + t_lsb + t_settle/2, 2.0, 'SETTLE', ha='center', fontsize=8)
    ax.text(adc_start + t_convert/2, 2.0, 'ADC CONVERT', ha='center', fontsize=8)

    # Plot 2: Wordlines
    ax = axes[1]
    colors = plt.cm.tab10(np.linspace(0, 1, 8))
    for i in range(8):
        ax.plot(t, wl[i] + i*0.1, color=colors[i], linewidth=1,
                label=f'WL[{i}] x={int(x_hw[i])}')
    ax.set_ylabel('Voltage (V)')
    ax.legend(loc='upper right', fontsize=7, ncol=2)
    ax.set_ylim(-0.2, 2.5)

    # Plot 3: Bitlines
    ax = axes[2]
    for j in range(8):
        dot_j = x_hw @ w_pos[:, j]
        ax.plot(t, bl[j], color=colors[j], linewidth=1.5,
                label=f'BL[{j}] dot={dot_j:.0f}')
    ax.set_ylabel('Voltage (V)')
    ax.legend(loc='upper right', fontsize=7, ncol=2)
    ax.set_ylim(-0.1, 2.0)
    ax.axhline(1.8, color='gray', linestyle=':', alpha=0.3)

    # Plot 4: ADC output codes
    ax = axes[3]
    codes, _ = tile.mvm(np.pad(W, ((0, 56), (0, 56)))[:64, :64],
                         np.pad(x_hw, (0, 56))[:64], add_noise=False)
    for j in range(8):
        ax.bar(j, codes[j], color=colors[j], alpha=0.8)
        ax.text(j, codes[j]+0.5, str(codes[j]), ha='center', fontsize=8)
    ax.set_xlabel('Time (ns) / Column Index')
    ax.set_ylabel('ADC Code')
    ax.set_title('ADC Output Codes')

    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "e2e_waveforms.png"), dpi=150)
    plt.close()
    print("  Saved plots/e2e_waveforms.png")


# =========================================================================
# TB2: MNIST Single Digit
# =========================================================================
def plot_mnist_single_digit(tile, w1, w2, X_test, y_test):
    """Show processing of a single MNIST digit through the full pipeline."""
    fig = plt.figure(figsize=(14, 8))
    gs = gridspec.GridSpec(2, 4, figure=fig, hspace=0.4, wspace=0.4)

    idx = 0  # First test image (typically a '7')
    x = X_test[idx]
    x_hw = (x > 0.5).astype(float)
    x_real = 2*x_hw - 1
    w1_pos = (w1+1)/2
    w2_pos = (w2+1)/2

    # Show input image
    ax = fig.add_subplot(gs[0, 0])
    ax.imshow(x.reshape(28, 28), cmap='gray')
    ax.set_title(f'Input (label={y_test[idx]})')
    ax.axis('off')

    # Show binarized input
    ax = fig.add_subplot(gs[0, 1])
    ax.imshow(x_hw.reshape(28, 28), cmap='gray')
    ax.set_title(f'Binarized ({int(x_hw.sum())} active)')
    ax.axis('off')

    # Run layer 1
    accumulated = np.zeros(64, dtype=float)
    for c in range(13):
        start = c*64
        end = min(start+64, 784)
        x_ch = x_hw[start:end]
        w_ch = w1[start:end, :]
        wp_ch = w1_pos[start:end, :]
        actual_len = len(x_ch)
        if actual_len < 64:
            x_pad = np.zeros(64); x_pad[:actual_len] = x_ch
            w_pad = np.zeros((64,64)); w_pad[:actual_len,:] = w_ch
            wp_pad = np.zeros((64,64)); wp_pad[:actual_len,:] = wp_ch
            x_ch, w_ch, wp_ch = x_pad, w_pad, wp_pad
        codes, _ = tile.mvm(w_ch, x_ch, add_noise=False)
        dot_est = codes.astype(float) / tile.adc_gain
        signed = 4.0*dot_est - 2.0*x_ch.sum() - 2.0*wp_ch.sum(axis=0) + actual_len
        accumulated += signed

    # Show layer 1 pre-activation
    ax = fig.add_subplot(gs[0, 2])
    ax.bar(range(64), accumulated, color=np.where(accumulated >= 0, 'steelblue', 'coral'),
           alpha=0.8, width=1.0)
    ax.axhline(0, color='black', linewidth=0.5)
    ax.set_title('Layer 1 Pre-activation')
    ax.set_xlabel('Hidden unit')
    ax.set_ylabel('Value')

    # Layer 1 activation
    hidden = np.where(accumulated >= 0, 1.0, -1.0)

    ax = fig.add_subplot(gs[0, 3])
    ax.bar(range(64), hidden, color=np.where(hidden > 0, 'green', 'red'),
           alpha=0.8, width=1.0)
    ax.set_title('Layer 1 Activation (sign)')
    ax.set_xlabel('Hidden unit')
    ax.set_ylim(-1.5, 1.5)

    # Layer 2
    w2_pad = np.zeros((64, 64))
    w2_pad[:64, :10] = w2
    w2_pos_pad = np.zeros((64, 64))
    w2_pos_pad[:64, :10] = w2_pos
    h_hw = np.where(hidden > 0, 1.0, 0.0)
    codes2, _ = tile.mvm(w2_pad, h_hw, add_noise=False)
    dot_est2 = codes2.astype(float) / tile.adc_gain
    signed_out = 4.0*dot_est2 - 2.0*h_hw.sum() - 2.0*w2_pos_pad.sum(axis=0) + 64
    output = signed_out[:10]
    pred = np.argmax(output)

    ax = fig.add_subplot(gs[1, :2])
    colors = ['green' if i == y_test[idx] else 'coral' if i == pred and pred != y_test[idx]
              else 'steelblue' for i in range(10)]
    ax.bar(range(10), output, color=colors, alpha=0.8)
    ax.set_xticks(range(10))
    ax.set_xlabel('Digit class')
    ax.set_ylabel('Output score')
    ax.set_title(f'Layer 2 Output (predicted: {pred}, correct: {y_test[idx]})')

    # Ideal comparison
    z1_ideal = x_real @ w1
    a1_ideal = np.where(z1_ideal >= 0, 1.0, -1.0)
    z2_ideal = a1_ideal @ w2
    ax = fig.add_subplot(gs[1, 2:])
    ax.bar(range(10), z2_ideal, color='steelblue', alpha=0.5, label='Ideal')
    ax.bar(range(10), output, color='coral', alpha=0.5, label='CIM Tile')
    ax.set_xticks(range(10))
    ax.set_xlabel('Digit class')
    ax.set_ylabel('Output score')
    ax.set_title('CIM vs Ideal Comparison')
    ax.legend()

    plt.suptitle(f'TB2: MNIST Single Digit Classification — Image #{idx}', fontsize=14)
    plt.savefig(str(PLOTS_DIR / "mnist_single_digit.png"), dpi=150, bbox_inches='tight')
    plt.close()
    print("  Saved plots/mnist_single_digit.png")


# =========================================================================
# TB3: MNIST Accuracy + Confusion Matrix
# =========================================================================
def plot_mnist_accuracy_and_confusion(tile, w1, w2, X_test, y_test, n_images=1000):
    """Run MNIST inference and plot accuracy curve + confusion matrix."""
    w1_pos = (w1+1)/2
    w2_pos = (w2+1)/2

    predictions = []
    correct_running = 0
    accuracies = []

    for idx in range(n_images):
        x = X_test[idx]
        x_hw = (x > 0.5).astype(float)

        # Layer 1
        accumulated = np.zeros(64, dtype=float)
        for c in range(13):
            start = c*64
            end = min(start+64, 784)
            x_ch = x_hw[start:end]
            w_ch = w1[start:end, :]
            wp_ch = w1_pos[start:end, :]
            actual_len = len(x_ch)
            if actual_len < 64:
                x_pad = np.zeros(64); x_pad[:actual_len] = x_ch
                w_pad = np.zeros((64,64)); w_pad[:actual_len,:] = w_ch
                wp_pad = np.zeros((64,64)); wp_pad[:actual_len,:] = wp_ch
                x_ch, w_ch, wp_ch = x_pad, w_pad, wp_pad
            codes, _ = tile.mvm(w_ch, x_ch, add_noise=True)
            dot_est = codes.astype(float) / tile.adc_gain
            signed = 4.0*dot_est - 2.0*x_ch.sum() - 2.0*wp_ch.sum(axis=0) + actual_len
            accumulated += signed

        hidden = np.where(accumulated >= 0, 1.0, -1.0)

        # Layer 2
        w2_pad = np.zeros((64,64)); w2_pad[:64,:10] = w2
        w2pp = np.zeros((64,64)); w2pp[:64,:10] = w2_pos
        h_hw = np.where(hidden > 0, 1.0, 0.0)
        codes2, _ = tile.mvm(w2_pad, h_hw, add_noise=True)
        dot2 = codes2.astype(float) / tile.adc_gain
        out = 4.0*dot2 - 2.0*h_hw.sum() - 2.0*w2pp.sum(axis=0) + 64
        pred = np.argmax(out[:10])
        predictions.append(pred)

        if pred == y_test[idx]:
            correct_running += 1
        accuracies.append(correct_running / (idx + 1) * 100)

    predictions = np.array(predictions)
    final_acc = accuracies[-1]

    # Plot 1: Accuracy vs number of images
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    ax = axes[0]
    ax.plot(range(1, n_images+1), accuracies, 'b-', linewidth=1)
    ax.axhline(85, color='red', linestyle='--', alpha=0.7, label='Target (85%)')
    ax.axhline(final_acc, color='green', linestyle=':', alpha=0.7,
               label=f'Final ({final_acc:.1f}%)')
    ax.set_xlabel('Number of test images')
    ax.set_ylabel('Accuracy (%)')
    ax.set_title(f'MNIST Classification Accuracy ({n_images} images)')
    ax.legend()
    ax.set_ylim(75, 100)
    ax.grid(alpha=0.3)

    # Plot 2: Confusion matrix
    ax = axes[1]
    conf = np.zeros((10, 10), dtype=int)
    for i in range(n_images):
        conf[y_test[i], predictions[i]] += 1

    im = ax.imshow(conf, cmap='Blues')
    ax.set_xlabel('Predicted')
    ax.set_ylabel('True')
    ax.set_title('Confusion Matrix')
    ax.set_xticks(range(10))
    ax.set_yticks(range(10))
    for i in range(10):
        for j in range(10):
            if conf[i, j] > 0:
                color = 'white' if conf[i, j] > conf.max()/2 else 'black'
                ax.text(j, i, str(conf[i, j]), ha='center', va='center',
                        fontsize=7, color=color)
    plt.colorbar(im, ax=ax, shrink=0.8)

    plt.suptitle('TB3: MNIST Behavioral Model Accuracy', fontsize=14)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "mnist_accuracy.png"), dpi=150)
    plt.close()

    # Separate confusion matrix
    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(conf, cmap='Blues')
    ax.set_xlabel('Predicted', fontsize=12)
    ax.set_ylabel('True Label', fontsize=12)
    ax.set_title(f'MNIST Confusion Matrix (acc={final_acc:.1f}%)', fontsize=14)
    ax.set_xticks(range(10))
    ax.set_yticks(range(10))
    for i in range(10):
        for j in range(10):
            if conf[i, j] > 0:
                color = 'white' if conf[i, j] > conf.max()/2 else 'black'
                ax.text(j, i, str(conf[i, j]), ha='center', va='center',
                        fontsize=9, color=color)
    plt.colorbar(im, ax=ax, shrink=0.8)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "mnist_confusion_matrix.png"), dpi=150)
    plt.close()

    print(f"  Saved plots/mnist_accuracy.png (acc={final_acc:.1f}%)")
    print(f"  Saved plots/mnist_confusion_matrix.png")
    return final_acc, predictions


# =========================================================================
# TB4: MNIST Examples Grid
# =========================================================================
def plot_mnist_examples(tile, w1, w2, X_test, y_test, predictions=None):
    """Show 25 random test images with classifications."""
    if predictions is None:
        predictions = np.zeros(len(y_test), dtype=int)

    np.random.seed(42)
    indices = np.random.choice(min(len(predictions), len(y_test)), 25, replace=False)

    fig, axes = plt.subplots(5, 5, figsize=(12, 12))

    for i, idx in enumerate(indices):
        ax = axes[i // 5, i % 5]
        img = X_test[idx].reshape(28, 28)
        pred = predictions[idx]
        true = y_test[idx]
        correct = pred == true

        ax.imshow(img, cmap='gray')
        ax.set_title(f'P:{pred} T:{true}',
                     color='green' if correct else 'red', fontsize=11, fontweight='bold')
        ax.axis('off')

        # Add colored border
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(3)
            spine.set_color('green' if correct else 'red')

    plt.suptitle('TB4: MNIST Classification Examples (Green=Correct, Red=Incorrect)',
                 fontsize=14)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "mnist_examples.png"), dpi=150)
    plt.close()
    print("  Saved plots/mnist_examples.png")


# =========================================================================
# TB5: Analog vs Digital Comparison
# =========================================================================
def plot_analog_vs_digital(tile, w1):
    """Compare MVM results across behavioral model, ideal, showing scatter."""
    w1_pos = (w1+1)/2

    ideal_all = []
    tile_all = []

    np.random.seed(123)
    for trial in range(100):
        # Random binary weights and inputs
        W = np.random.choice([-1, 1], size=(64, 64)).astype(float)
        x_hw = np.random.choice([0, 1], size=64).astype(float)
        x_real = 2*x_hw - 1
        w_pos = (W + 1) / 2

        # Ideal
        ideal = x_real @ W

        # Tile (with noise)
        codes, _ = tile.mvm(W, x_hw, add_noise=True)
        dot_est = codes.astype(float) / tile.adc_gain
        wp_sums = w_pos.sum(axis=0)
        tile_signed = 4.0*dot_est - 2.0*x_hw.sum() - 2.0*wp_sums + 64

        ideal_all.extend(ideal.tolist())
        tile_all.extend(tile_signed.tolist())

    ideal_all = np.array(ideal_all)
    tile_all = np.array(tile_all)

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    # Scatter: tile vs ideal
    ax = axes[0]
    ax.scatter(ideal_all, tile_all, alpha=0.05, s=5, c='steelblue')
    lims = [min(ideal_all.min(), tile_all.min()), max(ideal_all.max(), tile_all.max())]
    ax.plot(lims, lims, 'r--', linewidth=1, label='y=x')
    ax.set_xlabel('Ideal (numpy)')
    ax.set_ylabel('CIM Tile (behavioral)')
    ax.set_title('MVM Output: CIM vs Ideal')
    ax.legend()
    ax.set_aspect('equal')
    ax.grid(alpha=0.3)

    # Error histogram
    ax = axes[1]
    errors = tile_all - ideal_all
    ax.hist(errors, bins=50, color='steelblue', alpha=0.8, edgecolor='white')
    ax.axvline(0, color='red', linewidth=1)
    ax.set_xlabel('Error (tile - ideal)')
    ax.set_ylabel('Count')
    ax.set_title(f'MVM Error Distribution\nmean={errors.mean():.2f}, std={errors.std():.2f}')
    ax.grid(alpha=0.3)

    # RMSE per trial
    ax = axes[2]
    rmses = []
    for trial in range(100):
        s = trial * 64
        e = s + 64
        err = tile_all[s:e] - ideal_all[s:e]
        scale = max(np.abs(ideal_all[s:e]).max(), 1.0)
        rmses.append(np.sqrt(np.mean(err**2)) / scale * 100)
    ax.hist(rmses, bins=30, color='coral', alpha=0.8, edgecolor='white')
    ax.axvline(np.mean(rmses), color='red', linewidth=2,
               label=f'Mean RMSE = {np.mean(rmses):.1f}%')
    ax.set_xlabel('RMSE (%)')
    ax.set_ylabel('Count')
    ax.set_title('MVM RMSE Distribution (100 trials)')
    ax.legend()
    ax.grid(alpha=0.3)

    plt.suptitle('TB5: Analog vs Digital MVM Comparison', fontsize=14)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "analog_vs_digital.png"), dpi=150)
    plt.close()
    print("  Saved plots/analog_vs_digital.png")


# =========================================================================
# Additional: Specs Summary, Power Breakdown, Timing Breakdown
# =========================================================================
def plot_specs_summary(measurements):
    """Summary plot showing all specs vs targets."""
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))

    meas = measurements

    # 1. MNIST Accuracy
    ax = axes[0, 0]
    accs = [meas.get('ideal_mnist_accuracy_pct', 0), meas.get('mnist_accuracy_pct', 0)]
    colors_acc = ['forestgreen', 'darkorange']
    bars = ax.bar(['Ideal\n(float)', 'CIM Tile\n(behavioral)'], accs, color=colors_acc, alpha=0.8)
    ax.axhline(85, color='red', linestyle='--', linewidth=2, label='Target (85%)')
    ax.set_ylabel('Accuracy (%)')
    ax.set_title('MNIST Classification Accuracy')
    ax.set_ylim(0, 100)
    ax.legend()
    for bar, val in zip(bars, accs):
        ax.text(bar.get_x() + bar.get_width()/2, val + 1, f'{val:.1f}%',
                ha='center', fontsize=11, fontweight='bold')

    # 2. MVM Accuracy
    ax = axes[0, 1]
    mvm_acc = meas.get('mvm_accuracy_pct', 0)
    ax.bar(['MVM\nAccuracy'], [mvm_acc], color='steelblue', alpha=0.8, width=0.4)
    ax.axhline(90, color='red', linestyle='--', linewidth=2, label='Target (90%)')
    ax.set_ylabel('Accuracy (%)')
    ax.set_title('Matrix-Vector Multiply Accuracy')
    ax.set_ylim(0, 100)
    ax.legend()
    ax.text(0, mvm_acc + 1, f'{mvm_acc:.1f}%', ha='center', fontsize=11, fontweight='bold')

    # 3. Cycle Time Breakdown
    ax = axes[1, 0]
    tb = meas.get('cycle_time_breakdown', {})
    phases = ['Precharge', 'Compute', 'Settle', 'Convert']
    times = [tb.get('precharge_ns', 5), tb.get('compute_ns', 75),
             tb.get('settle_ns', 20), tb.get('convert_ns', 108)]
    colors_t = ['#2196F3', '#FF9800', '#4CAF50', '#9C27B0']
    ax.bar(phases, times, color=colors_t, alpha=0.8)
    total = sum(times)
    ax.axhline(500, color='red', linestyle='--', linewidth=2, label=f'Target (<500ns)')
    ax.set_ylabel('Time (ns)')
    ax.set_title(f'Cycle Time Breakdown (total={total:.0f}ns)')
    ax.legend()
    for i, (p, t_val) in enumerate(zip(phases, times)):
        ax.text(i, t_val + 3, f'{t_val:.0f}ns', ha='center', fontsize=9)

    # 4. Power Breakdown
    ax = axes[1, 1]
    pb = meas.get('power_breakdown_mw', {})
    components = ['ADC\n(64x)', 'Array', 'PWM\n(64x)', 'Digital\nOverhead']
    powers = [pb.get('adc_64x_uw', 0)/1000, pb.get('array_uw', 0)/1000,
              pb.get('pwm_64x_uw', 0)/1000, pb.get('digital_overhead_uw', 0)/1000]
    colors_p = ['#E91E63', '#3F51B5', '#009688', '#FF5722']
    ax.bar(components, powers, color=colors_p, alpha=0.8)
    total_p = sum(powers)
    ax.axhline(10, color='red', linestyle='--', linewidth=2, label=f'Target (<10mW)')
    ax.set_ylabel('Power (mW)')
    ax.set_title(f'Power Breakdown (total={total_p:.2f}mW)')
    ax.legend()
    for i, p_val in enumerate(powers):
        ax.text(i, p_val + 0.01, f'{p_val:.3f}', ha='center', fontsize=8)

    plt.suptitle('CIM Tile Integration — Specification Summary', fontsize=14)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "integration_results.png"), dpi=150)
    plt.close()
    print("  Saved plots/integration_results.png")


# =========================================================================
# Main
# =========================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("CIM Tile Integration — Generating All Verification Plots")
    print("=" * 60)

    measurements, tile, w1, w2, X_test, y_test = load_everything()

    with open(BLOCK_DIR / "measurements.json") as f:
        saved_meas = json.load(f)

    print("\nTB1: End-to-End MVM Waveforms...")
    plot_e2e_waveforms(tile, w1)

    print("\nTB2: MNIST Single Digit...")
    plot_mnist_single_digit(tile, w1, w2, X_test, y_test)

    print("\nTB3: MNIST Accuracy + Confusion Matrix (1000 images)...")
    final_acc, predictions = plot_mnist_accuracy_and_confusion(
        tile, w1, w2, X_test, y_test, n_images=1000)

    print("\nTB4: MNIST Examples Grid...")
    plot_mnist_examples(tile, w1, w2, X_test, y_test, predictions)

    print("\nTB5: Analog vs Digital Comparison...")
    plot_analog_vs_digital(tile, w1)

    print("\nSpecs Summary...")
    plot_specs_summary(saved_meas)

    print("\n" + "=" * 60)
    print("All plots generated successfully!")
    print("=" * 60)
