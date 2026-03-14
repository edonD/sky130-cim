#!/usr/bin/env python3
"""
orchestrate.py — CIM Tile Build Orchestrator

Checks the status of each block, enforces dependency order,
and propagates measured interface values between blocks.

Usage:
    python orchestrate.py              # Show status of all blocks
    python orchestrate.py --propagate  # Push upstream measurements into downstream specs
    python orchestrate.py --launch     # Print which blocks are ready to launch
"""

import os
import sys
import json
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
BLOCKS_DIR = PROJECT_ROOT / "blocks"

# Block definitions with dependencies
BLOCKS = {
    "bitcell": {
        "path": BLOCKS_DIR / "bitcell",
        "depends_on": [],
        "parallel_group": 1,
        "description": "8T SRAM CIM bitcell",
    },
    "adc": {
        "path": BLOCKS_DIR / "adc",
        "depends_on": [],
        "parallel_group": 1,
        "description": "6-bit SAR ADC",
    },
    "pwm-driver": {
        "path": BLOCKS_DIR / "pwm-driver",
        "depends_on": [],
        "parallel_group": 1,
        "description": "4-bit PWM wordline driver",
    },
    "array": {
        "path": BLOCKS_DIR / "array",
        "depends_on": ["bitcell", "pwm-driver"],
        "parallel_group": 2,
        "description": "64x64 CIM compute array",
    },
    "integration": {
        "path": BLOCKS_DIR / "integration",
        "depends_on": ["array", "adc"],
        "parallel_group": 3,
        "description": "Full tile + MNIST inference",
    },
}


def get_block_status(block_name: str) -> dict:
    """Check the status of a block."""
    block = BLOCKS[block_name]
    path = block["path"]

    status = {
        "name": block_name,
        "description": block["description"],
        "parallel_group": block["parallel_group"],
        "depends_on": block["depends_on"],
        "has_specs": (path / "specs.json").exists(),
        "has_program": (path / "program.md").exists(),
        "has_design": (path / "design.cir").exists(),
        "has_evaluate": (path / "evaluate.py").exists(),
        "has_parameters": (path / "parameters.csv").exists(),
        "has_best_params": (path / "best_parameters.csv").exists(),
        "has_measurements": (path / "measurements.json").exists(),
        "has_readme": (path / "README.md").exists(),
        "measurements": None,
        "score": None,
    }

    # Check if block is complete (has measurements and best params)
    if status["has_measurements"]:
        try:
            with open(path / "measurements.json") as f:
                meas = json.load(f)
            status["measurements"] = meas
            status["score"] = meas.get("score", None)
        except (json.JSONDecodeError, IOError):
            pass

    # Determine overall state
    if status["has_measurements"] and status["has_best_params"]:
        status["state"] = "COMPLETE"
    elif status["has_design"] and status["has_evaluate"]:
        status["state"] = "READY"
    elif status["has_specs"] and status["has_program"]:
        status["state"] = "SETUP"
    else:
        status["state"] = "EMPTY"

    return status


def check_dependencies_met(block_name: str, statuses: dict) -> bool:
    """Check if all dependencies of a block are complete."""
    block = BLOCKS[block_name]
    for dep in block["depends_on"]:
        if statuses[dep]["state"] != "COMPLETE":
            return False
    return True


def print_status():
    """Print the status of all blocks."""
    statuses = {name: get_block_status(name) for name in BLOCKS}

    print()
    print("=" * 70)
    print("  CIM TILE — BLOCK STATUS")
    print("=" * 70)

    # Group by parallel group
    for group in sorted(set(b["parallel_group"] for b in BLOCKS.values())):
        group_blocks = [n for n, b in BLOCKS.items() if b["parallel_group"] == group]
        parallel_label = "parallel" if len(group_blocks) > 1 else "sequential"
        print(f"\n  Phase {group} ({parallel_label}):")

        for name in group_blocks:
            s = statuses[name]
            deps_met = check_dependencies_met(name, statuses)

            # State indicator
            if s["state"] == "COMPLETE":
                indicator = "[DONE]"
                score_str = f" score={s['score']:.2f}" if s['score'] is not None else ""
            elif s["state"] == "READY":
                if deps_met:
                    indicator = "[READY TO RUN]"
                else:
                    waiting = [d for d in s["depends_on"] if statuses[d]["state"] != "COMPLETE"]
                    indicator = f"[WAITING: {', '.join(waiting)}]"
                score_str = ""
            elif s["state"] == "SETUP":
                indicator = "[SETUP ONLY]"
                score_str = ""
            else:
                indicator = "[EMPTY]"
                score_str = ""

            deps_str = f" (needs: {', '.join(s['depends_on'])})" if s['depends_on'] else ""
            print(f"    {name:<15} {indicator:<20} {s['description']}{deps_str}{score_str}")

    # Summary
    complete = sum(1 for s in statuses.values() if s["state"] == "COMPLETE")
    total = len(BLOCKS)
    print(f"\n  Progress: {complete}/{total} blocks complete")

    # What to do next
    print(f"\n  Next actions:")
    for name in BLOCKS:
        s = statuses[name]
        deps_met = check_dependencies_met(name, statuses)
        if s["state"] != "COMPLETE" and deps_met:
            if s["state"] in ("READY", "SETUP"):
                print(f"    -> Launch agent for: {name}")
            else:
                print(f"    -> Set up files for: {name}")

    blocked = [n for n in BLOCKS if not check_dependencies_met(n, statuses)
               and statuses[n]["state"] != "COMPLETE"]
    if blocked:
        print(f"\n  Blocked (waiting on dependencies): {', '.join(blocked)}")

    print(f"\n{'=' * 70}\n")
    return statuses


