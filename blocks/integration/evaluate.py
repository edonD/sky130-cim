#!/usr/bin/env python3
"""
CIM Tile Integration Evaluator

Combines all upstream block measurements into a complete CIM tile model,
runs MNIST inference, and validates against specs.json targets.

Two evaluation modes:
  1. SPICE-based: small-scale MVM through full transistor simulation (for calibration)
  2. Behavioral: Python model calibrated to upstream measurements (for full MNIST)
"""

import json
import os
import sys
import subprocess
import numpy as np
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BLOCK_DIR = Path(__file__).parent.resolve()
BLOCKS_DIR = BLOCK_DIR.parent
SPECS_FILE = BLOCK_DIR / "specs.json"

UPSTREAM_BLOCKS = {
    "bitcell":    BLOCKS_DIR / "bitcell",
    "adc":        BLOCKS_DIR / "adc",
    "pwm-driver": BLOCKS_DIR / "pwm-driver",
    "array":      BLOCKS_DIR / "array",
}

# ---------------------------------------------------------------------------
# Load upstream measurements
# ---------------------------------------------------------------------------

def load_upstream_measurements():
    """Load measurements.json from all upstream blocks."""
    measurements = {}
    for name, path in UPSTREAM_BLOCKS.items():
        meas_file = path / "measurements.json"
        if meas_file.exists():
            with open(meas_file) as f:
                measurements[name] = json.load(f)
            print(f"  Loaded {name}/measurements.json")
        else:
            print(f"  WARNING: {name}/measurements.json not found")
            measurements[name] = None
    return measurements

def load_specs():
    """Load target specifications."""
    with open(SPECS_FILE) as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Behavioral CIM tile model
# ---------------------------------------------------------------------------

