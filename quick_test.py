#!/usr/bin/env python3
"""
Quick test script - runs pipeline with minimal data for testing
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import run_pipeline

if __name__ == "__main__":
    print("Running quick test with 5000 records...")
    result = run_pipeline(
        limit=5000,
        year_start=2024,
        use_cache=True,
        output_dir="output_test"
    )
    print("\nQuick test complete!")
