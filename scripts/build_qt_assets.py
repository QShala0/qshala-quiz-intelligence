"""One-off asset pipeline for the QShala web rebrand.

Turns the raw designer files into web-ready assets:
  QT_CAT/*.png                      -> frontend/public/qt/<pose>.webp  (trimmed, <=480px)
  school templete/Rebrand font ...  -> frontend/src/fonts/CaustenRound-*.woff2

The raw 4167px originals are never copied into the app. Re-run any time:
  python scripts/build_qt_assets.py
"""
from __future__ import annotations

import re
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CAT_DIR = ROOT / "QT_CAT"
OUT_QT = ROOT / "frontend" / "public" / "qt"
FONT_SRC = ROOT / "school templete" / "Rebrand font family"
OUT_FONTS = ROOT / "frontend" / "src" / "fonts"

MAX_PX = 480

# "QT in action-NN" file number -> semantic pose name used by <QT pose="..." />
POSES = {
    "01": "sit",
    "02": "chase-blue",
    "03": "chase-yellow",
    "04": "chase-green",
    "05": "professor",
    "06": "box-blue",
    "07": "box-yellow",
    "08": "box-green",
    "09": "curled",
    "10": "fishbowl",
    "11": "stretch",
    "12": "round",
    "13": "whiskers",
    "14": "sprawl",
    "15": "wide-eyed",
    "B-15": "yarn-mat",
    "16": "fluffy",
    "17": "alert",
    "18": "yarn-green",
    "19": "yarn-blue",
    "20": "yarn-yellow",
    "21": "happy",
    "22": "sleepy",
    "23": "dizzy",
    "24": "door-green",
    "25": "door-yellow",
    "26": "door-blue",
}

FONTS = {  # weight -> source file stem
    400: "Regular",
    500: "Medium",
    600: "Semi Bold",
    700: "Bold",
    800: "Extra Bold",
    900: "Black",
}


def build_poses() -> None:
    OUT_QT.mkdir(parents=True, exist_ok=True)
    total = 0
    for png in sorted(CAT_DIR.glob("QT in action*.png")):
        m = re.search(r"action[-_ ]*([A-Za-z]*-?\d+)", png.stem)
        key = m.group(1).lstrip("-") if m else None
        # normalise "_B-15" -> "B-15"
        key = key.replace("_", "") if key else key
        name = POSES.get(key)
        if not name:
            print(f"  skip (unmapped): {png.name}")
            continue
        im = Image.open(png).convert("RGBA")
        bbox = im.getchannel("A").getbbox()
        if bbox:
            im = im.crop(bbox)
        im.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
        out = OUT_QT / f"{name}.webp"
        im.save(out, "WEBP", quality=88, method=6)
        total += out.stat().st_size
        print(f"  {png.name:28s} -> {out.name:18s} {im.size} {out.stat().st_size // 1024} KB")
    print(f"poses total: {total // 1024} KB")


def build_fonts() -> None:
    OUT_FONTS.mkdir(parents=True, exist_ok=True)
    total = 0
    for weight, stem in FONTS.items():
        src = FONT_SRC / f"Trustha - Causten Round {stem}.otf"
        f = TTFont(src)
        f.flavor = "woff2"
        out = OUT_FONTS / f"CaustenRound-{weight}.woff2"
        f.save(out)
        total += out.stat().st_size
        print(f"  {src.name} -> {out.name} {out.stat().st_size // 1024} KB")
    print(f"fonts total: {total // 1024} KB")


if __name__ == "__main__":
    build_poses()
    build_fonts()
