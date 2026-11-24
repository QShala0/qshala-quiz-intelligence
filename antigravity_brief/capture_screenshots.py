import subprocess
import time
import os

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT_DIR = os.path.abspath(r"antigravity_brief\screenshots")

ROUTES = [
    ("home", "http://localhost:3000/"),
    ("knowledge-base", "http://localhost:3000/knowledge-base"),
    ("generate", "http://localhost:3000/generate"),
    ("quizzes", "http://localhost:3000/quizzes"),
    ("uploads", "http://localhost:3000/uploads"),
    ("settings", "http://localhost:3000/settings"),
    ("design", "http://localhost:3000/design"),
    ("not-found", "http://localhost:3000/non-existent-page-for-404"),
]

SIZES = [
    ("desktop-1440", 1440, 900),
    ("mobile-390", 390, 844),
]

os.makedirs(OUT_DIR, exist_ok=True)

for name, url in ROUTES:
    for size_name, w, h in SIZES:
        out_file = os.path.join(OUT_DIR, f"{name}_{size_name}.png")
        cmd = [
            EDGE,
            "--headless=new",
            f"--screenshot={out_file}",
            f"--window-size={w},{h}",
            "--hide-scrollbars",
            "--disable-gpu",
            url
        ]
        print(f"Capturing {name} at {size_name}...")
        try:
            subprocess.run(cmd, check=True, timeout=15)
            if os.path.exists(out_file):
                print(f"  Saved: {out_file} ({os.path.getsize(out_file) // 1024} KB)")
        except Exception as e:
            print(f"  Error capturing {name}: {e}")

print("All captures completed.")
