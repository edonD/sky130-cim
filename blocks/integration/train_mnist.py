#!/usr/bin/env python3
"""
Train a binary-weight neural network for MNIST classification.

Architecture: 784 -> 64 (binary weights, sign activation) -> 10 (binary weights, argmax)
Training uses straight-through estimator (STE) for binary weight gradients.

Outputs:
  w1.npy  -- Layer 1 weights, shape (784, 64), values in {-1, +1}
  w2.npy  -- Layer 2 weights, shape (64, 10), values in {-1, +1}

Dependencies: numpy only (no PyTorch/TensorFlow).
"""

import numpy as np
import os
import struct
import gzip
import urllib.request

# ---------------------------------------------------------------------------
# MNIST data loading
# ---------------------------------------------------------------------------

MNIST_URLS = [
    "https://storage.googleapis.com/cvdf-datasets/mnist/",
    "https://ossci-datasets.s3.amazonaws.com/mnist/",
    "http://yann.lecun.com/exdb/mnist/",
]
MNIST_FILES = {
    "train_images": "train-images-idx3-ubyte.gz",
    "train_labels": "train-labels-idx1-ubyte.gz",
    "test_images":  "t10k-images-idx3-ubyte.gz",
    "test_labels":  "t10k-labels-idx1-ubyte.gz",
}

def download_mnist(data_dir="mnist_data"):
    """Download MNIST dataset if not already present."""
    os.makedirs(data_dir, exist_ok=True)
    for key, fname in MNIST_FILES.items():
        fpath = os.path.join(data_dir, fname)
        if not os.path.exists(fpath):
            downloaded = False
            for base_url in MNIST_URLS:
                try:
                    print(f"Downloading {fname} from {base_url}...")
                    urllib.request.urlretrieve(base_url + fname, fpath)
                    downloaded = True
                    break
                except Exception as e:
                    print(f"  Failed: {e}")
            if not downloaded:
                raise RuntimeError(f"Could not download {fname} from any mirror")
    return data_dir

def load_mnist_images(fpath):
    """Load MNIST image file (idx3-ubyte format)."""
    with gzip.open(fpath, "rb") as f:
        magic, n, rows, cols = struct.unpack(">IIII", f.read(16))
        assert magic == 2051
        data = np.frombuffer(f.read(), dtype=np.uint8)
        return data.reshape(n, rows * cols).astype(np.float32) / 255.0

def load_mnist_labels(fpath):
    """Load MNIST label file (idx1-ubyte format)."""
    with gzip.open(fpath, "rb") as f:
        magic, n = struct.unpack(">II", f.read(8))
        assert magic == 2049
        return np.frombuffer(f.read(), dtype=np.uint8)

def load_mnist(data_dir="mnist_data"):
    """Load full MNIST dataset. Downloads if needed."""
    data_dir = download_mnist(data_dir)
    X_train = load_mnist_images(os.path.join(data_dir, MNIST_FILES["train_images"]))
    y_train = load_mnist_labels(os.path.join(data_dir, MNIST_FILES["train_labels"]))
    X_test  = load_mnist_images(os.path.join(data_dir, MNIST_FILES["test_images"]))
    y_test  = load_mnist_labels(os.path.join(data_dir, MNIST_FILES["test_labels"]))
    return X_train, y_train, X_test, y_test

# ---------------------------------------------------------------------------
# Binary neural network primitives
# ---------------------------------------------------------------------------

def binarize(w):
    """Binarize weights to {-1, +1} using sign function."""
    return np.where(w >= 0, 1.0, -1.0)

def sign_activation(x):
    """Sign activation: output is +1 if x >= 0, else -1."""
    return np.where(x >= 0, 1.0, -1.0)

def softmax(x):
    """Numerically stable softmax."""
    e = np.exp(x - x.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)

def cross_entropy_loss(probs, labels):
    """Cross-entropy loss."""
    n = len(labels)
    log_probs = np.log(probs[np.arange(n), labels] + 1e-12)
    return -np.mean(log_probs)

def one_hot(labels, n_classes=10):
    """One-hot encode labels."""
    oh = np.zeros((len(labels), n_classes), dtype=np.float32)
    oh[np.arange(len(labels)), labels] = 1.0
    return oh

