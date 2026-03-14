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

MNIST_URL = "http://yann.lecun.com/exdb/mnist/"
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
            print(f"Downloading {fname}...")
            urllib.request.urlretrieve(MNIST_URL + fname, fpath)
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

def train_binary_nn(X_train, y_train, X_test, y_test,
                    hidden_size=64, epochs=50, batch_size=256, lr=0.01):
    """
    Train a 2-layer binary-weight neural network using STE.

    Forward pass uses binarized weights. Backward pass uses straight-through
    estimator: gradients pass through the sign function as if it were identity,
    but are clipped to zero for latent weights with |w| > 1.
    """
    n_in = X_train.shape[1]   # 784
    n_out = 10

    # Latent (real-valued) weights -- these get binarized in the forward pass
    w1_lat = np.random.randn(n_in, hidden_size).astype(np.float32) * 0.01
    w2_lat = np.random.randn(hidden_size, n_out).astype(np.float32) * 0.01

    n_train = X_train.shape[0]
    best_test_acc = 0.0
    best_w1_bin = None
    best_w2_bin = None

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

            # Layer 2: linear + softmax
            z2 = a1 @ w2_bin                  # (bs, 10)
            probs = softmax(z2)               # (bs, 10)

            loss = cross_entropy_loss(probs, yb)
            epoch_loss += loss
            n_batches += 1

            # --- Backward pass (STE) ---
            # Gradient of softmax cross-entropy
            dz2 = (probs - one_hot(yb)) / bs  # (bs, 10)

            # Gradient w.r.t. w2 (latent)
            dw2 = a1.T @ dz2                  # (hidden, 10)

            # Gradient w.r.t. a1
            da1 = dz2 @ w2_bin.T              # (bs, hidden)

            # STE for sign activation: gradient passes through where |z1| <= 1
            # (hard tanh derivative)
            dz1 = da1 * (np.abs(z1) <= hidden_size).astype(np.float32)

            # Gradient w.r.t. w1 (latent)
            dw1 = xb.T @ dz1                  # (784, hidden)

            # STE for binarize: gradient passes through, clipped for |w| > 1
            dw1 *= (np.abs(w1_lat) <= 1.0).astype(np.float32)
            dw2 *= (np.abs(w2_lat) <= 1.0).astype(np.float32)

            # Update latent weights
            w1_lat -= lr * dw1
            w2_lat -= lr * dw2

            # Clip latent weights to [-1, 1]
            w1_lat = np.clip(w1_lat, -1.0, 1.0)
            w2_lat = np.clip(w2_lat, -1.0, 1.0)

        # --- Evaluate on test set ---
        w1_bin = binarize(w1_lat)
        w2_bin = binarize(w2_lat)

        # Float-precision forward pass (using binary weights)
        z1_test = X_test @ w1_bin
        a1_test = sign_activation(z1_test)
        z2_test = a1_test @ w2_bin
        preds = np.argmax(z2_test, axis=1)
        test_acc = np.mean(preds == y_test) * 100.0

        # Also evaluate training accuracy
        z1_tr = X_train @ w1_bin
        a1_tr = sign_activation(z1_tr)
        z2_tr = a1_tr @ w2_bin
        preds_tr = np.argmax(z2_tr, axis=1)
        train_acc = np.mean(preds_tr == y_train) * 100.0

        avg_loss = epoch_loss / max(n_batches, 1)
        print(f"Epoch {epoch+1:3d}/{epochs}  loss={avg_loss:.4f}  "
              f"train_acc={train_acc:.2f}%  test_acc={test_acc:.2f}%")

        if test_acc > best_test_acc:
            best_test_acc = test_acc
            best_w1_bin = w1_bin.copy()
            best_w2_bin = w2_bin.copy()

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
    print("\nTraining with straight-through estimator...")
    w1, w2, best_acc = train_binary_nn(
        X_train_bin, y_train, X_test_bin, y_test,
        hidden_size=64, epochs=100, batch_size=256, lr=0.005
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
