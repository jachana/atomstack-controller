"""Accept files dropped onto the window.

Tk has no drag and drop of its own, and the usual answer is another dependency
carrying its own compiled Tcl package. This app already ships only on Windows,
where the shell will send a window WM_DROPFILES for nothing, so it asks for that
directly instead of growing the bundle.

Everything here degrades: if the window handle cannot be found or the shell
refuses, ``enable`` returns False and the app works as it did before, with the
file dialogs.
"""
import ctypes
import os
from ctypes import wintypes

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

IMAGES = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")
VECTORS = {".svg": "svg", ".dxf": "dxf"}
DESIGNS = (".atomdesign", ".json")


def classify(paths):
    """What a dropped file is for, as (kind, path) pairs in the order given.

    Kinds are "image", "svg", "dxf", "design" and "unknown". Deciding this
    separately from the shell plumbing is what makes it testable.
    """
    sorted_out = []
    for path in paths:
        suffix = os.path.splitext(str(path))[1].lower()
        if suffix in IMAGES:
            sorted_out.append(("image", path))
        elif suffix in VECTORS:
            sorted_out.append((VECTORS[suffix], path))
        elif suffix in DESIGNS:
            sorted_out.append(("design", path))
        else:
            sorted_out.append(("unknown", path))
    return sorted_out


def dropped_paths(drop_handle):
    """Read the file names out of a WM_DROPFILES message."""
    shell = ctypes.windll.shell32
    count = shell.DragQueryFileW(drop_handle, 0xFFFFFFFF, None, 0)
    paths = []
    for index in range(count):
        length = shell.DragQueryFileW(drop_handle, index, None, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell.DragQueryFileW(drop_handle, index, buffer, length + 1)
        paths.append(buffer.value)
    shell.DragFinish(drop_handle)
    return paths


def window_handle(widget):
    """The top-level window Windows knows about, not Tk's inner frame."""
    top = widget.winfo_toplevel()
    top.update_idletasks()          # The handle does not exist until it is mapped.
    handle = top.winfo_id()
    parent = ctypes.windll.user32.GetParent(handle)
    return parent or handle


def enable(widget, on_drop):
    """Route files dropped on ``widget``'s window to ``on_drop``.

    Returns False when the platform will not do it, so callers can carry on
    without drag and drop rather than failing to start.
    """
    if os.name != "nt":
        return False
    try:
        handle = window_handle(widget)
        prototype = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                                       ctypes.c_size_t, ctypes.c_ssize_t)
        user32 = ctypes.windll.user32
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        # Without these, ctypes assumes these functions return a C int, and the
        # window procedure's address comes back truncated to 32 bits. Calling
        # through the result then crashes the process on the first message.
        set_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        call_previous = user32.CallWindowProcW
        call_previous.restype = ctypes.c_ssize_t
        call_previous.argtypes = [ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                                  ctypes.c_size_t, ctypes.c_ssize_t]
        previous = None

        def handler(hwnd, message, wparam, lparam):
            if message == WM_DROPFILES:
                try:
                    on_drop(dropped_paths(wparam))
                except Exception:
                    pass  # A bad drop must not take the window's message loop with it.
                return 0
            return call_previous(previous, hwnd, message, wparam, lparam)

        callback = prototype(handler)
        # The call wants the procedure's address, not the ctypes object.
        address = ctypes.cast(callback, ctypes.c_void_p).value
        previous = set_long(handle, GWLP_WNDPROC, address)
        if not previous:
            return False
        ctypes.windll.shell32.DragAcceptFiles(handle, True)
        # The callback and the old procedure have to outlive this function, or
        # Windows calls into freed memory the first time something is dropped.
        widget._drop_callback = callback
        widget._drop_previous = previous

        def restore(_event=None):
            # Put the original procedure back before the window goes away, or
            # Windows keeps calling a callback whose Python object is gone.
            try:
                ctypes.windll.shell32.DragAcceptFiles(handle, False)
                set_long(handle, GWLP_WNDPROC, previous)
            except Exception:
                pass

        widget.bind("<Destroy>", restore, add="+")
        return True
    except Exception:
        return False