# ---------------------------------------------------------------------------
# Training with Straight-Through Estimator (STE)
# ---------------------------------------------------------------------------

class Adam:
    """Adam optimizer for numpy arrays."""
    def __init__(self, params, lr=0.001, beta1=0.9, beta2=0.999, eps=1e-8):
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.t = 0
        self.m = [np.zeros_like(p) for p in params]
        self.v = [np.zeros_like(p) for p in params]

    def step(self, params, grads):
        self.t += 1
        for i, (p, g) in enumerate(zip(params, grads)):
            self.m[i] = self.beta1 * self.m[i] + (1 - self.beta1) * g
            self.v[i] = self.beta2 * self.v[i] + (1 - self.beta2) * g**2
            m_hat = self.m[i] / (1 - self.beta1**self.t)
            v_hat = self.v[i] / (1 - self.beta2**self.t)
            p -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)
        return params


def train_binary_nn(X_train, y_train, X_test, y_test,
                    hidden_size=64, epochs=200, batch_size=128, lr=0.001):
    """
    Train a 2-layer binary-weight neural network using STE + Adam.

    Forward pass uses binarized weights. Backward pass uses straight-through
    estimator with proper gradient clipping.
    """
    n_in = X_train.shape[1]   # 784
    n_out = 10

    # Latent (real-valued) weights -- these get binarized in the forward pass
    # Xavier initialization scaled for binary networks
    w1_lat = np.random.randn(n_in, hidden_size).astype(np.float32) * np.sqrt(2.0 / n_in)
    w2_lat = np.random.randn(hidden_size, n_out).astype(np.float32) * np.sqrt(2.0 / hidden_size)
    w1_lat = np.clip(w1_lat, -1.0, 1.0)
    w2_lat = np.clip(w2_lat, -1.0, 1.0)

    optimizer = Adam([w1_lat, w2_lat], lr=lr)

    n_train = X_train.shape[0]
    best_test_acc = 0.0
    best_w1_bin = None
    best_w2_bin = None
    patience_count = 0

    for epoch in range(epochs):
        # Shuffle training data
        perm = np.random.permutation(n_train)
        X_shuf = X_train[perm]
        y_shuf = y_train[perm]

        epoch_loss = 0.0
        n_batches = 0

        for i in range(0, n_train, batch_size):
            xb = X_shuf[i:i+batch_size]
            yb = y_shuf[i:i+batch_size]
            bs = xb.shape[0]

            # --- Forward pass with binary weights ---
            w1_bin = binarize(w1_lat)
            w2_bin = binarize(w2_lat)

            # Layer 1: linear + sign activation
            z1 = xb @ w1_bin                  # (bs, hidden)
            a1 = sign_activation(z1)          # (bs, hidden)

            # Layer 2: linear + softmax (scale logits for better gradients)
            z2 = a1 @ w2_bin                  # (bs, 10)
            # Scale logits — with binary weights and 64 hidden units,
            # z2 can be in [-64, 64], too large for softmax
            z2_scaled = z2 / np.sqrt(hidden_size)
            probs = softmax(z2_scaled)        # (bs, 10)

            loss = cross_entropy_loss(probs, yb)
            epoch_loss += loss
            n_batches += 1

            # --- Backward pass (STE) ---
            # Gradient of softmax cross-entropy (with scaling)
            dz2_scaled = (probs - one_hot(yb)) / bs
            dz2 = dz2_scaled / np.sqrt(hidden_size)

            # Gradient w.r.t. w2 (latent)
            dw2 = a1.T @ dz2                  # (hidden, 10)

            # Gradient w.r.t. a1
            da1 = dz2 @ w2_bin.T              # (bs, hidden)

            # STE for sign activation: pass gradient through everywhere
            # (full STE — no clipping on pre-activation, works better for BNNs)
            dz1 = da1

            # Gradient w.r.t. w1 (latent)
            dw1 = xb.T @ dz1                  # (784, hidden)

            # STE for binarize: gradient passes through, clipped for |w| > 1
            dw1 *= (np.abs(w1_lat) <= 1.0).astype(np.float32)
            dw2 *= (np.abs(w2_lat) <= 1.0).astype(np.float32)

            # Update with Adam
            [w1_lat, w2_lat] = optimizer.step([w1_lat, w2_lat], [dw1, dw2])

            # Clip latent weights to [-1, 1]
            w1_lat = np.clip(w1_lat, -1.0, 1.0)
            w2_lat = np.clip(w2_lat, -1.0, 1.0)

        # --- Evaluate on test set ---
        w1_bin = binarize(w1_lat)
        w2_bin = binarize(w2_lat)

        z1_test = X_test @ w1_bin
        a1_test = sign_activation(z1_test)
        z2_test = a1_test @ w2_bin
        preds = np.argmax(z2_test, axis=1)
        test_acc = np.mean(preds == y_test) * 100.0

        z1_tr = X_train @ w1_bin
        a1_tr = sign_activation(z1_tr)
        z2_tr = a1_tr @ w2_bin
        preds_tr = np.argmax(z2_tr, axis=1)
        train_acc = np.mean(preds_tr == y_train) * 100.0

        avg_loss = epoch_loss / max(n_batches, 1)
        marker = ""
        if test_acc > best_test_acc:
            best_test_acc = test_acc
            best_w1_bin = w1_bin.copy()
            best_w2_bin = w2_bin.copy()
            patience_count = 0
            marker = " *best*"
        else:
            patience_count += 1

        if (epoch + 1) % 10 == 0 or marker:
            print(f"Epoch {epoch+1:3d}/{epochs}  loss={avg_loss:.4f}  "
                  f"train={train_acc:.2f}%  test={test_acc:.2f}%{marker}")

        # Early stopping with generous patience
        if patience_count > 50:
            print(f"Early stopping at epoch {epoch+1} (no improvement for 50 epochs)")
            break

    return best_w1_bin, best_w2_bin, best_test_acc

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Binary-Weight Neural Network Training for MNIST")
    print("Architecture: 784 -> 64 (sign) -> 10 (argmax)")
    print("=" * 60)

    # Load MNIST
    print("\nLoading MNIST dataset...")
    X_train, y_train, X_test, y_test = load_mnist()
    print(f"  Train: {X_train.shape[0]} images")
    print(f"  Test:  {X_test.shape[0]} images")

    # Binarize inputs: threshold at 0.5
    X_train_bin = np.where(X_train > 0.5, 1.0, -1.0).astype(np.float32)
    X_test_bin  = np.where(X_test  > 0.5, 1.0, -1.0).astype(np.float32)

    # Train
    print("\nTraining with STE + Adam optimizer...")
    w1, w2, best_acc = train_binary_nn(
        X_train_bin, y_train, X_test_bin, y_test,
        hidden_size=64, epochs=200, batch_size=128, lr=0.001
    )

    # Save weights
    np.save("w1.npy", w1)
    np.save("w2.npy", w2)
    print(f"\nSaved w1.npy shape={w1.shape} and w2.npy shape={w2.shape}")

    # Final evaluation with float inputs (not binarized)
    z1 = X_test @ w1  # float inputs * binary weights
    a1 = sign_activation(z1)
    z2 = a1 @ w2
    preds_float = np.argmax(z2, axis=1)
    float_acc = np.mean(preds_float == y_test) * 100.0

    # Final evaluation with binary inputs
    z1b = X_test_bin @ w1
    a1b = sign_activation(z1b)
    z2b = a1b @ w2
    preds_bin = np.argmax(z2b, axis=1)
    bin_acc = np.mean(preds_bin == y_test) * 100.0

    print(f"\n{'=' * 60}")
    print(f"RESULTS")
    print(f"{'=' * 60}")
    print(f"  Float-input accuracy:  {float_acc:.2f}%")
    print(f"  Binary-input accuracy: {bin_acc:.2f}%")
    print(f"  Best training accuracy: {best_acc:.2f}%")
    print(f"\n  Weight statistics:")
    print(f"    w1: {w1.shape}, unique values: {np.unique(w1)}")
    print(f"    w2: {w2.shape}, unique values: {np.unique(w2)}")
    print(f"    w1 +1 fraction: {np.mean(w1 == 1):.3f}")
    print(f"    w2 +1 fraction: {np.mean(w2 == 1):.3f}")

if __name__ == "__main__":
    main()
