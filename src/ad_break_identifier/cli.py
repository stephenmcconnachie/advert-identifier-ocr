#!/usr/bin/env python3
"""Central CLI entry points for ad break identifier tools.

This module provides entry point functions for all CLI tools,
allowing them to be called via `advert-identifier-*` commands after installation.
"""

import sys
import os
from pathlib import Path


def _get_bin_dir():
    """Find the bin directory containing scripts.
    
    Works both in development (repo) and installed (site-packages) modes.
    """
    # In development mode, cli.py is in src/ad_break_identifier/
    # bin/ is at repo root (3 levels up from cli.py)
    dev_bin = Path(__file__).parent.parent.parent / "bin"
    if dev_bin.exists():
        return dev_bin
    
    # Fallback: check if running from repo root
    repo_root = Path.cwd()
    if (repo_root / "bin").exists() and (repo_root / "src").exists():
        return repo_root / "bin"
    
    # Check environment variable
    if "AD_BREAK_IDENTIFIER_ROOT" in os.environ:
        return Path(os.environ["AD_BREAK_IDENTIFIER_ROOT"]) / "bin"
    
    raise RuntimeError(
        "Could not find bin/ directory. "
        "Please run from the repository root or set AD_BREAK_IDENTIFIER_ROOT environment variable."
    )


def identifier_main():
    """Entry point for advert-identifier command."""
    # Add src to path for imports
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from ad_break_identifier.main import main
    main()


def pipeline_main():
    """Entry point for advert-identifier-pipeline command."""
    import subprocess
    
    bin_dir = _get_bin_dir()
    script = bin_dir / "advert-identifier-pipeline"
    result = subprocess.run([sys.executable, str(script)] + sys.argv[1:])
    sys.exit(result.returncode)


def extractor_main():
    """Entry point for advert-identifier-metadata-extract command."""
    import subprocess
    
    bin_dir = _get_bin_dir()
    script = bin_dir / "advert-identifier-metadata-extract"
    result = subprocess.run([sys.executable, str(script)] + sys.argv[1:])
    sys.exit(result.returncode)


def clipper_main():
    """Entry point for advert-identifier-clip command."""
    import subprocess
    
    bin_dir = _get_bin_dir()
    script = bin_dir / "advert-identifier-clip"
    result = subprocess.run([sys.executable, str(script)] + sys.argv[1:])
    sys.exit(result.returncode)


def benchmark_main():
    """Entry point for advert-identifier-benchmark command."""
    import subprocess
    
    bin_dir = _get_bin_dir()
    script = bin_dir / "advert-identifier-benchmark"
    result = subprocess.run([sys.executable, str(script)] + sys.argv[1:])
    sys.exit(result.returncode)


def describer_main():
    """Entry point for advert-identifier-describe command."""
    import subprocess

    bin_dir = _get_bin_dir()
    script = bin_dir / "advert-identifier-describe"
    result = subprocess.run([sys.executable, str(script)] + sys.argv[1:])
    sys.exit(result.returncode)


def single_advert_clip_main():
    """Entry point for advert-identifier-single-advert-clip command."""
    import subprocess

    bin_dir = _get_bin_dir()
    script = bin_dir / "advert-identifier-single-advert-clip"
    result = subprocess.run([sys.executable, str(script)] + sys.argv[1:])
    sys.exit(result.returncode)


if __name__ == "__main__":
    # Default to identifier if called directly
    identifier_main()