def propagate_measurements():
    """Push upstream measurements into downstream block specs/configs."""
    statuses = {name: get_block_status(name) for name in BLOCKS}

    print("\n--- Propagating interface values ---\n")

    # Bitcell → Array: push I_READ, C_BL_CELL into array config
    if statuses["bitcell"]["state"] == "COMPLETE":
        meas = statuses["bitcell"]["measurements"]
        if meas:
            config_path = BLOCKS["array"]["path"] / "upstream_config.json"
            upstream = {
                "bitcell": {
                    "i_read_ua": meas.get("i_read_ua"),
                    "i_leak_na": meas.get("i_leak_na"),
                    "c_bl_cell_ff": meas.get("c_bl_cell_ff"),
                    "t_read_ns": meas.get("t_read_ns"),
                    "cell_area_um2": meas.get("cell_area_um2"),
                    "source": str(BLOCKS["bitcell"]["path"] / "measurements.json"),
                }
            }
            # Merge with existing
            if config_path.exists():
                with open(config_path) as f:
                    existing = json.load(f)
                existing.update(upstream)
                upstream = existing
            with open(config_path, "w") as f:
                json.dump(upstream, f, indent=2)
            print(f"  bitcell → array: wrote {config_path}")

    # PWM → Array: push T_LSB into array config
    if statuses["pwm-driver"]["state"] == "COMPLETE":
        meas = statuses["pwm-driver"]["measurements"]
        if meas:
            config_path = BLOCKS["array"]["path"] / "upstream_config.json"
            upstream_pwm = {
                "pwm_driver": {
                    "t_lsb_ns": meas.get("t_lsb_ns"),
                    "rise_time_ns": meas.get("rise_time_ns"),
                    "fall_time_ns": meas.get("fall_time_ns"),
                    "source": str(BLOCKS["pwm-driver"]["path"] / "measurements.json"),
                }
            }
            if config_path.exists():
                with open(config_path) as f:
                    existing = json.load(f)
                existing.update(upstream_pwm)
                upstream_pwm = existing
            with open(config_path, "w") as f:
                json.dump(upstream_pwm, f, indent=2)
            print(f"  pwm-driver → array: wrote {config_path}")

    # Array → Integration: push MVM characteristics
    if statuses["array"]["state"] == "COMPLETE":
        meas = statuses["array"]["measurements"]
        if meas:
            config_path = BLOCKS["integration"]["path"] / "upstream_config.json"
            upstream = {
                "array": {
                    "mvm_rmse_pct": meas.get("mvm_rmse_pct"),
                    "compute_time_ns": meas.get("compute_time_ns"),
                    "v_bl_range": meas.get("v_bl_range"),
                    "power_mw": meas.get("power_mw"),
                    "source": str(BLOCKS["array"]["path"] / "measurements.json"),
                }
            }
            if config_path.exists():
                with open(config_path) as f:
                    existing = json.load(f)
                existing.update(upstream)
                upstream = existing
            with open(config_path, "w") as f:
                json.dump(upstream, f, indent=2)
            print(f"  array → integration: wrote {config_path}")

    # ADC → Integration: push ADC characteristics
    if statuses["adc"]["state"] == "COMPLETE":
        meas = statuses["adc"]["measurements"]
        if meas:
            config_path = BLOCKS["integration"]["path"] / "upstream_config.json"
            upstream_adc = {
                "adc": {
                    "dnl_lsb": meas.get("dnl_lsb"),
                    "inl_lsb": meas.get("inl_lsb"),
                    "enob": meas.get("enob"),
                    "conversion_time_ns": meas.get("conversion_time_ns"),
                    "power_uw": meas.get("power_uw"),
                    "source": str(BLOCKS["adc"]["path"] / "measurements.json"),
                }
            }
            if config_path.exists():
                with open(config_path) as f:
                    existing = json.load(f)
                existing.update(upstream_adc)
                upstream_adc = existing
            with open(config_path, "w") as f:
                json.dump(upstream_adc, f, indent=2)
            print(f"  adc → integration: wrote {config_path}")

    print("\n--- Propagation complete ---\n")


def print_launch_info():
    """Print which blocks can be launched right now."""
    statuses = {name: get_block_status(name) for name in BLOCKS}

    launchable = []
    for name in BLOCKS:
        s = statuses[name]
        if s["state"] != "COMPLETE" and check_dependencies_met(name, statuses):
            if s["state"] in ("READY", "SETUP"):
                launchable.append(name)

    if not launchable:
        complete = sum(1 for s in statuses.values() if s["state"] == "COMPLETE")
        if complete == len(BLOCKS):
            print("\nAll blocks complete! The CIM tile is ready.")
        else:
            blocked = [n for n in BLOCKS if statuses[n]["state"] != "COMPLETE"]
            print(f"\nNo blocks ready to launch. Blocked: {', '.join(blocked)}")
            for b in blocked:
                waiting = [d for d in BLOCKS[b]["depends_on"]
                           if statuses[d]["state"] != "COMPLETE"]
                if waiting:
                    print(f"  {b} waiting on: {', '.join(waiting)}")
    else:
        print(f"\nBlocks ready to launch ({len(launchable)}):")
        for name in launchable:
            block_path = BLOCKS[name]["path"]
            print(f"\n  {name}:")
            print(f"    cd {block_path}")
            print(f"    # Launch your AI agent here pointing at this directory")


def main():
    parser = argparse.ArgumentParser(description="CIM Tile Build Orchestrator")
    parser.add_argument("--propagate", action="store_true",
                        help="Propagate upstream measurements to downstream blocks")
    parser.add_argument("--launch", action="store_true",
                        help="Show which blocks are ready to launch")
    args = parser.parse_args()

    if args.propagate:
        propagate_measurements()
    elif args.launch:
        print_launch_info()

    print_status()


if __name__ == "__main__":
    main()
