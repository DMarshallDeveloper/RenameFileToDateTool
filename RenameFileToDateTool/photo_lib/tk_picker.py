"""Wrapper around tkinter's folder picker so every script doesn't reinvent the same
4 lines of ``Tk(); withdraw(); askdirectory(); destroy()`` boilerplate.

``resolve_directory`` is the entry point for scripts that take an optional
``--path`` CLI flag: use the flag if given, fall back to the Tk picker otherwise.
This is what makes the Takeout → ingest pipeline chainable from the command line.
"""

import os
from tkinter import Tk, filedialog


def choose_directory(title: str = "Select a folder", initial_dir: str | None = None) -> str | None:
    """Show a folder picker and return the selected path, or None if cancelled.

    Destroys the hidden Tk root after the dialog closes so we don't leak it.
    """
    root = Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(title=title, initialdir=initial_dir or "")
    finally:
        root.destroy()
    return selected or None


def resolve_directory(cli_path: str | None, title: str,
                      initial_dir: str | None = None,
                      must_exist: bool = True) -> str | None:
    """Return ``cli_path`` if it points to a real directory; otherwise open the picker.

    Scripts pass their ``args.path`` here. If the user provided ``--path`` on the
    command line, the picker is skipped entirely (so the script can run unattended).
    A bad ``--path`` aborts loudly rather than silently falling back to the picker.

    Set ``must_exist=False`` for output-folder args where the script will create
    the directory if absent (e.g. convert_unwanted_formats's --output).
    """
    if cli_path:
        # Resolve to absolute up front so downstream callers (and any sidecar
        # databases they keep) get a stable, separator-normalised key. This
        # closes a class of bug where a shell silently mangled the input —
        # e.g. bash-on-Windows dropping ``\P`` in ``F:\PhotosCombined`` so
        # Python received ``F:PhotosCombined`` (drive-relative). Without
        # abspath here, every cache lookup against that root missed.
        cli_path = os.path.abspath(cli_path)
        if must_exist and not os.path.isdir(cli_path):
            raise SystemExit(f"Not a directory: {cli_path}")
        return cli_path
    return choose_directory(title, initial_dir=initial_dir)
