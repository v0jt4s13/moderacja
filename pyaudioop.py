"""
Compatibility shim for libraries that do `import pyaudioop as audioop`.

On Python 3.13+, the stdlib `audioop` module was removed (PEP 594). If you
need it, install the backport `audioop-lts` and this shim will re-export its
symbols under the `pyaudioop` name expected by some packages.

Usage:
- Python 3.12 or lower: stdlib `audioop` is available; this shim re-exports it.
- Python 3.13+: `pip install audioop-lts` and keep this file in project root.
"""

try:
    # Prefer stdlib (Python <= 3.12)
    from audioop import *  # type: ignore
except Exception as e:  # pragma: no cover
    # Fallback expects the `audioop-lts` backport to provide `audioop`
    try:
        from audioop import *  # type: ignore
    except Exception as inner:
        raise ImportError(
            "audioop (or backport audioop-lts) is required. On Python 3.13,\n"
            "install it via `pip install audioop-lts`, or use Python 3.12.x."
        ) from inner

