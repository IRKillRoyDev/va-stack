"""Shared test setup: Qt windows run headless (offscreen) unless told otherwise.

Set QT_QPA_PLATFORM yourself (for example to "xcb") to watch the windows.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
