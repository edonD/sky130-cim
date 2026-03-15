#!/usr/bin/env python3
"""
Phase B: Deep verification and margin analysis for CIM tile integration.

1. Multi-run noise sensitivity (accuracy stability)
2. Per-digit accuracy breakdown
3. Error budget analysis (quantization vs noise vs accumulation)
4. Noise sweep (accuracy vs noise level)
"""

import numpy as np
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BLOCK_DIR = Path(__file__).parent.resolve()
PLOTS_DIR = BLOCK_DIR / "plots"
sys.path.insert(0, str(BLOCK_DIR))

from evaluate import load_upstream_measurements, CIMTileBehavioral
from train_mnist import load_mnist


def run_mnist_inference(tile, w1, w2, X_test, y_test, n_images, add_noise=True):
    """Run MNIST inference, returns predictions array."""
    w1_pos = (w1+1)/2
    w2_pos = (w2+1)/2
    predictions = []

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
            codes, _ = tile.mvm(w_ch, x_ch, add_noise=add_noise)
            dot_est = codes.astype(float) / tile.adc_gain
            signed = 4.0*dot_est - 2.0*x_ch.sum() - 2.0*wp_ch.sum(axis=0) + actual_len
            accumulated += signed

        hidden = np.where(accumulated >= 0, 1.0, -1.0)

        # Layer 2
        w2_pad = np.zeros((64,64)); w2_pad[:64,:10] = w2
        w2pp = np.zeros((64,64)); w2pp[:64,:10] = w2_pos
        h_hw = np.where(hidden > 0, 1.0, 0.0)
        codes2, _ = tile.mvm(w2_pad, h_hw, add_noise=add_noise)
        dot2 = codes2.astype(float) / tile.adc_gain
        out = 4.0*dot2 - 2.0*h_hw.sum() - 2.0*w2pp.sum(axis=0) + 64
        predictions.append(np.argmax(out[:10]))

    return np.array(predictions)


def analysis_multi_run(tile, w1, w2, X_test, y_test, n_images=1000, n_runs=10):
    """Run inference multiple times to assess noise stability."""
    print("\n=== Multi-Run Noise Sensitivity ===")
    accuracies = []
    for run in range(n_runs):
        preds = run_mnist_inference(tile, w1, w2, X_test, y_test, n_images, add_noise=True)
        acc = np.mean(preds == y_test[:n_images]) * 100
        accuracies.append(acc)
        print(f"  Run {run+1}/{n_runs}: {acc:.1f}%")

    accuracies = np.array(accuracies)
    print(f"\n  Mean: {accuracies.mean():.2f}%")
    print(f"  Std:  {accuracies.std():.2f}%")
    print(f"  Min:  {accuracies.min():.1f}%, Max: {accuracies.max():.1f}%")

    # Plot
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(range(1, n_runs+1), accuracies, color='steelblue', alpha=0.8)
    ax.axhline(85, color='red', linestyle='--', linewidth=2, label='Target (85%)')
    ax.axhline(accuracies.mean(), color='green', linestyle=':', linewidth=2,
               label=f'Mean ({accuracies.mean():.1f}%)')
    ax.fill_between(range(0, n_runs+2),
                     accuracies.mean() - accuracies.std(),
                     accuracies.mean() + accuracies.std(),
                     alpha=0.2, color='green', label=f'±1σ ({accuracies.std():.1f}%)')
    ax.set_xlabel('Run number')
    ax.set_ylabel('Accuracy (%)')
    ax.set_title(f'MNIST Accuracy Stability ({n_runs} runs, {n_images} images each)')
    ax.set_ylim(80, 95)
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "accuracy_stability.png"), dpi=150)
    plt.close()
    print("  Saved plots/accuracy_stability.png")
    return accuracies


