#!/usr/bin/env python3
"""Rebuild asset library from disk + extract any missing zips. Verbose output."""
# ── vibe-editing portable path bootstrap (auto-inserted) ──
import os as _os, sys as _sys
import pathlib as _pl
def _acq_root():
    r = _os.environ.get("VIBE_PIPELINE_ROOT") or _os.environ.get("CLAUDE_PLUGIN_ROOT")
    if r and _os.path.isdir(_os.path.join(r, ".claude-plugin")):
        return r
    d = _os.path.dirname(_os.path.abspath(__file__))
    while d != _os.path.dirname(d):
        if _os.path.isdir(_os.path.join(d, ".claude-plugin")):
            return d
        d = _os.path.dirname(d)
    return _os.path.dirname(_os.path.abspath(__file__))
VIBE_ROOT = _acq_root()
VIBE_SHARED = _os.path.join(VIBE_ROOT, "lib", "_shared")
VIBE_SKILLS = _os.path.join(VIBE_ROOT, "skills")
VIBE_VAULT  = _os.path.join(VIBE_ROOT, "vault")
VIBE_ASSETS = _os.environ.get("VIBE_ASSETS") or _os.path.join(VIBE_ROOT, "assets")
def _acq(p):
    parts = [x for x in str(p).strip("/").split("/") if x]
    if parts and parts[0] == "_shared":
        return _pl.Path(_os.path.join(VIBE_ROOT, "lib", *parts))
    return _pl.Path(_os.path.join(VIBE_SKILLS, *parts))
def _acqv(p):
    return _pl.Path(_os.path.join(VIBE_VAULT, *[x for x in str(p).strip("/").split("/") if x]))
if VIBE_SHARED not in _sys.path:
    _sys.path.insert(0, VIBE_SHARED)
# ── end bootstrap ──
import json
import re
import subprocess
from pathlib import Path

ASSETS = VIBE_ASSETS
DOWNLOADS = Path.home() / "Downloads"

