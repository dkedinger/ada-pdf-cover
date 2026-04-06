#!/usr/bin/env python3
from __future__ import annotations
"""
add_accessibility_cover.py
──────────────────────────────────────────────────────────────────────────────
Adds a branded accessibility cover page to every PDF in a folder tree.

• Auto-installs all required dependencies on first run
• Interactive menu — configure, dry-run, then commit
• Batch processing with live progress bar + per-batch status
• Embeds the logo (reads logo.svg alongside the script,
  falls back to downloading from url if provided, or continues without logo if neither is available)
• Originals archived to  _old_pdfs/    (same subfolder structure)
• Encrypted PDFs copied to  _encrypted_pdfs/
• Unreadable / error PDFs copied to  _failed_pdfs/
──────────────────────────────────────────────────────────────────────────────

Copyright (C) 2026  Daniel Kedinger

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 0 — Auto-install missing dependencies
# ═══════════════════════════════════════════════════════════════════════════════

import importlib.util, subprocess, sys

REQUIRED_PACKAGES = {
    "pypdf":     "pypdf",
    "reportlab": "reportlab",
    "svglib":    "svglib",
    "rich":      "rich",
    "requests":  "requests",
    "lxml":      "lxml",
}

def _ensure_deps():
    missing = [
        pip for mod, pip in REQUIRED_PACKAGES.items()
        if importlib.util.find_spec(mod) is None
    ]
    if not missing:
        return
    print(f"\n📦  Installing: {', '.join(missing)}")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", *missing],
        stdout=subprocess.DEVNULL,
    )
    print("✅  Done — continuing.\n")

_ensure_deps()

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Imports
# ═══════════════════════════════════════════════════════════════════════════════

import io, shutil, time, textwrap
from pathlib import Path

import requests
import lxml.etree as ET
from svglib.svglib import SvgRenderer
from reportlab.graphics import renderPDF
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as rl_canvas
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.progress import (
    Progress, SpinnerColumn, BarColumn,
    TextColumn, TimeElapsedColumn, MofNCompleteColumn,
)
from rich.text import Text
from rich import box
from rich.rule import Rule

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_HEADER_COLOR = "#000000"
DEFAULT_TEXT_COLOR   = "#000000"
WHITE      = (1.0, 1.0, 1.0)
LIGHT_BG   = (0.95, 0.97, 0.99)


def hex_to_rgb(hex_str: str) -> tuple:
    """Convert '#RRGGBB' or 'RRGGBB' to an (r, g, b) tuple with 0-1 floats."""
    h = hex_str.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: {hex_str}")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)

ARCHIVE_DIR   = "_old_pdfs"
ENCRYPTED_DIR = "_encrypted_pdfs"
FAILED_DIR    = "_failed_pdfs"
BATCH_SIZE    = 50

LOCAL_LOGO_NAME   = "logo.svg"

console = Console()

# ═══════════════════════════════════════════════════════════════════════════════
# LOGO HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def load_logo_bytes(logo_url: str) -> bytes | None:
    """
    If logo_url is provided, download from the URL.
    Otherwise look for logo.svg next to the script.
    If neither is available, return None (no logo).
    """
    script_dir = Path(__file__).parent

    # 1. If a URL was provided, download it
    if logo_url:
        try:
            console.print(f"  [dim]Downloading logo → {logo_url}[/dim]")
            r = requests.get(logo_url, timeout=12)
            r.raise_for_status()
            console.print("  [dim green]✓ Logo downloaded.[/dim green]")
            return r.content
        except Exception as exc:
            console.print(
                f"  [yellow]⚠ Logo download failed ({exc}).[/yellow]"
            )
            # Fall through to local file check

    # 2. Look for logo.svg next to the script
    local = script_dir / LOCAL_LOGO_NAME
    if local.exists():
        console.print(f"  [dim]Logo loaded from {local.name}[/dim]")
        return local.read_bytes()

    if logo_url:
        console.print("  [yellow]⚠ No local logo.svg fallback — continuing without logo.[/yellow]")
    else:
        console.print("  [dim]No logo URL set and no logo.svg found — continuing without logo.[/dim]")
    return None


def svg_to_drawing(svg_bytes: bytes, max_w: float, max_h: float,
                   fill_color: str | None = None):
    """
    Parse SVG → scaled reportlab Drawing, or None on failure.
    If fill_color is given (e.g. 'white'), all shape elements are recoloured
    before rendering — useful for placing dark logos on coloured backgrounds.
    """
    try:
        tree = ET.fromstring(svg_bytes)

        if fill_color:
            SHAPE_TAGS = {"path", "polygon", "polyline", "rect",
                          "circle", "ellipse", "line", "g"}
            for el in tree.iter():
                tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
                if tag in SHAPE_TAGS:
                    el.set("fill", fill_color)
                    el.attrib.pop("stroke", None)

        renderer = SvgRenderer("")
        drawing  = renderer.render(tree)
        if drawing is None or drawing.width == 0 or drawing.height == 0:
            return None
        scale = min(max_w / drawing.width, max_h / drawing.height)
        drawing.width  *= scale
        drawing.height *= scale
        drawing.transform = (scale, 0, 0, scale, 0, 0)
        return drawing
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# COVER-PAGE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_cover_page(cfg: dict, logo_bytes) -> PdfReader:
    buf  = io.BytesIO()
    W, H = letter          # 612 × 792 pt
    c    = rl_canvas.Canvas(buf, pagesize=letter)

    header_rgb = hex_to_rgb(cfg["header_color"])
    text_rgb   = hex_to_rgb(cfg["text_color"])

    margin_l = 0.75 * inch
    margin_r = W - 0.75 * inch
    text_w   = margin_r - margin_l

    # ── White background ──────────────────────────────────────────────────────
    c.setFillColorRGB(*WHITE)
    c.rect(0, 0, W, H, stroke=0, fill=1)

    # ── Header banner ─────────────────────────────────────────────────────────
    banner_h = 100
    c.setFillColorRGB(*header_rgb)
    c.rect(0, H - banner_h, W, banner_h, stroke=0, fill=1)

    # Logo — recoloured white, right-aligned directly on blue banner
    if logo_bytes:
        drawing = svg_to_drawing(logo_bytes, 200, 70, fill_color="white")
        if drawing:
            lx = W - margin_l - drawing.width
            ly = H - banner_h + (banner_h - drawing.height) / 2
            renderPDF.draw(drawing, c, lx, ly)

    # Banner text (left side)
    c.setFillColorRGB(*WHITE)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin_l, H - 28, cfg["org_name"].upper())
    c.setFont("Helvetica-Bold", 22)
    c.drawString(margin_l, H - 58, "Accessibility Notice")
    c.setFont("Helvetica", 8.5)
    c.drawString(margin_l, H - 78,
                 "Archived Document  —  Alternative Format Available Upon Request")

    # ── Body helpers ──────────────────────────────────────────────────────────
    def wrap_text(text, x, y, font, size, leading=0):
        c.setFillColorRGB(*text_rgb)
        c.setFont(font, size)
        if not leading:
            leading = size + 5
        char_w = size * 0.52
        for line in textwrap.wrap(text, width=max(1, int(text_w / char_w))):
            c.drawString(x, y, line)
            y -= leading
        return y

    def section_head(label, y):
        """Section heading: top padding, bold label, small bottom gap — no lines."""
        y -= 18                          # space ABOVE heading
        c.setFillColorRGB(*text_rgb)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(margin_l, y, label)
        return y - 10                   # tight gap BELOW heading

    # ── Section 1: About ──────────────────────────────────────────────────────
    y = H - banner_h - 22
    y = section_head("About This Document", y)
    y -= 6                              # extra spacing after heading

    y = wrap_text(
        "This document is part of an archived public record library. We are committed "
        "to making all information accessible to everyone, including people with "
        "disabilities. Some archived documents may not fully conform to current Web "
        "Content Accessibility Guidelines (WCAG 2.1 Level AA) or applicable ADA "
        "Title II requirements.",
        margin_l, y, "Helvetica", 10, leading=16,
    )
    y -= 10

    y = wrap_text(
        "We are actively reviewing and remediating our document library. If you "
        "encounter any barriers accessing this content, please contact us using the "
        "information below and we will provide the information in an alternative "
        "accessible format as promptly as possible.",
        margin_l, y, "Helvetica", 10, leading=16,
    )

    # ── Section 2: Contact ────────────────────────────────────────────────────
    y = section_head("Request an Accessible Format", y)

    rows = [
        ("Contact",  cfg["contact_name"]),
        ("Email",    cfg["contact_email"]),
        ("Phone",    cfg["contact_phone"]),
        ("TTY / TDD", "Please use your preferred relay service"),
    ]
    row_h  = 22
    pad    = 14
    box_h  = len(rows) * row_h + pad * 2
    box_y  = y - box_h

    c.setFillColorRGB(*LIGHT_BG)
    c.roundRect(margin_l, box_y, text_w, box_h, radius=6, stroke=0, fill=1)

    # Thin left accent bar
    c.setFillColorRGB(*header_rgb)
    c.rect(margin_l, box_y, 3, box_h, stroke=0, fill=1)

    cy = y - pad - 8
    for label, val in rows:
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(*text_rgb)
        c.drawString(margin_l + 14, cy, f"{label}:")
        c.setFont("Helvetica", 10)
        c.drawString(margin_l + 100, cy, val)
        cy -= row_h

    y = box_y - 10

    # ── Footer note ───────────────────────────────────────────────────────────
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawCentredString(
        W / 2, 36,
        "This accessibility notice was added automatically. "
        "The original archived document begins on the following page.",
    )

    c.save()
    buf.seek(0)
    return PdfReader(buf)


# ═══════════════════════════════════════════════════════════════════════════════
# FILE PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

SKIP_DIRS = {ARCHIVE_DIR, ENCRYPTED_DIR, FAILED_DIR}

def collect_pdfs(root: Path) -> list:
    return sorted(
        p for p in root.rglob("*.pdf")
        if not any(d in p.parts for d in SKIP_DIRS)
        and p.stat().st_size > 0
    )


def _copy_to(pdf_path: Path, root: Path, dest_folder: str) -> Path:
    """Copy a PDF into a mirrored subfolder under root."""
    dest = root / dest_folder / pdf_path.relative_to(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, dest)
    return dest


def process_one(pdf_path: Path, root: Path, cover: PdfReader, dry_run: bool) -> dict:
    r = {"path": pdf_path, "status": "ok", "pages": 0, "msg": ""}
    try:
        reader = PdfReader(str(pdf_path))

        if reader.is_encrypted:
            if not dry_run:
                _copy_to(pdf_path, root, ENCRYPTED_DIR)
            r["status"] = "encrypted"
            r["msg"]    = (f"Encrypted — copied to {ENCRYPTED_DIR}/"
                           if not dry_run else "Encrypted — would copy to encrypted_pdfs/")
            return r

        r["pages"] = len(reader.pages)

        if dry_run:
            r["msg"] = f"Would add cover to {r['pages']}-page document"
            return r

        # Archive original
        _copy_to(pdf_path, root, ARCHIVE_DIR)

        # Write new PDF (cover + original pages)
        writer = PdfWriter()
        writer.add_page(cover.pages[0])
        for page in reader.pages:
            writer.add_page(page)
        with open(pdf_path, "wb") as f:
            writer.write(f)

        r["msg"] = f"Cover added — {r['pages']} original page(s) archived"

    except PdfReadError as e:
        r["status"] = "failed"
        r["msg"]    = f"Unreadable PDF: {e}"
        if not dry_run:
            try:
                _copy_to(pdf_path, root, FAILED_DIR)
                r["msg"] += f" — copied to {FAILED_DIR}/"
            except Exception:
                pass

    except PermissionError as e:
        r["status"] = "failed"
        r["msg"]    = f"Permission denied: {e}"

    except Exception as e:
        r["status"] = "failed"
        r["msg"]    = f"Unexpected error: {e}"
        if not dry_run:
            try:
                _copy_to(pdf_path, root, FAILED_DIR)
                r["msg"] += f" — copied to {FAILED_DIR}/"
            except Exception:
                pass

    return r


def run_batch_processing(pdfs: list, root: Path, cover: PdfReader,
                         dry_run: bool) -> dict:
    totals  = {"ok": 0, "encrypted": 0, "failed": 0,
               "enc_files": [], "fail_files": []}
    batches = [pdfs[i:i + BATCH_SIZE] for i in range(0, len(pdfs), BATCH_SIZE)]
    tag     = "[dim][DRY RUN][/dim] " if dry_run else ""

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold #000000]{task.description}"),
        BarColumn(bar_width=36, style="#000000", complete_style="green"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task(f"{tag}Processing PDFs…", total=len(pdfs))

        for b_num, batch in enumerate(batches, 1):
            b_ok = b_enc = b_fail = 0
            for pdf in batch:
                res = process_one(pdf, root, cover, dry_run)
                s   = res["status"]
                totals[s if s in totals else "failed"] += 1
                if s == "ok":
                    b_ok += 1
                elif s == "encrypted":
                    b_enc += 1
                    totals["enc_files"].append(res)
                else:
                    b_fail += 1
                    totals["fail_files"].append(res)
                progress.advance(task)

            # Per-batch status line
            parts = [f"[dim]Batch {b_num}/{len(batches)}[/dim]",
                     f"[green]✓ {b_ok} processed[/green]"]
            if b_enc:
                parts.append(f"[yellow]🔒 {b_enc} encrypted[/yellow]")
            if b_fail:
                parts.append(f"[red]✗ {b_fail} failed[/red]")
            console.print("  " + "  ".join(parts))

    return totals



def collect_archived_pdfs(root: Path) -> list:
    """
    Return (original, main_path) pairs for every PDF in _old_pdfs/ that still
    has a live counterpart — these are ready for cover replacement.
    """
    archive_root = root / ARCHIVE_DIR
    if not archive_root.exists():
        return []
    results = []
    for orig in sorted(archive_root.rglob("*.pdf")):
        rel       = orig.relative_to(archive_root)
        main_path = root / rel
        if main_path.exists():
            results.append((orig, main_path))
    return results


def replace_one(orig_path: Path, main_path: Path,
                cover: PdfReader, dry_run: bool) -> dict:
    """
    Rebuild main_path from the clean orig_path with the updated cover page.
    Uses the archived original so there is never any risk of double-stacking.
    """
    r = {"orig": orig_path, "path": main_path,
         "status": "ok", "pages": 0, "msg": ""}
    try:
        reader     = PdfReader(str(orig_path))
        r["pages"] = len(reader.pages)

        if dry_run:
            r["msg"] = f"Would replace cover on {r['pages']}-page document"
            return r

        writer = PdfWriter()
        writer.add_page(cover.pages[0])
        for page in reader.pages:
            writer.add_page(page)
        with open(main_path, "wb") as f:
            writer.write(f)
        r["msg"] = f"Cover replaced — {r['pages']} page(s)"

    except PdfReadError as e:
        r["status"] = "failed"; r["msg"] = f"Unreadable original: {e}"
    except PermissionError as e:
        r["status"] = "failed"; r["msg"] = f"Permission denied: {e}"
    except Exception as e:
        r["status"] = "failed"; r["msg"] = f"Unexpected error: {e}"

    return r


def run_replace_processing(pairs: list, cover: PdfReader,
                           dry_run: bool) -> dict:
    """Batch-replace covers using archived originals as the source."""
    totals  = {"ok": 0, "failed": 0, "fail_files": []}
    batches = [pairs[i:i + BATCH_SIZE] for i in range(0, len(pairs), BATCH_SIZE)]
    tag     = "[dim][DRY RUN][/dim] " if dry_run else ""

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold #000000]{task.description}"),
        BarColumn(bar_width=36, style="#000000", complete_style="green"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task(f"{tag}Replacing covers…", total=len(pairs))

        for b_num, batch in enumerate(batches, 1):
            b_ok = b_fail = 0
            for orig, main in batch:
                res = replace_one(orig, main, cover, dry_run)
                if res["status"] == "ok":
                    totals["ok"] += 1; b_ok += 1
                else:
                    totals["failed"] += 1; b_fail += 1
                    totals["fail_files"].append(res)
                progress.advance(task)

            parts = [f"[dim]Batch {b_num}/{len(batches)}[/dim]",
                     f"[green]✓ {b_ok} replaced[/green]"]
            if b_fail:
                parts.append(f"[red]✗ {b_fail} failed[/red]")
            console.print("  " + "  ".join(parts))

    return totals


# ═══════════════════════════════════════════════════════════════════════════════
# MENU SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

def print_banner():
    console.print()
    console.print(Panel(
        Text.assemble(
            ("  Accessibility Cover Page Tool\n\n", "bold white"),
            ("  Follow the steps below to programmatically\n", "bold white"),
            ("  add an accessibility cover page to every\n", "bold white"),
            ("  PDF in a folder tree recursively.\n", "bold white"),
        ),
        style="bold on #000000",
        padding=(1, 4),
        expand=False,
    ))
    console.print()


def print_config_table(cfg: dict):
    t = Table(box=box.ROUNDED, show_header=False,
              border_style="dim #000000", padding=(0, 2), expand=False)
    t.add_column("", style="bold #000000", width=18)
    t.add_column("", style="white")
    t.add_row("Organisation",  cfg["org_name"])
    t.add_row("Contact",       cfg["contact_name"])
    t.add_row("Email",         cfg["contact_email"])
    t.add_row("Phone",         cfg["contact_phone"])
    t.add_row("Logo",          cfg["logo_url"] or "[dim]local logo.svg (if present)[/dim]")
    t.add_row("Header color", f"[{cfg['header_color']}]██[/{cfg['header_color']}] {cfg['header_color']}")
    t.add_row("Text color",   f"[{cfg['text_color']}]██[/{cfg['text_color']}] {cfg['text_color']}")
    console.print(t)


def print_dir_stats(root: Path, pdfs: list):
    console.print(
        f"\n  📁 [bold]Directory:[/bold] [cyan]{root}[/cyan]\n"
        f"  📄 [bold]PDFs found:[/bold] "
        + ("[green]" + str(len(pdfs)) + " file(s)[/green]"
           if pdfs else "[yellow]none found[/yellow]")
    )


def print_summary(totals: dict, root: Path, dry_run: bool, elapsed: float):
    console.print()
    console.print(Rule(
        f"[bold #000000]{'Dry Run' if dry_run else 'Full Run'}"
        f" — Complete[/bold #000000]"
    ))

    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 4))
    t.add_column("", style="bold", min_width=20)
    t.add_column("")
    t.add_row("✓  Processed",   f"[green]{totals['ok']}[/green]")
    t.add_row("🔒  Encrypted",  f"[yellow]{totals['encrypted']}[/yellow]")
    t.add_row("✗  Failed",      f"[red]{totals['failed']}[/red]")
    t.add_row("⏱  Time",        f"[dim]{elapsed:.1f}s[/dim]")
    if not dry_run:
        if totals["ok"]:
            t.add_row("📦  Originals",  f"[dim]{root / ARCHIVE_DIR}[/dim]")
        if totals["encrypted"]:
            t.add_row("🔒  Encrypted",  f"[dim]{root / ENCRYPTED_DIR}[/dim]")
        if totals["failed"]:
            t.add_row("⚠  Failed",     f"[dim]{root / FAILED_DIR}[/dim]")
    console.print(t)

    if totals["enc_files"]:
        console.print("\n[bold yellow]Encrypted files:[/bold yellow]")
        for r in totals["enc_files"]:
            console.print(f"  [yellow]🔒[/yellow] [dim]{r['path']}[/dim]")

    if totals["fail_files"]:
        console.print("\n[bold red]Failed files:[/bold red]")
        for r in totals["fail_files"]:
            console.print(f"  [red]✗[/red] [dim]{r['path']}[/dim]")
            console.print(f"     {r['msg']}")

    # Write log
    log_name = f"accessibility_log{'_dryrun' if dry_run else ''}.txt"
    with open(root / log_name, "w") as lf:
        lf.write(f"Run: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        lf.write(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n")
        lf.write(f"Target: {root}\n")
        lf.write(f"Processed: {totals['ok']}  "
                 f"Encrypted: {totals['encrypted']}  "
                 f"Failed: {totals['failed']}\n\n")
        for key, label in [("enc_files", "ENCRYPTED"), ("fail_files", "FAILED")]:
            if totals[key]:
                lf.write(f"{label}:\n")
                for r in totals[key]:
                    lf.write(f"  {r['path']}\n  → {r['msg']}\n\n")
    console.print(f"\n  [dim]Log → {root / log_name}[/dim]")


def configure_settings(cfg: dict) -> dict:
    console.print(
        "\n[bold #000000]Configure[/bold #000000]  "
        "[dim]Press Enter to keep the current value.[/dim]\n"
    )
    cfg["org_name"]      = Prompt.ask("  Organisation name",   default=cfg["org_name"])
    cfg["contact_name"]  = Prompt.ask("  Contact name / dept", default=cfg["contact_name"])
    cfg["contact_email"] = Prompt.ask("  Contact email",        default=cfg["contact_email"])
    cfg["contact_phone"] = Prompt.ask("  Contact phone",        default=cfg["contact_phone"])
    cfg["logo_url"]      = Prompt.ask(
        "  Logo SVG URL [dim](blank = use logo.svg from script folder)[/dim]",
        default=cfg["logo_url"],
    )
    cfg["header_color"]  = Prompt.ask("  Header color (hex)",   default=cfg["header_color"])
    cfg["text_color"]    = Prompt.ask("  Text color (hex)",     default=cfg["text_color"])
    console.print()
    return cfg


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print_banner()

    raw = Prompt.ask("  [bold]Path to PDF folder[/bold]", default=str(Path.cwd()))
    root = Path(raw.strip()).expanduser().resolve()
    if not root.is_dir():
        console.print(f"\n[red]✗ Not a valid directory:[/red] {root}\n")
        sys.exit(1)

    cfg = {
        "org_name":      "XYZ State Agency",
        "contact_name":  "Accessibility Coordinator",
        "contact_email": "accessibility@state.gov",
        "contact_phone": "(555) 555-5555",
        "logo_url":      "",
        "header_color":  DEFAULT_HEADER_COLOR,
        "text_color":    DEFAULT_TEXT_COLOR,
    }

    console.print()
    logo_bytes = load_logo_bytes(cfg["logo_url"])

    while True:
        pdfs = collect_pdfs(root)
        print_dir_stats(root, pdfs)
        console.print()
        print_config_table(cfg)
        console.print()

        pairs = collect_archived_pdfs(root)   # for replace-cover mode

        console.print("  [bold]What would you like to do?[/bold]\n")
        console.print("  [bold cyan][1][/bold cyan]  Dry Run        "
                      "[dim]— preview which files would be processed (safe)[/dim]")
        console.print("  [bold green][2][/bold green]  Full Run       "
                      "[dim]— add cover pages and archive originals[/dim]")
        console.print("  [bold magenta][4][/bold magenta]  Replace Covers "
                      f"[dim]— update cover on already-processed PDFs "
                      f"({len(pairs)} found)[/dim]")
        console.print("  [bold yellow][3][/bold yellow]  Configure      "
                      "[dim]— update org name, contact details, colors, or logo URL[/dim]")
        console.print("  [bold blue][5][/bold blue]  Change Folder  "
                      "[dim]— switch to a different PDF directory[/dim]")
        console.print("  [bold red][0][/bold red]  Exit\n")

        choice = Prompt.ask("  Choice", choices=["0","1","2","3","4","5"], default="1")
        console.print()

        if choice == "0":
            console.print("[dim]Goodbye.[/dim]\n")
            break

        elif choice == "3":
            old_url = cfg["logo_url"]
            cfg = configure_settings(cfg)
            if cfg["logo_url"] != old_url:
                logo_bytes = load_logo_bytes(cfg["logo_url"])

        elif choice == "5":
            raw = Prompt.ask("  [bold]New path to PDF folder[/bold]",
                             default=str(root))
            new_root = Path(raw.strip()).expanduser().resolve()
            if new_root.is_dir():
                root = new_root
                console.print(f"  [green]✓ Switched to:[/green] [cyan]{root}[/cyan]\n")
            else:
                console.print(f"  [red]✗ Not a valid directory:[/red] {new_root}\n")

        elif choice in ("1", "2"):
            dry_run = (choice == "1")

            if not pdfs:
                console.print(
                    "[yellow]⚠ No processable PDFs found.[/yellow]\n"
                )
                continue

            if not dry_run:
                console.print(
                    f"  ⚠  [bold]This will modify "
                    f"[green]{len(pdfs)}[/green] file(s).[/bold]\n"
                    f"     Originals → [cyan]{root / ARCHIVE_DIR}[/cyan]\n"
                    f"     Encrypted → [cyan]{root / ENCRYPTED_DIR}[/cyan]\n"
                    f"     Failed    → [cyan]{root / FAILED_DIR}[/cyan]\n"
                )
                if not Confirm.ask("  Proceed with Full Run?", default=False):
                    console.print("[dim]  Cancelled — no files were changed.[/dim]\n")
                    continue

            mode = "Dry Run" if dry_run else "Full Run"
            console.print(f"  [bold #000000]Starting {mode}…[/bold #000000]\n")

            with console.status("[dim]Building cover page…[/dim]", spinner="dots"):
                cover = build_cover_page(cfg, logo_bytes)

            start   = time.time()
            totals  = run_batch_processing(pdfs, root, cover, dry_run)
            elapsed = time.time() - start

            print_summary(totals, root, dry_run, elapsed)

            if dry_run and totals["ok"] > 0:
                console.print(
                    "\n  [dim]Satisfied? Choose [bold]2[/bold] "
                    "to apply for real.[/dim]\n"
                )

        elif choice == "4":
            # ── Replace covers on already-processed PDFs ──────────────────
            if not pairs:
                console.print(
                    f"[yellow]⚠ No previously processed PDFs found.\n"
                    f"  Run a Full Run first to archive originals, then "
                    f"use Replace Covers to update them.[/yellow]\n"
                )
                continue

            dry_run = True   # always preview first
            console.print(
                f"  Found [green]{len(pairs)}[/green] previously processed "
                f"PDF(s) in [cyan]{root / ARCHIVE_DIR}[/cyan]\n"
                f"  Originals will be used as the source — "
                f"only the cover page will change.\n"
            )

            with console.status("[dim]Building new cover page…[/dim]", spinner="dots"):
                cover = build_cover_page(cfg, logo_bytes)

            # Dry run preview
            console.print("  [bold #000000]Starting Dry Run…[/bold #000000]\n")
            start  = time.time()
            totals = run_replace_processing(pairs, cover, dry_run=True)
            elapsed = time.time() - start

            console.print()
            console.print(Rule("[bold #000000]Replace Preview — Complete[/bold #000000]"))
            t = Table(box=box.SIMPLE, show_header=False, padding=(0, 4))
            t.add_column("", style="bold", min_width=20)
            t.add_column("")
            t.add_row("✓  Would replace", f"[green]{totals['ok']}[/green]")
            t.add_row("✗  Would fail",    f"[red]{totals['failed']}[/red]")
            t.add_row("⏱  Time",          f"[dim]{elapsed:.1f}s[/dim]")
            console.print(t)
            console.print()

            if totals["ok"] == 0:
                console.print("[yellow]Nothing to replace.[/yellow]\n")
                continue

            if not Confirm.ask(
                f"  Apply new cover to {totals['ok']} PDF(s)?", default=False
            ):
                console.print("[dim]  Cancelled — no files were changed.[/dim]\n")
                continue

            # Live run
            console.print("\n  [bold #000000]Replacing covers…[/bold #000000]\n")
            start   = time.time()
            totals  = run_replace_processing(pairs, cover, dry_run=False)
            elapsed = time.time() - start

            console.print()
            console.print(Rule("[bold #000000]Replace Covers — Complete[/bold #000000]"))
            t = Table(box=box.SIMPLE, show_header=False, padding=(0, 4))
            t.add_column("", style="bold", min_width=20)
            t.add_column("")
            t.add_row("✓  Replaced",  f"[green]{totals['ok']}[/green]")
            t.add_row("✗  Failed",    f"[red]{totals['failed']}[/red]")
            t.add_row("⏱  Time",      f"[dim]{elapsed:.1f}s[/dim]")
            console.print(t)

            if totals["fail_files"]:
                console.print("\n[bold red]Failed:[/bold red]")
                for r in totals["fail_files"]:
                    console.print(f"  [red]✗[/red] [dim]{r['path']}[/dim]  — {r['msg']}")

            # Log
            log_path = root / "replace_covers_log.txt"
            with open(log_path, "w") as lf:
                lf.write(f"Replace run: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                lf.write(f"Target: {root}\n")
                lf.write(f"Replaced: {totals['ok']}  Failed: {totals['failed']}\n\n")
                if totals["fail_files"]:
                    lf.write("FAILED:\n")
                    for r in totals["fail_files"]:
                        lf.write(f"  {r['path']}\n  → {r['msg']}\n\n")
            console.print(f"\n  [dim]Log → {log_path}[/dim]\n")


if __name__ == "__main__":
    main()
