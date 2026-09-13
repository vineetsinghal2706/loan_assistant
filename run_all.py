#!/usr/bin/env python3
"""
Run this once to materialize the full Loan Eligibility Assistant repository
in this folder: it executes every scaffold_*.py script found alongside it,
in order, and each of those writes its own set of files/folders.

Usage:
    python run_all.py

Safe to re-run - it will simply overwrite the generated files.
"""
import glob
import os
import subprocess
import sys


def main():
    folder = os.path.dirname(os.path.abspath(__file__))
    scripts = sorted(glob.glob(os.path.join(folder, "scaffold_*.py")))
    if not scripts:
        print("No scaffold_*.py files found next to run_all.py.")
        sys.exit(1)

    for script in scripts:
        name = os.path.basename(script)
        print(f"\n--- running {name} ---")
        subprocess.check_call([sys.executable, script])

    print("\nAll done. Your repository is ready under:")
    print(folder)
    print("\nNext steps:")
    print("  1. cp .env.example .env   (or copy on Windows)")
    print("  2. docker compose up --build")
    print("  3. open http://localhost:8501 (chat UI) and http://localhost:3000 (Grafana)")


if __name__ == "__main__":
    main()