CATEGORIES = [
    (r"Action_Seamless_Transitions", "transitions"),
    (r"Camera_Flash_Transitions", "transitions"),
    (r"Cinematic_Transitions", "transitions"),
    (r"Dark_Horror_Transitions", "transitions"),
    (r"Documentary_Offset_Transitions", "transitions"),
    (r"Film_Damage_Transitions", "transitions"),
    (r"Freeze_Frame_Transitions", "transitions"),
    (r"Glitch_Transitions", "transitions"),
    (r"Horror_Transitions", "transitions"),
    (r"Particles_Transitions", "transitions"),
    (r"Ripped_Frame_Transitions", "transitions"),
    (r"Seamless_Transitions", "transitions"),
    (r"Spin_Transitions", "transitions"),
    (r"Universal_Vertical_Transitions", "transitions"),
    (r"Vertical_Glitch_Transitions", "transitions"),
    (r"Whip_Noise_Transitions", "transitions"),
    (r"Zoom_Transitions", "transitions"),
    (r"Pack_Of_Film_Burn_Transitions", "transitions"),
    (r"Pack_Of_6_Film_Burn", "transitions"),
    (r"Pack_Of_10_Frame_Film_Light", "transitions"),
    (r"Pack_Of_2_Abstract_Tearing", "transitions"),
    (r"Pack_Of_4_Holographic_3D_Transitions", "transitions"),
    (r"Light_Leaks", "overlays"),
    (r"Sunlight_Effects", "overlays"),
    (r"Pack_Of_Flashing_Light_Leaks", "overlays"),
    (r"Universal_Light_Leaks", "overlays"),
    (r"Film_Burn_Effects", "overlays"),
    (r"Film_Burn_Overlay", "overlays"),
    (r"Flicker_Effects", "overlays"),
    (r"Film_Emulation", "overlays"),
    (r"Old_Film_Effect", "overlays"),
    (r"VHS_Elements", "overlays"),
    (r"Vintage_Old_Film", "overlays"),
    (r"Pack_Of_14_Black_Film_Matte", "overlays"),
    (r"Pack_Of_10_Scribble_Border", "overlays"),
    (r"Pack_Of_60_Scribbled_Elements", "overlays"),
    (r"Pack_Of_9_Holographic", "overlays"),
    (r"Pack_Of_16_Retro_3D_Tech", "overlays"),
    (r"Pack_Of_6_Abstract_3D_Objects", "overlays"),
    (r"Pack_Of_6_Toxic_Neon", "overlays"),
    (r"Pack_Of_5_Real_Glass_Cracking", "overlays"),
    (r"Inflate_Room", "overlays"),
    (r"Pack_Of_15_Holo_Iridescent_3D", "overlays"),
    (r"Pack_Of_16_Colorful_Inflated_Icons", "overlays"),
    (r"Floral_Fantasy", "overlays"),
    (r"3D_Shapes_Pack_V2", "overlays"),
    (r"Pack_Of_14_Colorful_Cartoon_Anime_Elements", "overlays"),
    (r"A_Pack_Of_72_Animated_Retro_Icons_And_Animated_Letters", "overlays"),
    (r"3D_Parallax", "effects"),
    (r"3D_Screen", "effects"),
    (r"16mm_Movie", "effects"),
    (r"Action_Zoom", "effects"),
    (r"Camera_Pack", "effects"),
    (r"Real_Camera_Shakes", "effects"),
    (r"Chromatic_Camera_Focus", "effects"),
    (r"Chromatic_Color_Grades", "luts"),
    (r"Dark_City_Color_Grades", "luts"),
    (r"70_Color_Grading_Presets", "luts"),
    (r"Dramatic_Look", "effects"),
    (r"HDR_Black", "effects"),
    (r"HDR_Look", "effects"),
    (r"Heartbeat_Effects", "effects"),
    (r"Extreme_Fire_Hits", "effects"),
    (r"Light_Speed", "effects"),
    (r"New_Double_Exposure", "effects"),
    (r"Photo_Animator", "effects"),
    (r"Prismatic_Effects", "effects"),
    (r"Retro_Poster", "effects"),
    (r"Retro_TV_Text", "effects"),
    (r"VHS_Colors", "luts"),
    (r"Underwater_Video", "effects"),
    (r"Transitions___Overlays", "overlays"),
    (r"Surface_And_Depth", "effects"),
    (r"Posters_In_Space", "effects"),
    (r"Pulse_Neon_Text", "text"),
    (r"Smoke_Titles", "text"),
    (r"Team Speaker.*Font Presets", "text"),
    (r"Text Animations", "text"),
    (r"Big_Kinetic_Type_Opener", "text"),
    (r"Cloth_Typography_Opener", "text"),
    (r"Crystal_Clear.*Glass_Titles", "text"),
    (r"Pack_Of_52_Glass_Refraction_Typefaces", "text"),
    (r"The_Chromatic_Typeface", "text"),
    (r"Bubble_Trouble_Typeface", "text"),
    (r"Audio Presets", "audio/presets"),
    (r"Old Hip Hop", "audio/music"),
    (r"^Pop-", "audio/music"),
    (r"^Trap-", "audio/music"),
    (r"Trending Music", "audio/music"),
    (r"^Tik Tok-", "audio/music"),
    (r"^Other-", "audio/music"),
    (r"Q&A Editing Assets", "misc-qa-assets"),
]

SKIP_PATTERNS = [
    r"^caption-clips-backup",
    r"^montserrat-nova",
    r"^DaVinci_Resolve",
    r"^bank statements",
    r"^drive-download",
    r"^editor-skills",
    r"Motion.Array.Hub",
]


def classify(stem):
    for pat, cat in CATEGORIES:
        if re.search(pat, stem):
            return cat
    return None