class CIMTileBehavioral:
    """
    Behavioral model of the 64x64 CIM tile, calibrated to upstream SPICE
    measurements.

    Models the full signal chain:
      input (4-bit) -> PWM pulse width -> bitcell current -> BL voltage -> ADC code
    """

    def __init__(self, measurements, array_rows=64, array_cols=64,
                 input_bits=4, output_bits=6):
        self.rows = array_rows
        self.cols = array_cols
        self.input_bits = input_bits
        self.output_bits = output_bits
        self.n_codes = 2**output_bits  # 64

        # Extract parameters from upstream measurements (with defaults)
        self._extract_params(measurements)

    def _extract_params(self, meas):
        """Extract tile parameters from upstream block measurements."""
        # Bitcell parameters
        bc = meas.get("bitcell") or {}
        self.i_read_ua = bc.get("i_read_ua", 20.0)       # uA
        self.i_leak_na = bc.get("i_leak_na", 10.0)        # nA
        self.c_bl_cell_ff = bc.get("c_bl_cell_ff", 0.5)   # fF per cell

        # PWM parameters
        pwm = meas.get("pwm-driver") or {}
        self.t_lsb_ns = pwm.get("t_lsb_ns", 5.0)         # ns per LSB
        self.pwm_inl_lsb = pwm.get("inl_lsb", 0.3)       # PWM nonlinearity

        # ADC parameters
        adc = meas.get("adc") or {}
        self.adc_dnl_lsb = adc.get("dnl_lsb", 0.3)
        self.adc_inl_lsb = adc.get("inl_lsb", 0.5)
        self.adc_enob = adc.get("enob", 5.5)
        self.adc_conv_time_ns = adc.get("conversion_time_ns", 150.0)
        self.adc_power_uw = adc.get("power_uw", 30.0)

        # Array parameters
        arr = meas.get("array") or {}
        self.array_rmse_pct = arr.get("rmse_pct", 5.0)
        self.bl_swing_mv = arr.get("bl_swing_mv", 500.0)
        self.array_power_uw = arr.get("power_uw", 500.0)

        # Derived parameters
        self.c_bl_total_ff = self.c_bl_cell_ff * self.rows  # total BL cap
        # Max BL discharge: all rows active at max input
        max_input = (2**self.input_bits - 1)
        self.t_max_ns = max_input * self.t_lsb_ns
        # Charge per cell at max pulse: I_read * T_max
        self.q_per_cell_max = self.i_read_ua * 1e-6 * self.t_max_ns * 1e-9  # Coulombs
        # Voltage per cell at max pulse: Q / C_bl_total
        c_bl_total_f = self.c_bl_total_ff * 1e-15
        if c_bl_total_f > 0:
            self.dv_per_cell_max_v = self.q_per_cell_max / c_bl_total_f
        else:
            self.dv_per_cell_max_v = 0.01  # fallback

        # Full-scale voltage swing
        self.v_pre = 1.8  # precharge voltage
        self.v_fs = min(self.dv_per_cell_max_v * self.rows, self.v_pre)

        # ADC LSB voltage
        self.v_lsb = self.v_fs / self.n_codes if self.v_fs > 0 else 0.028

        print(f"\n  CIM Tile Behavioral Model Parameters:")
        print(f"    I_READ = {self.i_read_ua:.1f} uA, I_LEAK = {self.i_leak_na:.1f} nA")
        print(f"    T_LSB = {self.t_lsb_ns:.1f} ns, T_MAX = {self.t_max_ns:.1f} ns")
        print(f"    C_BL_total = {self.c_bl_total_ff:.1f} fF")
        print(f"    dV/cell (max pulse) = {self.dv_per_cell_max_v*1000:.1f} mV")
        print(f"    V_FS = {self.v_fs*1000:.1f} mV, V_LSB = {self.v_lsb*1000:.2f} mV")
        print(f"    ADC ENOB = {self.adc_enob:.1f} bits")

    def mvm(self, weight_matrix, input_vector, add_noise=True):
        """
        Perform a matrix-vector multiply through the behavioral CIM model.

        Args:
            weight_matrix: (rows, cols) binary weights in {0, 1} or {-1, +1}
            input_vector: (rows,) input values, 4-bit unsigned [0..15] or
                          float [-1, +1] (will be quantized)
            add_noise: whether to add realistic noise/nonlinearity

        Returns:
            adc_codes: (cols,) integer ADC output codes [0..63]
            analog_voltages: (cols,) bitline voltages before ADC (for debug)
        """
        rows, cols = weight_matrix.shape
        assert rows <= self.rows and cols <= self.cols

        # Convert {-1,+1} weights to {0,1} if needed
        if np.any(weight_matrix < 0):
            w_binary = (weight_matrix + 1) / 2  # map -1->0, +1->1
        else:
            w_binary = weight_matrix.copy()

        # Quantize input to 4-bit unsigned [0..15]
        if np.any(input_vector < 0):
            # Input is in [-1, +1] range, map to [0, 15]
            x_quant = np.clip(np.round((input_vector + 1) / 2 * 15), 0, 15).astype(int)
        else:
            x_quant = np.clip(np.round(input_vector), 0, 15).astype(int)

        # Compute ideal dot product: sum_i(w[i,j] * x[i])
        # This represents total charge drawn from each bitline
        dot = x_quant.astype(float) @ w_binary[:rows, :cols]

        # Convert to bitline voltage discharge
        # V_bl = V_pre - dot * I_read * T_lsb / C_bl_total
        charge_per_unit = self.i_read_ua * 1e-6 * self.t_lsb_ns * 1e-9  # Coulombs
        c_bl = self.c_bl_total_ff * 1e-15
        if c_bl > 0:
            dv = dot * charge_per_unit / c_bl
        else:
            dv = dot * 0.001

        v_bl = self.v_pre - dv

        if add_noise:
            # Add leakage contribution (small, systematic)
            # Leakage from cells with weight=0
            n_leak = rows - w_binary.sum(axis=0)  # number of off-cells per column
            leak_charge = n_leak * self.i_leak_na * 1e-9 * self.t_max_ns * 1e-9
            if c_bl > 0:
                v_bl -= leak_charge / c_bl

            # Add random noise (thermal + mismatch)
            noise_sigma = self.v_lsb * 0.3  # ~0.3 LSB of noise
            v_bl += np.random.randn(cols) * noise_sigma

            # Add PWM nonlinearity (systematic, input-dependent)
            pwm_error = self.pwm_inl_lsb * self.v_lsb * np.random.randn(cols) * 0.1
            v_bl += pwm_error

        # Clamp to valid range
        v_bl = np.clip(v_bl, 0, self.v_pre)

        # ADC conversion
        adc_codes = self._adc_convert(v_bl)

        return adc_codes, v_bl

    def _adc_convert(self, v_bl):
        """
        Model ADC conversion with realistic DNL/INL.

        The ADC converts V_bl to a 6-bit code. Lower voltage = higher code
        (discharge = more activity = larger dot product).
        """
        # Ideal conversion: code = (V_pre - V_bl) / V_lsb
        v_discharge = self.v_pre - v_bl
        ideal_code = v_discharge / self.v_lsb if self.v_lsb > 0 else v_discharge * 10

        # Add DNL (random code-dependent offset)
        dnl_error = self.adc_dnl_lsb * np.random.randn(len(v_bl)) * 0.5
        noisy_code = ideal_code + dnl_error

        # Quantize and clamp
        codes = np.clip(np.round(noisy_code), 0, self.n_codes - 1).astype(int)

        return codes

    def mvm_signed(self, weight_matrix, input_vector, add_noise=True):
        """
        Signed MVM for binary weights in {-1, +1}.

        Uses two's complement approach:
          - Run MVM with w_pos = (W+1)/2 (weights mapped to {0,1})
          - The result encodes: sum(w * x) = 2 * sum(w_pos * x) - sum(x)
          - Digital post-processing recovers the signed result
        """
        codes, v_bl = self.mvm(weight_matrix, input_vector, add_noise=add_noise)

        # Recover signed result:
        # code represents sum(w_pos * x) where w_pos = (W+1)/2
        # signed_result = 2 * code - sum(x)
        if np.any(input_vector < 0):
            x_quant = np.clip(np.round((input_vector + 1) / 2 * 15), 0, 15)
        else:
            x_quant = np.clip(np.round(input_vector), 0, 15)
        x_sum = x_quant.sum()

        signed_result = 2.0 * codes.astype(float) - x_sum

        return signed_result, codes, v_bl

