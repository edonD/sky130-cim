#!/usr/bin/env python3
"""Sweep Cbl_extra_ff to find optimal BL capacitance for 64-row operation."""

import numpy as np
from evaluate import evaluate, score, passes_specs, spec_summary, VDD

# Sweep C_BL for 64x8 array
cbl_values = [5000, 8000, 10000, 12000, 15000]

print("C_BL Sweep for 64x8 Array")
print("=" * 80)
print(f"{'Cbl_extra(fF)':<15} {'RMSE(%)':<10} {'MaxErr(%)':<10} {'CompTime(ns)':<15} {'Power(mW)':<10} {'Score'}")
print("-" * 80)

best_score = 0
best_cbl = 10000
best_results = None

for cbl in cbl_values:
    params = {
        "Wpre": 4.0,
        "Lpre": 0.15,
        "Tpre_ns": 5.0,
        "Cbl_extra_ff": float(cbl),
    }

    results = evaluate(params=params, n_rows=64, n_cols=8, n_tests=5,
                       verbose=False, seed=123)

    if results:
        s = score(results)
        passed = passes_specs(results)
        print(f"{cbl:<15} {results['mvm_rmse_pct']:<10.3f} {results['max_error_pct']:<10.3f} "
              f"{results['compute_time_ns']:<15.2f} {results['power_mw']:<10.4f} {s:.2f} {'PASS' if passed else 'FAIL'}")

        # Use a combined metric: minimize RMSE + MaxErr while passing all specs
        if passed:
            # Prefer lower RMSE and MaxErr
            combined = results['mvm_rmse_pct'] + results['max_error_pct']
            if best_results is None or combined < (best_results['mvm_rmse_pct'] + best_results['max_error_pct']):
                best_score = s
                best_cbl = cbl
                best_results = results

print(f"\nBest C_BL: {best_cbl} fF")
if best_results:
    print(f"\n{spec_summary(best_results)}")
