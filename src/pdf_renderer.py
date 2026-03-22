"""
pdf_renderer.py -- Convert .docx files to PDF using docx2pdf or LibreOffice fallback.
"""

import os
import subprocess
import sys
from pathlib import Path


def render_pdf(docx_path: str) -> str:
    """
    Convert a .docx file to PDF. Returns the path to the generated PDF.

    Tries in order:
    1. docx2pdf (requires Microsoft Word on Windows/macOS)
    2. LibreOffice headless (cross-platform fallback)

    Raises RuntimeError if neither method succeeds.
    """
    docx_path = os.path.abspath(docx_path)
    if not os.path.exists(docx_path):
        raise FileNotFoundError(f"File not found: {docx_path}")

    pdf_path = str(Path(docx_path).with_suffix(".pdf"))

    # --- Method 1: docx2pdf ---
    try:
        _render_via_docx2pdf(docx_path, pdf_path)
        return pdf_path
    except Exception:
        pass

    # --- Method 2: LibreOffice headless ---
    try:
        _render_via_libreoffice(docx_path, pdf_path)
        return pdf_path
    except Exception as e2:
        raise RuntimeError(
            f"PDF conversion failed.\n"
            f"  docx2pdf error: {e1}\n"
            f"  LibreOffice error: {e2}\n\n"
            "Install one of:\n"
            "  pip install docx2pdf  (requires Microsoft Word)\n"
            "  https://www.libreoffice.org/download/  (free, no Word needed)"
        )


def _render_via_docx2pdf(docx_path: str, pdf_path: str) -> None:
    """Convert using docx2pdf (requires MS Word on Windows/macOS)."""
    try:
        from docx2pdf import convert
    except ImportError:
        raise ImportError("docx2pdf not installed")

    # Re-save via python-docx first to avoid Word protected-mode issues on Windows
    _resave_docx(docx_path)
    convert(docx_path, pdf_path)

    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
        raise RuntimeError("docx2pdf produced no output")


def _render_via_libreoffice(docx_path: str, pdf_path: str) -> None:
    """Convert using LibreOffice headless (soffice)."""
    output_dir = os.path.dirname(pdf_path)

    # Common soffice locations
    candidates = ["soffice", "libreoffice"]
    if sys.platform == "win32":
        candidates += [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]

    cmd = None
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True, timeout=10
            )
            if result.returncode == 0:
                cmd = candidate
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if not cmd:
        raise FileNotFoundError("LibreOffice (soffice) not found on PATH")

    result = subprocess.run(
        [
            cmd,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", output_dir,
            docx_path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice exited {result.returncode}: {result.stderr}")

    # LibreOffice names the output after the input file
    expected = os.path.join(output_dir, Path(docx_path).stem + ".pdf")
    if not os.path.exists(expected) or os.path.getsize(expected) == 0:
        raise RuntimeError("LibreOffice produced no output")

    if expected != pdf_path:
        os.replace(expected, pdf_path)


def _resave_docx(docx_path: str) -> None:
    """Re-save a .docx via python-docx to clear any protected-mode flags."""
    try:
        from docx import Document
        doc = Document(docx_path)
        doc.save(docx_path)
    except Exception:
        pass  # If it fails, proceed anyway
