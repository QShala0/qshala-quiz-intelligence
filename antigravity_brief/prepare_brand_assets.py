"""Prepare web-ready brand assets from the raw designer folder.

Run from repo root:  python antigravity_brief/prepare_brand_assets.py [--dry-run]
Needs: Pillow. Outputs under frontend/public/.
Idempotent; never modifies the raw folder.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageSequence

ROOT = Path(__file__).resolve().parent.parent
BE = ROOT / "OneDrive_2026-11-24" / "Brand Elements"
PUB = ROOT / "frontend" / "public"
APP = ROOT / "frontend" / "src" / "app"

# source svg stem -> output slug (see ASSET_MAP.md)
SVGS = {
    "QT normal": "normal", "QT happy": "happy", "QT lookign up": "looking-up",
    "QT cute thinking": "thinking", "QT question with normal face": "question",
    "QT tail Question mark": "tail-question", "QT sherlock": "sherlock",
    "QT playing": "playing", "QT playing 2": "playing-2", "QT playing with butterfly": "butterfly",
    "QT quizzing": "quizzing", "QT puzzle": "puzzle", "QT reading": "reading",
    "QT with headphone": "headphones", "Qt with headphone": "headphones",
    "QT holding camera": "camera", "QT jumping": "jumping", "Qt jumping 2": "jumping-2",
    "Qt winner": "winner", "QT with trophy": "trophy", "QT superhero": "superhero",
    "QT scared": "scared", "QT shock": "shock", "QT not happy": "sad", "QT peaking": "peeking",
    "Qt peaking 2": "peeking-2", "QT Idea": "idea", "QT Sleeping": "sleeping",
    "Qt Laydown": "laydown", "QT laydown 2": "laydown-2", "QT anounce": "announce",
    "QT taling": "talking", "QT professional": "professional", "QT lawyer": "lawyer",
    "Qt with kid": "with-kid", "Qt travel": "travel", "QT yoga": "yoga", "QT 1": "base",
}

LOGOS = {  # source file -> (output name, max width)
    "QShala Logo with tag and QT.png": ("logo-horizontal-black.png", 1200),
    "QShala Logo.png": ("logo-black.png", 800),
    "QShala Logo with tag.png": ("logo-tagline-black.png", 1000),
    "QShala Logo W_1.png": ("logo-white.png", 1000),
}

COLOURED_LOGOS = {
    "logo-sky.png": (48, 178, 231),     # #30B2E7
    "logo-grass.png": (117, 181, 67),   # #75B543
    "logo-sun.png": (253, 185, 19),     # #FDB913
}

LANDING = {
    "QT for landing page_LandD.png": "landing-ld.webp",
    "QT for landing page_Onboarding.png": "landing-onboarding.webp",
    "QT for landing page_People and Culture.png": "landing-people.webp",
}

ANIMS = {
    "QT Celebrate.gif": "celebrate", "QT Letsget started.gif": "lets-get-started",
    "QT Magnify.gif": "magnify", "QT Megaphone.gif": "megaphone",
    "QT Animations correct answer (1).gif": "correct-answer",
}


def resize(src: Path, dst: Path, max_w: int, dry: bool) -> None:
    print(f"  {src.name} -> {dst.relative_to(ROOT)} (<= {max_w}px)")
    if dry:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(src).convert("RGBA")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    if dst.suffix == ".webp":
        im.save(dst, "WEBP", quality=88, method=6)
    else:
        im.save(dst, optimize=True)


def create_coloured_logos(src: Path, dry: bool) -> None:
    """Create horizontal logo variants with black cat and coloured wordmark."""
    if dry:
        return
    im = Image.open(src).convert("RGBA")
    # Resize to 1200w first
    if im.width > 1200:
        im = im.resize((1200, round(im.height * 1200 / im.width)), Image.LANCZOS)
    w, h = im.size
    cat_split_x = int(w * 0.28)  # Cat is on the left 28% of the width

    for name, (cr, cg, cb) in COLOURED_LOGOS.items():
        dst = PUB / "brand" / name
        variant = im.copy()
        pixels = variant.load()
        for y in range(h):
            for x in range(cat_split_x, w):
                r, g, b, a = pixels[x, y]
                if a > 0:
                    pixels[x, y] = (cr, cg, cb, a)
        variant.save(dst, optimize=True)
        print(f"  Created coloured logo -> {dst.relative_to(ROOT)}")


def create_app_icons(cat_crop: Image.Image, dry: bool) -> None:
    """Generate official brand favicons & app icons (cat on Sun-yellow rounded rect)."""
    if dry:
        return
    # 512x512 icon.png
    im512 = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw512 = ImageDraw.Draw(im512)
    draw512.rounded_rectangle((0, 0, 512, 512), radius=112, fill=(253, 185, 19, 255))
    cw512 = 380
    ch512 = int(cat_crop.height * (cw512 / cat_crop.width))
    c_resized512 = cat_crop.resize((cw512, ch512), Image.LANCZOS)
    im512.paste(c_resized512, ((512 - cw512) // 2, (512 - ch512) // 2), c_resized512)
    im512.save(APP / "icon.png", optimize=True)
    print("  Created icon.png (512x512)")

    # 180x180 apple-icon.png
    im180 = Image.new("RGBA", (180, 180), (0, 0, 0, 0))
    draw180 = ImageDraw.Draw(im180)
    draw180.rounded_rectangle((0, 0, 180, 180), radius=40, fill=(253, 185, 19, 255))
    cw180 = 136
    ch180 = int(cat_crop.height * (cw180 / cat_crop.width))
    c_resized180 = cat_crop.resize((cw180, ch180), Image.LANCZOS)
    im180.paste(c_resized180, ((180 - cw180) // 2, (180 - ch180) // 2), c_resized180)
    im180.save(APP / "apple-icon.png", optimize=True)
    print("  Created apple-icon.png (180x180)")

    # favicon.ico (multi-size: 48, 32, 16)
    im512.save(APP / "favicon.ico", format="ICO", sizes=[(48, 48), (32, 32), (16, 16)])
    print("  Created favicon.ico (48, 32, 16)")


def create_og_image(dry: bool) -> None:
    """Generate official OG sharing card (1200x630, cream bg, tagline logo, yellow bar)."""
    if dry:
        return
    bg_color = (255, 246, 225, 255)  # Cream #FFF6E1
    im = Image.new("RGBA", (1200, 630), bg_color)
    draw = ImageDraw.Draw(im)

    # Top signature bar in Joy of Quest (#FDB913)
    draw.rectangle((0, 0, 1200, 16), fill=(253, 185, 19, 255))

    # Center logo with tagline
    logo_path = PUB / "brand" / "logo-tagline-black.png"
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        target_w = 640
        target_h = int(logo.height * (target_w / logo.width))
        logo_res = logo.resize((target_w, target_h), Image.LANCZOS)
        x = (1200 - target_w) // 2
        y = (630 - target_h) // 2 - 20
        im.paste(logo_res, (x, y), logo_res)

        # Signature Sun-yellow rule under the logo
        rule_y = y + target_h + 30
        draw.rectangle(((1200 - 320) // 2, rule_y, (1200 + 320) // 2, rule_y + 4), fill=(253, 185, 19, 255))

    dst = PUB / "brand" / "og.png"
    im.save(dst, optimize=True)
    print(f"  Created og.png (1200x630) -> {dst.relative_to(ROOT)}")


def convert_animations_pillow(dry: bool) -> None:
    """Convert GIF animations to optimized animated WebP using Pillow."""
    if dry:
        return
    anim_dir = PUB / "qt" / "anim"
    anim_dir.mkdir(parents=True, exist_ok=True)
    for name, slug in ANIMS.items():
        src = BE / "QT" / "QT Animations" / name
        dst = anim_dir / f"{slug}.webp"
        if not src.exists():
            continue
        try:
            im = Image.open(src)
            frames = []
            durations = []
            max_size = 220 if "Celebrate" in name else 360
            for frame in ImageSequence.Iterator(im):
                f = frame.convert("RGBA")
                if f.width > max_size or f.height > max_size:
                    f.thumbnail((max_size, max_size), Image.LANCZOS)
                frames.append(f)
                durations.append(frame.info.get("duration", 40) or 40)

            if frames:
                # Subsample if frame count is large to keep under 400KB
                step = 2 if len(frames) > 70 else 1
                if step > 1:
                    frames = frames[::step]
                    durations = [d * step for d in durations[::step]]

                frames[0].save(
                    dst,
                    format="WEBP",
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=0,
                    quality=75,
                    method=4,
                )
                print(f"  {name} -> {dst.relative_to(ROOT)} ({dst.stat().st_size // 1024} KB)")
        except Exception as e:
            print(f"  Failed to convert {name}: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    dry = ap.parse_args().dry_run
    if not BE.exists():
        raise SystemExit(f"Missing {BE}")

    print("Logos")
    for name, (out, w) in LOGOS.items():
        resize(BE / "QT" / "QShala Updated Logo" / name, PUB / "brand" / out, w, dry)

    print("Coloured logos")
    create_coloured_logos(BE / "QT" / "QShala Updated Logo" / "QShala Logo with tag and QT.png", dry)

    print("QT SVGs")
    svg_dir = BE / "QT" / "QT SVG"
    done: set[str] = set()
    for stem, slug in SVGS.items():
        src = svg_dir / f"{stem}.svg"
        if not src.exists() or slug in done:
            continue
        done.add(slug)
        dst = PUB / "qt" / "svg" / f"{slug}.svg"
        print(f"  {src.name} -> {dst.relative_to(ROOT)}")
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            txt = src.read_text(encoding="utf-8", errors="ignore")
            txt = re.sub(r"<\?xml[^>]*\?>\s*", "", txt)
            dst.write_text(txt, encoding="utf-8")

    # Also save QT normal.svg as qt-mark.svg in brand folder
    if not dry and (svg_dir / "QT normal.svg").exists():
        dst_mark = PUB / "brand" / "qt-mark.svg"
        txt = (svg_dir / "QT normal.svg").read_text(encoding="utf-8", errors="ignore")
        txt = re.sub(r"<\?xml[^>]*\?>\s*", "", txt)
        dst_mark.write_text(txt, encoding="utf-8")
        print(f"  QT normal.svg -> {dst_mark.relative_to(ROOT)}")

    missing = [s for s in SVGS if not (svg_dir / f"{s}.svg").exists()]
    if missing:
        print("  (not found, check spelling in folder):", ", ".join(missing))

    print("Landing illustrations")
    for name, out in LANDING.items():
        resize(BE / "QT" / name, PUB / "brand" / out, 1600, dry)

    print("Favicon / app icons (cat on Sun yellow rounded square)")
    cat_orig = Image.open(APP / "icon.png")
    cat_crop = cat_orig.crop(cat_orig.getbbox())
    create_app_icons(cat_crop, dry)

    print("OG image")
    create_og_image(dry)

    print("Animations (GIF -> WebP)")
    convert_animations_pillow(dry)

    print("\nDone. Checking sizes under 150KB (except animations <= 400KB):")
    if not dry:
        check = subprocess.run(
            ["powershell", "-Command",
             "Get-ChildItem frontend/public/brand,frontend/public/qt -Recurse | Where-Object { -not $_.PSIsContainer } | sort Length -desc | select -first 15 Name, Length"],
            capture_output=True, text=True
        )
        print(check.stdout)


if __name__ == "__main__":
    main()