# ---------------------------------------------------------------------------
# MNIST inference through behavioral model
# ---------------------------------------------------------------------------

def load_weights():
    """Load trained binary weights."""
    w1_path = BLOCK_DIR / "w1.npy"
    w2_path = BLOCK_DIR / "w2.npy"

    if not w1_path.exists() or not w2_path.exists():
        print("  Weights not found. Running train_mnist.py...")
        subprocess.run([sys.executable, str(BLOCK_DIR / "train_mnist.py")],
                       cwd=str(BLOCK_DIR), check=True)

    w1 = np.load(str(w1_path))  # (784, 64) binary
    w2 = np.load(str(w2_path))  # (64, 10) binary
    print(f"  Loaded w1 {w1.shape} and w2 {w2.shape}")
    return w1, w2

def mnist_inference_behavioral(tile, w1, w2, X_test, y_test, n_images=100):
    """
    Run MNIST inference through the behavioral CIM tile model.

    Layer 1 (784->64) is tiled across multiple MVM passes.
    Layer 2 (64->10) is a single pass.
    """
    n_images = min(n_images, len(X_test))
    correct = 0
    predictions = []

    for idx in range(n_images):
        x = X_test[idx]  # (784,) float in [0,1]

        # --- Layer 1: tiled MVM ---
        # Split 784-element input into chunks of 64
        chunk_size = 64
        n_chunks = (784 + chunk_size - 1) // chunk_size  # 13 chunks
        accumulated = np.zeros(64, dtype=float)

        for c in range(n_chunks):
            start = c * chunk_size
            end = min(start + chunk_size, 784)
            x_chunk = x[start:end]
            w_chunk = w1[start:end, :]  # (<=64, 64)

            # Pad if last chunk is shorter
            if len(x_chunk) < chunk_size:
                x_pad = np.zeros(chunk_size)
                x_pad[:len(x_chunk)] = x_chunk
                w_pad = np.zeros((chunk_size, 64))
                w_pad[:w_chunk.shape[0], :] = w_chunk
                x_chunk = x_pad
                w_chunk = w_pad

            # Quantize input to 4-bit: map [0,1] -> [0,15]
            x_4bit = np.clip(np.round(x_chunk * 15), 0, 15)

            # Run through tile (unsigned MVM, then correct for signed weights)
            signed_result, _, _ = tile.mvm_signed(w_chunk, x_4bit, add_noise=True)
            accumulated += signed_result

        # Sign activation
        hidden = np.where(accumulated >= 0, 1.0, -1.0)

        # --- Layer 2: single MVM pass ---
        # w2 is (64, 10), pad to (64, 64) for tile
        w2_pad = np.zeros((64, 64))
        w2_pad[:64, :10] = w2

        # Hidden layer values are {-1, +1}, map to 4-bit: -1->0, +1->15
        h_4bit = np.where(hidden > 0, 15.0, 0.0)

        signed_out, _, _ = tile.mvm_signed(w2_pad, h_4bit, add_noise=True)
        output = signed_out[:10]  # only first 10 columns matter

        pred = np.argmax(output)
        predictions.append(pred)
        if pred == y_test[idx]:
            correct += 1

    accuracy = correct / n_images * 100.0
    return accuracy, np.array(predictions)

