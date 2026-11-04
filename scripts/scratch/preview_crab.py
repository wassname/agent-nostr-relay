"""Render and check the real TV lobster through the maintained browser test. -- PI/OpenAI"""
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
env = os.environ.copy()
env.setdefault("SCREENSHOT_DIR", str(root / "slop/designs/tv-lobster"))
subprocess.run(["uv", "run", str(root / "search/test_barkeep.py")], cwd=root, env=env, check=True)