def analysis_per_digit(tile, w1, w2, X_test, y_test, n_images=1000):
    """Per-digit accuracy breakdown."""
    print("\n=== Per-Digit Accuracy ===")
    preds = run_mnist_inference(tile, w1, w2, X_test, y_test, n_images, add_noise=True)

    # Also get ideal
    x_real = np.where(X_test[:n_images] > 0.5, 1.0, -1.0)
    z1 = x_real @ w1
    a1 = np.where(z1 >= 0, 1.0, -1.0)
    z2 = a1 @ w2
    ideal_preds = np.argmax(z2, axis=1)

    per_digit = {}
    for d in range(10):
        mask = y_test[:n_images] == d
        n_d = mask.sum()
        if n_d > 0:
            hw_acc = np.mean(preds[mask] == d) * 100
            ideal_acc = np.mean(ideal_preds[mask] == d) * 100
            per_digit[d] = {'n': n_d, 'hw_acc': hw_acc, 'ideal_acc': ideal_acc}
            print(f"  Digit {d}: n={n_d:3d}, HW={hw_acc:.1f}%, Ideal={ideal_acc:.1f}%, "
                  f"Drop={ideal_acc - hw_acc:+.1f}%")

    # Plot
    fig, ax = plt.subplots(figsize=(10, 6))
    digits = list(range(10))
    hw_accs = [per_digit[d]['hw_acc'] for d in digits]
    ideal_accs = [per_digit[d]['ideal_acc'] for d in digits]

    x_pos = np.arange(10)
    width = 0.35
    ax.bar(x_pos - width/2, ideal_accs, width, label='Ideal (float)', color='steelblue', alpha=0.8)
    ax.bar(x_pos + width/2, hw_accs, width, label='CIM Tile', color='coral', alpha=0.8)
    ax.axhline(85, color='red', linestyle='--', alpha=0.5, label='Target')
    ax.set_xticks(x_pos)
    ax.set_xticklabels([str(d) for d in digits])
    ax.set_xlabel('Digit')
    ax.set_ylabel('Accuracy (%)')
    ax.set_title('Per-Digit Classification Accuracy')
    ax.set_ylim(60, 100)
    ax.legend()
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "per_digit_accuracy.png"), dpi=150)
    plt.close()
    print("  Saved plots/per_digit_accuracy.png")
    return per_digit


def analysis_noise_sweep(tile_base_measurements, w1, w2, X_test, y_test, n_images=500):
    """Sweep noise level and measure accuracy degradation."""
    print("\n=== Noise Level Sweep ===")

    # Test with different noise multipliers
    noise_levels = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0]
    accuracies = []

    for noise_mult in noise_levels:
        # Create tile with adjusted noise
        tile = CIMTileBehavioral(tile_base_measurements, max_input_value=1)
        # Scale the noise in the mvm function
        original_v_lsb = tile.v_lsb

        # Monkey-patch the noise level for testing
        tile._noise_mult = noise_mult

        # Run inference
        preds = run_mnist_inference(tile, w1, w2, X_test, y_test, n_images,
                                     add_noise=(noise_mult > 0))
        acc = np.mean(preds == y_test[:n_images]) * 100
        accuracies.append(acc)
        print(f"  Noise x{noise_mult:.1f}: {acc:.1f}%")

    # Plot
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(noise_levels, accuracies, 'bo-', linewidth=2, markersize=8)
    ax.axhline(85, color='red', linestyle='--', linewidth=2, label='Target (85%)')
    ax.set_xlabel('Noise multiplier')
    ax.set_ylabel('MNIST Accuracy (%)')
    ax.set_title('Accuracy vs Noise Level')
    ax.legend()
    ax.grid(alpha=0.3)
    ax.set_ylim(50, 95)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "noise_sweep.png"), dpi=150)
    plt.close()
    print("  Saved plots/noise_sweep.png")
    return noise_levels, accuracies