def mnist_inference_ideal(w1, w2, X_test, y_test, n_images=100):
    """
    Run MNIST inference with ideal floating-point arithmetic (no hardware effects).
    This is the upper bound on accuracy.
    """
    n_images = min(n_images, len(X_test))

    # Binarize inputs at 0.5 threshold to match training
    X_bin = np.where(X_test[:n_images] > 0.5, 1.0, -1.0)

    z1 = X_bin @ w1           # (n, 64)
    a1 = np.where(z1 >= 0, 1.0, -1.0)
    z2 = a1 @ w2              # (n, 10)
    preds = np.argmax(z2, axis=1)

    accuracy = np.mean(preds == y_test[:n_images]) * 100.0
    return accuracy, preds

# ---------------------------------------------------------------------------
# MVM accuracy test (random vectors)
# ---------------------------------------------------------------------------

def test_mvm_accuracy(tile, n_tests=50):
    """
    Test MVM accuracy with random weight matrices and input vectors.
    Compare tile output to ideal numpy dot product.
    """
    errors = []

    for _ in range(n_tests):
        # Random binary weights
        W = np.random.choice([-1, 1], size=(64, 64)).astype(float)
        # Random 4-bit inputs
        x = np.random.randint(0, 16, size=64).astype(float)

        # Ideal result
        w_pos = (W + 1) / 2
        ideal_dot = x @ w_pos
        ideal_signed = 2 * ideal_dot - x.sum()

        # Tile result
        signed_result, _, _ = tile.mvm_signed(W, x, add_noise=True)

        # Normalized error
        scale = max(np.abs(ideal_signed).max(), 1.0)
        rmse = np.sqrt(np.mean((signed_result - ideal_signed)**2)) / scale * 100
        errors.append(rmse)

    mean_rmse = np.mean(errors)
    accuracy = 100.0 - mean_rmse
    return accuracy, mean_rmse, errors

# ---------------------------------------------------------------------------
# Cycle time and power estimation
# ---------------------------------------------------------------------------

def estimate_cycle_time(measurements):
    """Estimate total cycle time from upstream measurements."""
    pwm = measurements.get("pwm-driver") or {}
    adc = measurements.get("adc") or {}

    t_precharge = 5.0   # ns (fixed)
    t_lsb = pwm.get("t_lsb_ns", 5.0)
    t_compute = 15 * t_lsb  # worst case: max input value
    t_settle = 20.0     # ns (fixed)
    t_convert = adc.get("conversion_time_ns", 200.0)

    total = t_precharge + t_compute + t_settle + t_convert
    breakdown = {
        "precharge_ns": t_precharge,
        "compute_ns": t_compute,
        "settle_ns": t_settle,
        "convert_ns": t_convert,
        "total_ns": total,
    }
    return total, breakdown

