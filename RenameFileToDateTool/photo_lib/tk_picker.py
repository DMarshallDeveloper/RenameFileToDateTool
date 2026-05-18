"""Wrapper around tkinter's folder picker so every script doesn't reinvent the same
4 lines of ``Tk(); withdraw(); askdirectory(); destroy()`` boilerplate.
"""

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