def analysis_error_budget(tile, w1, w2, X_test, y_test, n_images=200):
    """Analyze error budget: quantization, noise, accumulation."""
    print("\n=== Error Budget Analysis ===")
    w1_pos = (w1+1)/2

    quant_errors = []
    noise_errors = []
    total_errors = []

    for idx in range(n_images):
        x = X_test[idx]
        x_hw = (x > 0.5).astype(float)
        x_real = 2*x_hw - 1

        # Ideal result
        ideal_z1 = x_real @ w1

        # No-noise tile result (quantization only)
        acc_quant = np.zeros(64, dtype=float)
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
            acc_quant += signed

        # With-noise tile result
        acc_noisy = np.zeros(64, dtype=float)
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
            acc_noisy += signed

        q_err = np.abs(acc_quant - ideal_z1).mean()
        n_err = np.abs(acc_noisy - acc_quant).mean()
        t_err = np.abs(acc_noisy - ideal_z1).mean()
        quant_errors.append(q_err)
        noise_errors.append(n_err)
        total_errors.append(t_err)

    quant_errors = np.array(quant_errors)
    noise_errors = np.array(noise_errors)
    total_errors = np.array(total_errors)

    print(f"  Quantization error (mean): {quant_errors.mean():.2f}")
    print(f"  Noise error (mean):        {noise_errors.mean():.2f}")
    print(f"  Total error (mean):        {total_errors.mean():.2f}")
    print(f"  Quantization fraction:     {quant_errors.mean()/total_errors.mean()*100:.1f}%")
    print(f"  Noise fraction:            {noise_errors.mean()/total_errors.mean()*100:.1f}%")

    # Plot
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    ax = axes[0]
    categories = ['Quantization', 'Noise', 'Total']
    values = [quant_errors.mean(), noise_errors.mean(), total_errors.mean()]
    colors = ['steelblue', 'coral', 'forestgreen']
    ax.bar(categories, values, color=colors, alpha=0.8)
    ax.set_ylabel('Mean Absolute Error')
    ax.set_title('Error Budget (Layer 1 Pre-activation)')
    for i, v in enumerate(values):
        ax.text(i, v+0.1, f'{v:.2f}', ha='center', fontsize=10)
    ax.grid(axis='y', alpha=0.3)

    ax = axes[1]
    ax.hist(quant_errors, bins=30, alpha=0.7, color='steelblue', label='Quantization')
    ax.hist(noise_errors, bins=30, alpha=0.7, color='coral', label='Noise')
    ax.set_xlabel('Mean Absolute Error per Image')
    ax.set_ylabel('Count')
    ax.set_title('Error Distribution')
    ax.legend()
    ax.grid(alpha=0.3)

    plt.suptitle('Error Budget Analysis', fontsize=14)
    plt.tight_layout()
    plt.savefig(str(PLOTS_DIR / "error_budget.png"), dpi=150)
    plt.close()
    print("  Saved plots/error_budget.png")

    return {
        'quant_mean': quant_errors.mean(),
        'noise_mean': noise_errors.mean(),
        'total_mean': total_errors.mean()
    }


if __name__ == "__main__":
    print("=" * 60)
    print("CIM Tile Integration — Phase B Deep Analysis")
    print("=" * 60)

    measurements = load_upstream_measurements()
    tile = CIMTileBehavioral(measurements, max_input_value=1)
    w1 = np.load(str(BLOCK_DIR / "w1.npy"))
    w2 = np.load(str(BLOCK_DIR / "w2.npy"))
    _, _, X_test, y_test = load_mnist(str(BLOCK_DIR / "mnist_data"))

    # 1. Multi-run stability
    accs = analysis_multi_run(tile, w1, w2, X_test, y_test, n_images=1000, n_runs=10)

    # 2. Per-digit breakdown
    per_digit = analysis_per_digit(tile, w1, w2, X_test, y_test, n_images=1000)

    # 3. Error budget
    err_budget = analysis_error_budget(tile, w1, w2, X_test, y_test, n_images=200)

    # 4. Noise sweep (smaller set for speed)
    # noise_levels, noise_accs = analysis_noise_sweep(measurements, w1, w2, X_test, y_test, n_images=500)

    print("\n" + "=" * 60)
    print("Phase B Analysis Complete!")
    print("=" * 60)