def estimate_power(measurements):
    """Estimate total tile power from upstream measurements."""
    adc = measurements.get("adc") or {}
    arr = measurements.get("array") or {}
    pwm = measurements.get("pwm-driver") or {}

    # 64 ADCs
    adc_power_uw = adc.get("power_uw", 30.0) * 64
    # Array power (already for full array)
    array_power_uw = arr.get("power_uw", 500.0)
    # 64 PWM drivers
    pwm_power_uw = pwm.get("power_uw", 5.0) * 64
    # Digital logic overhead (~10% of analog)
    digital_overhead_uw = (adc_power_uw + array_power_uw + pwm_power_uw) * 0.1

    total_uw = adc_power_uw + array_power_uw + pwm_power_uw + digital_overhead_uw
    total_mw = total_uw / 1000.0

    breakdown = {
        "adc_64x_uw": adc_power_uw,
        "array_uw": array_power_uw,
        "pwm_64x_uw": pwm_power_uw,
        "digital_overhead_uw": digital_overhead_uw,
        "total_uw": total_uw,
        "total_mw": total_mw,
    }
    return total_mw, breakdown

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_results(results, specs):
    """Score results against specs.json targets."""
    measurements_spec = specs["measurements"]
    total_score = 0
    total_weight = 0
    details = {}

    for name, spec in measurements_spec.items():
        target = spec["target"]
        weight = spec["weight"]
        value = results.get(name)

        if value is None:
            details[name] = {"value": None, "pass": False, "score": 0}
            total_weight += weight
            continue

        # Parse target
        if target.startswith(">"):
            threshold = float(target[1:])
            passed = value > threshold
            # Score: how far above threshold (normalized)
            margin = (value - threshold) / threshold if threshold != 0 else 0
        elif target.startswith("<"):
            threshold = float(target[1:])
            passed = value < threshold
            margin = (threshold - value) / threshold if threshold != 0 else 0
        else:
            threshold = float(target)
            passed = abs(value - threshold) < threshold * 0.1
            margin = 0

        score = weight * (1.0 if passed else max(0, 0.5 + margin))
        total_score += score
        total_weight += weight

        details[name] = {
            "value": value,
            "target": target,
            "pass": passed,
            "margin": margin,
            "score": score,
            "weight": weight,
        }

    final_score = total_score / total_weight * 100 if total_weight > 0 else 0
    return final_score, details

# ---------------------------------------------------------------------------
# SPICE-based small-scale validation
# ---------------------------------------------------------------------------

def run_small_spice_mvm(measurements, size=8):
    """
    Run a small-scale MVM through full SPICE simulation for calibration.

    This uses the upstream design.cir files to build a small array and
    simulate one MVM operation end-to-end.

    Returns None if SPICE files are not available.
    """
    # Check if upstream design files exist
    array_cir = BLOCKS_DIR / "array" / "design.cir"
    if not array_cir.exists():
        print("  Array design.cir not found -- skipping SPICE validation")
        return None

    print(f"  SPICE validation with {size}x{size} array...")
    # TODO: Build and run SPICE testbench from upstream designs
    # This will be implemented when all upstream blocks are complete
    print("  SPICE validation not yet implemented (waiting for upstream blocks)")
    return None

# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate(n_mnist_images=100, run_spice=False):
    """Run full integration evaluation."""
    print("=" * 60)
    print("CIM Tile Integration Evaluation")
    print("=" * 60)

    # Load specs
    specs = load_specs()
    print(f"\nTarget specs:")
    for name, spec in specs["measurements"].items():
        print(f"  {name}: {spec['target']} {spec['unit']}")

    # Load upstream measurements
    print(f"\nLoading upstream measurements...")
    measurements = load_upstream_measurements()

    # Estimate cycle time and power
    print(f"\nEstimating cycle time and power...")
    cycle_time_ns, time_breakdown = estimate_cycle_time(measurements)
    total_power_mw, power_breakdown = estimate_power(measurements)
    print(f"  Cycle time: {cycle_time_ns:.1f} ns")
    for k, v in time_breakdown.items():
        print(f"    {k}: {v:.1f} ns")
    print(f"  Total power: {total_power_mw:.2f} mW")
    for k, v in power_breakdown.items():
        print(f"    {k}: {v:.1f}")

    # Build behavioral model
    print(f"\nBuilding behavioral CIM tile model...")
    tile = CIMTileBehavioral(measurements)

    # MVM accuracy test
    print(f"\nTesting MVM accuracy (random vectors)...")
    mvm_acc, mvm_rmse, mvm_errors = test_mvm_accuracy(tile, n_tests=100)
    print(f"  MVM accuracy: {mvm_acc:.1f}% (RMSE: {mvm_rmse:.2f}%)")

    # Load or train MNIST weights
    print(f"\nLoading MNIST weights...")
    w1, w2 = load_weights()

    # Load MNIST test data
    print(f"\nLoading MNIST test data...")
    sys.path.insert(0, str(BLOCK_DIR))
    from train_mnist import load_mnist
    _, _, X_test, y_test = load_mnist(str(BLOCK_DIR / "mnist_data"))

    # Ideal accuracy (upper bound)
    print(f"\nRunning ideal MNIST inference...")
    ideal_acc, ideal_preds = mnist_inference_ideal(w1, w2, X_test, y_test, n_mnist_images)
    print(f"  Ideal accuracy: {ideal_acc:.1f}%")

    # Behavioral model accuracy
    print(f"\nRunning behavioral MNIST inference ({n_mnist_images} images)...")
    behav_acc, behav_preds = mnist_inference_behavioral(
        tile, w1, w2, X_test, y_test, n_mnist_images
    )
    print(f"  Behavioral model accuracy: {behav_acc:.1f}%")

    # SPICE validation (if requested)
    spice_result = None
    if run_spice:
        spice_result = run_small_spice_mvm(measurements)

    # Compile results
    results = {
        "mnist_accuracy_pct": behav_acc,
        "mvm_accuracy_pct": mvm_acc,
        "cycle_time_ns": cycle_time_ns,
        "total_power_mw": total_power_mw,
    }

    # Score against specs
    print(f"\n{'=' * 60}")
    print("SCORING")
    print(f"{'=' * 60}")
    score, details = score_results(results, specs)
    for name, d in details.items():
        status = "PASS" if d["pass"] else "FAIL"
        print(f"  {name}: {d['value']:.2f} (target {d['target']}) [{status}]")
    print(f"\n  Overall score: {score:.1f}/100")

    # Save measurements.json
    all_measurements = {
        **results,
        "ideal_mnist_accuracy_pct": ideal_acc,
        "mvm_rmse_pct": mvm_rmse,
        "cycle_time_breakdown": time_breakdown,
        "power_breakdown_mw": {k: v/1000 for k, v in power_breakdown.items()
                                if k != "total_mw"},
        "score": score,
    }

    meas_path = BLOCK_DIR / "measurements.json"
    with open(meas_path, "w") as f:
        json.dump(all_measurements, f, indent=2)
    print(f"\n  Saved measurements.json")

    return score, all_measurements

# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_results(measurements):
    """Generate summary plots."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not available -- skipping plots")
        return

    plots_dir = BLOCK_DIR / "plots"
    plots_dir.mkdir(exist_ok=True)

    # Bar chart of specs vs results
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    specs = load_specs()
    names = list(specs["measurements"].keys())
    targets = []
    values = []
    for n in names:
        t = specs["measurements"][n]["target"]
        targets.append(float(t.lstrip("><")))
        values.append(measurements.get(n, 0))

    ax = axes[0]
    x = np.arange(len(names))
    ax.bar(x - 0.2, targets, 0.35, label="Target", alpha=0.7, color="steelblue")
    ax.bar(x + 0.2, values, 0.35, label="Measured", alpha=0.7, color="coral")
    ax.set_xticks(x)
    ax.set_xticklabels([n.replace("_", "\n") for n in names], fontsize=8)
    ax.set_title("Specs vs Measured")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    # Accuracy comparison
    ax = axes[1]
    accs = [
        measurements.get("ideal_mnist_accuracy_pct", 0),
        measurements.get("mnist_accuracy_pct", 0),
    ]
    labels = ["Ideal\n(float)", "Behavioral\n(CIM tile)"]
    colors = ["forestgreen", "darkorange"]
    ax.bar(range(len(accs)), accs, color=colors, alpha=0.8)
    ax.set_xticks(range(len(accs)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("MNIST Accuracy (%)")
    ax.set_title("MNIST Classification Accuracy")
    ax.set_ylim(0, 100)
    ax.axhline(85, color="red", linestyle="--", label="Target (85%)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(str(plots_dir / "integration_results.png"), dpi=150)
    plt.close()
    print(f"  Saved plots/integration_results.png")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CIM Tile Integration Evaluation")
    parser.add_argument("--n-images", type=int, default=100,
                        help="Number of MNIST test images")
    parser.add_argument("--spice", action="store_true",
                        help="Run SPICE validation (requires upstream designs)")
    parser.add_argument("--plot", action="store_true",
                        help="Generate summary plots")
    args = parser.parse_args()

    score, meas = evaluate(n_mnist_images=args.n_images, run_spice=args.spice)

    if args.plot:
        plot_results(meas)

    sys.exit(0 if score >= 50 else 1)