def pack_name(stem):
    n = re.sub(r"_source_\d+$", "", stem)
    n = re.sub(r"-\d{8}T\d{6}Z-\d+-\d+$", "", n)
    n = n.replace("_", " ").strip()
    return n


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    zips = sorted(DOWNLOADS.glob("*.zip"))

    extracted_now = 0
    already = 0
    skipped_mapping = []
    skipped_patterns = []
    failed = []

    for z in zips:
        if any(re.search(p, z.stem) for p in SKIP_PATTERNS):
            skipped_patterns.append(z.name)
            continue
        cat = classify(z.stem)
        if cat is None:
            skipped_mapping.append(z.name)
            continue
        pack = pack_name(z.stem)
        dest = ASSETS / cat / pack
        if dest.exists():
            already += 1
            continue
        dest.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["unzip", "-q", "-o", str(z), "-d", str(dest)],
                           check=True, capture_output=True)
            print(f"  EXTRACT  {cat}/{pack}", flush=True)
            extracted_now += 1
        except subprocess.CalledProcessError as e:
            failed.append((z.name, e.stderr.decode()[:150]))
            # Remove empty dir
            try:
                dest.rmdir()
            except OSError:
                pass

    # Now rebuild catalog from DISK — list everything in each category folder.
    catalog = {}
    if ASSETS.exists():
        for cat_dir in sorted(ASSETS.iterdir()):
            if not cat_dir.is_dir():
                continue
            cat = cat_dir.relative_to(ASSETS).as_posix()
            packs = sorted([p.name for p in cat_dir.iterdir() if p.is_dir() or p.suffix.lower() in {".cube", ".3dl", ".ttf", ".otf", ".prfpset", ".prtextstyle", ".pdf"}])
            if packs:
                catalog[cat] = packs
            # One-level-deeper categories (e.g., audio/music)
            for sub in sorted(cat_dir.iterdir()):
                if sub.is_dir():
                    subcat = sub.relative_to(ASSETS).as_posix()
                    subpacks = sorted([p.name for p in sub.iterdir() if p.is_dir() or p.suffix.lower() in {".cube", ".3dl", ".ttf", ".otf", ".prfpset", ".prtextstyle", ".pdf", ".wav", ".mp3"}])
                    if subpacks and subcat != cat:
                        catalog[subcat] = subpacks

    total_items = sum(len(v) for v in catalog.values())
    lines = [
        "# Asset Library Catalog",
        "",
        f"_{total_items} packs/files across {len(catalog)} categories._",
        "_Use these as building blocks when editing clips — each category is a different kind of visual/audio effect._",
        "",
        "## When to reach for each category",
        "",
        "- **transitions/** — between cuts. Use sparingly; per Speaker SOP the structure matters more than VFX.",
        "- **overlays/** — light leaks, film grain, 3D elements that sit on top of footage with blend modes.",
        "- **effects/** — camera shakes, zooms, chromatic effects, one-shot visual punches.",
        "- **text/** — title cards, typefaces, kinetic typography openers.",
        "- **luts/** — color grading presets (.cube / .3dl). Never apply to Modern Wisdom clips.",
        "- **audio/music/** — background tracks by genre. Always duck when Speaker is talking.",
        "- **audio/presets/** — audio processing presets for Premiere.",
        "- **reference/** — editing theory / notes (In the Blink of an Eye).",
        "",
    ]
    for cat in sorted(catalog.keys()):
        lines.append(f"## `{cat}/`")
        lines.append("")
        for p in catalog[cat]:
            lines.append(f"- {p}")
        lines.append("")
    (ASSETS / "README.md").write_text("\n".join(lines))

    print(f"\n  extracted now: {extracted_now}")
    print(f"  already present: {already}")
    print(f"  skipped (by pattern): {len(skipped_patterns)}")
    print(f"  skipped (no mapping): {len(skipped_mapping)}")
    print(f"  failed: {len(failed)}")
    print(f"\n  catalog: {total_items} items across {len(catalog)} categories → {ASSETS / 'README.md'}")

    if skipped_mapping:
        print("\nSKIPPED (need mapping):")
        for s in skipped_mapping:
            print(f"  {s}")
    if failed:
        print("\nFAILED:")
        for name, err in failed:
            print(f"  {name}: {err}")


if __name__ == "__main__":
    main()
