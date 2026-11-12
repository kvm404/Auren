"""The macOS clipboard (NSPasteboard), with the interface of hands/clipboard.py.

Kinds are given as MIME types, as on Linux: text ("text/plain;charset=utf-8") and "image/png" map to
the pasteboard's own types; others are passed through as pasteboard type names. `sensitive` adds the
org.nspasteboard.ConcealedType marker, which clipboard managers (Maccy, Raycast, Alfred...) skip.
"""

from __future__ import annotations

from ..hands.clipboard import _TEXT, TEXT, ClipboardError

__all__ = ["types", "save", "copy", "text", "restore"]

CONCEALED = "org.nspasteboard.ConcealedType"


def _board():
    from AppKit import NSPasteboard
    return NSPasteboard.generalPasteboard()


def _ns_type(kind: str | None) -> str:
    from AppKit import NSPasteboardTypePNG, NSPasteboardTypeString
    if kind is None or kind in _TEXT or kind.startswith("text/plain"):
        return str(NSPasteboardTypeString)
    if kind == "image/png":
        return str(NSPasteboardTypePNG)
    return kind


def _mime(ns: str) -> str:
    from AppKit import NSPasteboardTypePNG, NSPasteboardTypeString
    return {str(NSPasteboardTypeString): TEXT, str(NSPasteboardTypePNG): "image/png"}.get(ns, ns)


def types() -> list[str]:
    try:
        return [_mime(str(t)) for t in (_board().types() or [])]
    except Exception:  # noqa: BLE001
        return []


def save() -> tuple[str, bytes] | None:
    try:
        offered = types()
        kind = next((t for t in (TEXT, "image/png") if t in offered), next((t for t in offered if t != CONCEALED), None))
        if kind is None:
            return None
        data = _board().dataForType_(_ns_type(kind))
    except Exception:  # noqa: BLE001
        return None
    return (kind, bytes(data)) if data is not None else None


def copy(data: bytes, kind: str | None = None, sensitive: bool = True) -> None:
    from Foundation import NSData
    try:
        board = _board()
        board.clearContents()
        ns = _ns_type(kind)
        ok = board.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), ns)
        if sensitive:
            board.setData_forType_(NSData.data(), CONCEALED)
    except Exception as e:  # noqa: BLE001
        raise ClipboardError(f"the clipboard can't be written: {e}") from None
    if not ok:
        raise ClipboardError("the clipboard can't be written")


def text(kind: str | None = None) -> str:
    try:
        data = _board().dataForType_(_ns_type(kind))
    except Exception:  # noqa: BLE001
        return ""
    return bytes(data).decode("utf-8", "replace") if data is not None else ""


def restore(saved: tuple[str, bytes] | None, ours: bytes | None = None) -> None:
    try:
        if ours is not None and text(TEXT).encode() != ours:
            return
        if saved is None:
            _board().clearContents()
        else:
            kind, data = saved
            copy(data, kind, sensitive=False)
    except (ClipboardError, Exception):  # noqa: BLE001
        pass
