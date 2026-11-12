"""Excalidraw: draw a whole picture or diagram at once, and read what's on the canvas.

Drawing shape by shape with mouse drags is slow and lands only roughly where it was aimed, so Auren hands
Excalidraw the finished drawing through the clipboard instead, the way Excalidraw's own API does:

- build(): the brain's elements become Excalidraw element "skeletons" (the format of its
  convertToExcalidrawElements), with a few conveniences: colour names, absolute points, labels as plain
  strings, arrows that only name the shapes they join. Arrows are aimed here, edge to edge: Excalidraw
  binds an arrow to its shapes but keeps the coordinates it is given.
- draw(): put {"type": "excalidraw-api/clipboard", "elements": [...]} (or a Mermaid definition) on the
  clipboard and press Ctrl+V over the canvas. Excalidraw converts it (labels measured, wrapped and bound;
  Mermaid laid out by mermaid-to-excalidraw) and drops it at the pointer, selected. Ctrl+C then copies the
  selection back, which proves it landed and says what it became; Shift+2 scrolls it into view, zooming
  out only if it's bigger than the window.
- read(): Ctrl+A, Ctrl+C: the canvas as Excalidraw's own clipboard JSON, summed up for the brain.

Excalidraw only takes a paste while the pointer is over the canvas and no text box has focus, so the pointer
goes to an empty part of the window and the selection tool is picked (Escape, V) and clicked first. Letters
go through wtype (by keysym, so any layout works); Shift+2 goes through the virtual keyboard, because
Excalidraw reads the physical key for it. The user's clipboard is put back afterwards; what Auren puts on it
is marked sensitive, so clipboard history skips it.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import sys
import time

from . import clipboard, desktop, screen

log = logging.getLogger("auren.excalidraw")

URL = "https://excalidraw.com"          # opened only when no Excalidraw window or web app is there
MAX_ELEMENTS = 500
GAP = 6                                 # between an arrow's tip and the outline it points at

# Excalidraw's own palette (open-color shades 2, 6 and 8): light-<hue> for fills, <hue> for bright parts of
# pictures, dark-<hue> for strokes and text (Excalidraw's default stroke colours).
_HUES = {
    "red": ("#ffc9c9", "#fa5252", "#e03131"),
    "pink": ("#fcc2d7", "#e64980", "#c2255c"),
    "purple": ("#eebefa", "#be4bdb", "#9c36b5"),
    "violet": ("#d0bfff", "#7950f2", "#6741d9"),
    "blue": ("#a5d8ff", "#228be6", "#1971c2"),
    "cyan": ("#99e9f2", "#15aabf", "#0c8599"),
    "teal": ("#96f2d7", "#12b886", "#099268"),
    "green": ("#b2f2bb", "#40c057", "#2f9e44"),
    "yellow": ("#ffec99", "#fab005", "#f08c00"),
    "orange": ("#ffd8a8", "#fd7e14", "#e8590c"),
    "gray": ("#e9ecef", "#868e96", "#343a40"),
    "brown": ("#eaddd7", "#a18072", "#846358"),
}
COLORS = {"black": "#1e1e1e", "white": "#ffffff", "transparent": "transparent"}
for _hue, (_light, _plain, _dark) in _HUES.items():
    COLORS.update({f"light-{_hue}": _light, _hue: _plain, f"dark-{_hue}": _dark})
FONTS = {"hand": 5, "normal": 6, "code": 8}     # Excalifont, Nunito, Comic Shanns

_SHAPES = {"rectangle", "ellipse", "diamond"}
_TYPES = (*sorted(_SHAPES), "text", "arrow", "line", "freedraw", "frame")
_ALIASES = {"color": "strokeColor", "stroke": "strokeColor", "fill": "backgroundColor", "background": "backgroundColor"}

# Mermaid diagrams Excalidraw turns into its own shapes; other Mermaid types arrive as one picture.
MERMAID_SHAPES = ("flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram", "erDiagram")
MERMAID_TYPES = (*MERMAID_SHAPES, "journey", "gantt", "pie", "quadrantChart", "requirementDiagram", "gitGraph",
                 "C4Context", "mindmap", "timeline", "zenuml", "sankey", "xychart", "block")

_drawn: dict[str, int] = {}             # the elements Auren pasted (as Excalidraw named them): id -> version


class ExcalidrawError(RuntimeError):
    pass


# --- building the drawing ------------------------------------------------------------------------------

def color(value, where: str) -> str:
    v = str(value).strip().lower().replace("grey", "gray").replace(" ", "-").replace("_", "-")
    if v in COLORS:
        return COLORS[v]
    if v.startswith("#") and len(v) in (4, 7) and all(c in "0123456789abcdef" for c in v[1:]):
        return v
    raise ExcalidrawError(f"{where}: unknown colour {value!r}: use #rrggbb or one of {', '.join(COLORS)}")


def _font(value, where: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if str(value).lower() in FONTS:
        return FONTS[str(value).lower()]
    raise ExcalidrawError(f"{where}: font must be one of {', '.join(FONTS)}")


def _number(e: dict, key: str, where: str) -> float:
    v = e.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise ExcalidrawError(f"{where}: needs a number for {key!r}")
    return v


def _points(value, where: str, least: int = 2) -> list[tuple[float, float]]:
    try:
        pts = [(float(p[0]), float(p[1])) for p in value]
    except (TypeError, ValueError, IndexError, KeyError):
        raise ExcalidrawError(f"{where}: points must be [[x, y], ...] in canvas coordinates") from None
    if len(pts) < least or not all(math.isfinite(c) for p in pts for c in p):
        raise ExcalidrawError(f"{where}: needs at least {least} points, [[x, y], ...]")
    return pts


def text_size(text: str, size: float, family: int = 5) -> tuple[float, float]:
    """About how much room Excalidraw gives text (it measures it exactly; this errs a little large)."""
    lines = text.split("\n")
    return max(len(line) for line in lines) * size * (0.62 if family == 8 else 0.58), len(lines) * size * 1.25


def _ref(value, where: str, end: str) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("id")
    if not isinstance(value, str) or not value:
        raise ExcalidrawError(f"{where}: {end} must be the id of a shape")
    return value


def build(elements: list, canvas: dict | None = None) -> tuple[list[dict], tuple[float, float, float, float]]:
    """The brain's elements as Excalidraw skeletons, and the drawing's bounds (x1, y1, x2, y2). canvas: the
    shapes already on the canvas (id -> box), which arrows may also start or end at."""
    canvas = canvas or {}
    if not isinstance(elements, list) or not elements:
        raise ExcalidrawError("elements must be a non-empty list")
    if len(elements) > MAX_ELEMENTS:
        raise ExcalidrawError(f"at most {MAX_ELEMENTS} elements in one drawing")
    out: list[dict] = []
    ids: set[str] = set()
    boxes: dict[str, tuple[str, float, float, float, float]] = {}      # id -> (type, x, y, width, height)
    links: list[tuple[dict, str, list | None, str | None, str | None, bool]] = []
    frames: list[tuple[dict, str]] = []
    for i, raw in enumerate(elements):
        if not isinstance(raw, dict):
            raise ExcalidrawError(f"element {i + 1} must be an object")
        e = dict(raw)
        kind = e.get("type")
        where = f"element {i + 1}" + (f" ({e['id']})" if e.get("id") else "")
        if kind not in _TYPES:
            raise ExcalidrawError(f"{where}: type must be one of {', '.join(_TYPES)}")
        e["id"] = str(e.get("id") or f"auren-{i + 1}")
        if e["id"] in ids:
            raise ExcalidrawError(f"{where}: the id {e['id']!r} is used twice")
        ids.add(e["id"])
        for alias, key in _ALIASES.items():
            if alias in e:
                e.setdefault(key, e[alias])
                del e[alias]
        _style(e, where)
        if kind in _SHAPES:
            boxes[e["id"]] = _shape(e, where)
        elif kind == "text":
            if not isinstance(e.get("text"), str) or not e["text"]:
                raise ExcalidrawError(f"{where}: text needs its text")
            x, y = _number(e, "x", where), _number(e, "y", where)
            e.setdefault("fontSize", 20)
            boxes[e["id"]] = ("text", x, y, *text_size(e["text"], e["fontSize"], e.get("fontFamily", 5)))
        elif kind in ("arrow", "line"):
            links.append(_link(e, where))
        elif kind == "freedraw":
            pts = _points(e.pop("points", None), where)
            _place(e, pts)
            e.update(simulatePressure=True, pressures=[])
        elif kind == "frame":
            if not isinstance(e.get("children"), list) or not e["children"]:
                raise ExcalidrawError(f"{where}: a frame needs children, the ids of the elements inside it")
            frames.append((e, where))
        out.append(e)
    for e, where, points, start, end, elbow in links:
        for ref, name in ((start, "start"), (end, "end")):
            if ref is not None and ref not in boxes and ref not in canvas:
                raise ExcalidrawError(f"{where}: {name} {ref!r} isn't the id of a shape or text in this drawing "
                                      "or on the canvas (excalidraw_read lists those)")
        if start is not None and start == end:
            raise ExcalidrawError(f"{where}: start and end are the same shape")
        others = [b for k, b in {**canvas, **boxes}.items() if k not in (start, end)]
        _place(e, route(boxes.get(start) or canvas.get(start), boxes.get(end) or canvas.get(end), points, elbow, others))
        # bound only to shapes in the same paste: Excalidraw can't attach a pasted arrow to one already there
        if start in boxes:
            e["start"] = {"id": start}
        if end in boxes:
            e["end"] = {"id": end}
    for e, where in frames:
        missing = [str(c) for c in e["children"] if c not in ids]
        if missing:
            raise ExcalidrawError(f"{where}: no elements with the ids {', '.join(missing)}")
    return out, _bounds(out, boxes)


def outside(elements: list) -> set[str]:
    """The ids arrows start or end at that aren't elements of this drawing: shapes already on the canvas."""
    if not isinstance(elements, list):
        return set()
    own = {str(e.get("id")) for e in elements if isinstance(e, dict) and e.get("id")}
    refs = set()
    for e in elements:
        if isinstance(e, dict) and e.get("type") == "arrow":
            for end in ("start", "end"):
                ref = e.get(end)
                ref = ref.get("id") if isinstance(ref, dict) else ref
                if isinstance(ref, str) and ref and ref not in own:
                    refs.add(ref)
    return refs


def boxes_of(elements: list[dict]) -> dict[str, tuple[str, float, float, float, float]]:
    """Excalidraw elements as boxes arrows can be aimed at (anything not an ellipse or diamond is a rectangle)."""
    return {x["id"]: (x["type"] if x.get("type") in ("ellipse", "diamond") else "rectangle", x.get("x", 0),
                      x.get("y", 0), x.get("width", 0) or 1, x.get("height", 0) or 1)
            for x in elements if x.get("id") and x.get("type") not in ("arrow", "line", "freedraw")
            and not x.get("containerId")}


def _style(e: dict, where: str) -> None:
    for key in ("strokeColor", "backgroundColor"):
        if key in e:
            e[key] = color(e[key], where)
    if e.get("backgroundColor", "transparent") != "transparent":
        e.setdefault("fillStyle", "solid")
    if "font" in e:
        e["fontFamily"] = _font(e.pop("font"), where)
    if "rotate" in e:
        e["angle"] = math.radians(float(e.pop("rotate")))
    group = e.pop("group", None)
    if group:
        e["groupIds"] = [str(g) for g in group] if isinstance(group, list) else [str(group)]


def _label(e: dict, where: str) -> dict | None:
    label = e.get("label")
    if label is None or label == "":
        e.pop("label", None)
        return None
    if isinstance(label, str):
        label = {"text": label}
    if not isinstance(label, dict) or not isinstance(label.get("text"), str):
        raise ExcalidrawError(f"{where}: label must be text, or {{\"text\": ...}}")
    label = dict(label)
    for key in ("fontSize", "fontFamily"):          # on the element, they mean its label
        if key in e:
            label.setdefault(key, e.pop(key))
    if "font" in label:
        label["fontFamily"] = _font(label.pop("font"), where)
    if "strokeColor" in label:
        label["strokeColor"] = color(label["strokeColor"], where)
    e["label"] = label
    return label


def _shape(e: dict, where: str) -> tuple[str, float, float, float, float]:
    x, y = _number(e, "x", where), _number(e, "y", where)
    label = _label(e, where)
    if label and ("width" not in e or "height" not in e):
        tw, th = text_size(label["text"], label.get("fontSize", 20), label.get("fontFamily", 5))
        grow = {"rectangle": (1, 1), "ellipse": (1.45, 1.5), "diamond": (1.8, 1.9)}[e["type"]]
        e.setdefault("width", math.ceil(max(140, (tw + 40) * grow[0]) / 10) * 10)
        e.setdefault("height", math.ceil(max(70, (th + 30) * grow[1]) / 10) * 10)
    e.setdefault("width", 100)
    e.setdefault("height", 100)
    w, h = _number(e, "width", where), _number(e, "height", where)
    if w <= 0 or h <= 0:
        raise ExcalidrawError(f"{where}: width and height must be more than 0")
    if e.pop("rounded", label is not None) and e["type"] != "ellipse":     # diagram boxes round, picture parts sharp
        e.setdefault("roundness", {"type": 3})
    return e["type"], x, y, w, h


def _link(e: dict, where: str) -> tuple[dict, str, list | None, str | None, str | None, bool]:
    if e.pop("smooth", False):
        e["roundness"] = {"type": 2}
    elbow = bool(e.pop("elbow", False))
    start, end = _ref(e.pop("start", None), where, "start"), _ref(e.pop("end", None), where, "end")
    if e["type"] == "line" and (start or end):
        raise ExcalidrawError(f"{where}: only arrows join shapes; give a line its points")
    if e["type"] == "arrow":
        _label(e, where)
    raw = e.pop("points", None)
    points = _points(raw, where) if raw is not None else None
    if points is None and not (start and end):
        raise ExcalidrawError(f"{where}: needs points ([[x, y], ...] in canvas coordinates) or start and end ids")
    if e["type"] == "line" and len(points) >= 4 and math.dist(points[0], points[-1]) < 1:
        e.setdefault("polygon", True)                 # closed: it can be filled
    return e, where, points, start, end, elbow


def _centre(box) -> tuple[float, float]:
    _, x, y, w, h = box
    return x + w / 2, y + h / 2


def edge(box, toward: tuple[float, float]) -> tuple[float, float]:
    """Where the line from the box's centre towards a point crosses its outline, GAP beyond it."""
    kind, x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == dy == 0:
        return cx, cy
    a, b = w / 2, h / 2
    if kind == "ellipse":
        t = 1 / math.hypot(dx / a, dy / b)
    elif kind == "diamond":
        t = 1 / (abs(dx) / a + abs(dy) / b)
    else:
        t = min(a / abs(dx) if dx else math.inf, b / abs(dy) if dy else math.inf)
    t += GAP / math.hypot(dx, dy)
    return cx + dx * t, cy + dy * t


def route(start, end, points: list | None, elbow: bool = False, others: list = ()) -> list[tuple[float, float]]:
    """An arrow's points: the given path, or straight (or at right angles, around the other shapes if it can)
    from one shape to the other, its ends moved onto the outlines of the shapes it joins."""
    if points:
        pts = list(points)
    elif elbow:
        return _elbow(start, end, others)
    else:
        pts = [_centre(start), _centre(end)]
    first, last = pts[1], pts[-2]
    if start:
        pts[0] = edge(start, first)
    if end:
        pts[-1] = edge(end, last)
    if math.dist(pts[0], pts[-1]) < 1 and len(pts) == 2:
        raise ExcalidrawError("an arrow's shapes overlap: move them apart")
    return pts


def _rim(box, x: float, d: int) -> float:
    """Where the line through x crosses the box's outline, on its bottom (d = 1) or top (d = -1), GAP beyond."""
    kind, bx, by, w, h = box
    u = min(1.0, abs(x - (bx + w / 2)) / (w / 2))
    reach = {"ellipse": math.sqrt(1 - u * u), "diamond": 1 - u}.get(kind, 1.0)
    return by + h / 2 + d * (h / 2 * reach + GAP)


def _swap(box):
    kind, x, y, w, h = box
    return kind, y, x, h, w


def _elbow(start, end, others: list = ()) -> list[tuple[float, float]]:
    """Right angles from one shape to the other: of the four ways (out of a side or the top or bottom, with one
    bend or two), the one that crosses the fewest other shapes, the most natural first when they tie."""
    (sx, sy), (ex, ey) = _centre(start), _centre(end)
    down = [_elbow_down(start, end), _l_down(start, end)]
    turned = [_turned(_elbow_down, start, end), _turned(_l_down, start, end)]
    ways = [w for w in (down + turned if abs(ey - sy) > abs(ex - sx) else turned + down) if w]
    return min(ways, key=lambda w: _crossings(w, others))       # the first of the fewest


def _turned(way, start, end):
    """A route worked out sideways: the same, with x and y swapped there and back."""
    pts = way(_swap(start), _swap(end))
    return [(y, x) for x, y in pts] if pts else None


def _l_down(start, end) -> list[tuple[float, float]] | None:
    """Out of the top or bottom, one bend, into the side of the other (None if they aren't placed for it)."""
    (sx, sy), (ex, ey) = _centre(start), _centre(end)
    d, h = (1 if ey > sy else -1), (1 if ex > sx else -1)
    y1, x2 = _rim(start, sx, d), _rim(_swap(end), ey, -h)
    if (ey - y1) * d <= 0 or (x2 - sx) * h <= 0:
        return None
    return [(sx, y1), (sx, ey), (x2, ey)]


def _crossings(pts: list[tuple[float, float]], others: list, margin: float = 8) -> int:
    """How many of the other shapes a right-angled path runs through."""
    hit = 0
    for _, x, y, w, h in others:
        for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False):
            if max(x1, x2) > x - margin and min(x1, x2) < x + w + margin and \
                    max(y1, y2) > y - margin and min(y1, y2) < y + h + margin:
                hit += 1
                break
    return hit


def _elbow_down(start, end) -> list[tuple[float, float]]:
    (sx, sy), (ex, ey) = _centre(start), _centre(end)
    d = 1 if ey > sy else -1
    lo, hi = max(start[1], end[1]), min(start[1] + start[3], end[1] + end[3])
    if hi - lo >= 20:                                 # they overlap sideways: one straight line
        x = sx if lo <= sx <= hi else ex if lo <= ex <= hi else (lo + hi) / 2
        return [(x, _rim(start, x, d)), (x, _rim(end, x, -d))]
    y1, y2 = _rim(start, sx, d), _rim(end, ex, -d)
    return [(sx, y1), (sx, (y1 + y2) / 2), (ex, (y1 + y2) / 2), (ex, y2)]


def _place(e: dict, pts: list[tuple[float, float]]) -> None:
    """Excalidraw keeps a linear element's points relative to its x, y (the first point)."""
    x0, y0 = pts[0]
    rel = [[round(px - x0, 2), round(py - y0, 2)] for px, py in pts]
    xs, ys = [p[0] for p in rel], [p[1] for p in rel]
    e.update(x=round(x0, 2), y=round(y0, 2), points=rel, width=max(xs) - min(xs), height=max(ys) - min(ys))


def _bounds(out: list[dict], boxes: dict) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for _, x, y, w, h in boxes.values():
        xs += [x, x + w]
        ys += [y, y + h]
    for e in out:
        for px, py in e.get("points") or []:
            xs.append(e["x"] + px)
            ys.append(e["y"] + py)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


def check_mermaid(text: str) -> str:
    """The definition, if Excalidraw will recognise it as Mermaid (else it would paste it as plain text)."""
    lines = [ln.strip() for ln in text.strip().splitlines()]
    first = next((ln for ln in lines if ln and not ln.startswith("%%")), "")
    if not any(first == t or first.startswith(t + " ") or first.startswith(t + "-") for t in MERMAID_TYPES):
        raise ExcalidrawError("mermaid must start with the diagram type, like 'flowchart TD' or 'sequenceDiagram'")
    return text.strip()


# --- the clipboard -------------------------------------------------------------------------------------

def _need_clipboard() -> None:
    if sys.platform == "darwin":            # macOS has its own clipboard and key events
        return
    missing = [c for c in ("wl-copy", "wl-paste", "wtype") if not shutil.which(c)]
    if missing:
        raise ExcalidrawError(f"drawing in Excalidraw needs {' and '.join(missing)} (install wl-clipboard and wtype)")


def _copy(data: bytes) -> None:
    try:
        clipboard.copy(data)
    except clipboard.ClipboardError:
        raise ExcalidrawError("couldn't put the drawing on the clipboard (copying failed)") from None


def _copied(sentinel: str, timeout: float, old: set[str] = frozenset()) -> list[dict] | None:
    """Press Ctrl+C until Excalidraw puts the selection on the clipboard: its elements, or None if it
    never does (nothing selected, or the page isn't listening). old: ids that were there before. A selection
    with any of them isn't the paste (yet): Mermaid is converted in the background, so a Ctrl+C can come
    before it lands, and it must never be taken for the paste (it would be deleted when the paste is moved)."""
    end = time.monotonic() + timeout
    while True:
        screen.press_keys(f"{screen.MOD}+c")
        time.sleep(0.3)
        text = clipboard.text()
        if text and text != sentinel:
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get("type") == "excalidraw/clipboard" and data.get("elements"):
                elements = [x for x in data["elements"] if isinstance(x, dict) and not x.get("isDeleted")]
                if elements and not any(x.get("id") in old for x in elements):
                    return elements
        if time.monotonic() >= end:
            return None
        time.sleep(0.4)


# --- the window ----------------------------------------------------------------------------------------

def _find() -> dict | None:
    """Excalidraw's window: an app (its class says so: the web app, or a desktop app), else a browser window
    showing the page, whose title then starts with the page's own ("Excalidraw Whiteboard - Brave"). Never
    just any window with the word in its title (a terminal on a branch, a page about Excalidraw): the keys
    would go there. A browser tab that isn't the one showing can't be seen."""
    try:
        wins = [w for w in desktop.windows() if w.get("mapped", True)]
    except desktop.DesktopError:
        return None

    def score(w: dict) -> int:
        if "excalidraw" in f"{w.get('class') or ''} {w.get('initialClass') or ''}".lower():
            return 2
        return 1 if (w.get("title") or "").lower().startswith("excalidraw whiteboard") else 0
    hits = sorted((w for w in wins if score(w)), key=lambda w: (-score(w), w.get("focusHistoryID", 99)))
    return hits[0] if hits else None


def _open() -> dict:
    """Open Excalidraw (the user's web app, else excalidraw.com in an app window) and wait for its window."""
    try:
        desktop.launch_app("excalidraw")
    except desktop.DesktopError:
        desktop.open_url(URL, app_mode=True)
    end = time.monotonic() + 20
    while time.monotonic() < end:
        w = _find()
        if w:
            time.sleep(4)                    # the page loads after its window appears
            return _find() or w
        time.sleep(0.3)
    raise ExcalidrawError("Excalidraw didn't open (no window after 20 seconds)")


def _canvas_point(w: dict) -> tuple[int, int]:
    """An empty part of the canvas: left of the middle, below it. Clear of the toolbar (top), the style panel
    (left edge), the welcome screen's menu (centre, about 300 wide: a pointer over it doesn't count as on the
    canvas, so a paste there is dropped) and the zoom and help buttons (bottom corners)."""
    (x, y), (width, height) = w["at"], w["size"]
    px = x + min(260, max(60, width / 2 - 200))
    return round(px), round(y + height * 0.62)


def _ready(open_it: bool) -> tuple[dict, tuple[int, int], bool]:
    """Bring Excalidraw up, ready to take a paste: (window, point on its canvas, whether it was just opened)."""
    w, opened = _find(), False
    if w is None:
        if not open_it:
            raise ExcalidrawError("Excalidraw isn't open")
        w, opened = _open(), True
    screen.focus(w["address"])
    w = next((x for x in desktop.windows() if x["address"] == w["address"]), w)      # its place now it's shown
    pt = _canvas_point(w)
    screen.hover(*pt)
    for key in ("Escape", "Escape", "v"):     # leave any text box or dialog, pick the selection tool
        screen.press_keys(key)
        time.sleep(0.05)
    screen.click_layout(*pt)                 # focus the canvas (a click with the selection tool changes nothing)
    return w, pt, opened


# --- what the tools do ---------------------------------------------------------------------------------

def _extent(elements: list[dict], turned: bool = False) -> tuple[float, float, float, float]:
    """The bounds (x1, y1, x2, y2) of Excalidraw elements on the canvas. turned: as rotated elements really
    reach (else unrotated, as the drawing was built)."""
    xs, ys = [], []
    for x in elements:
        ox, oy, w, h = x.get("x", 0), x.get("y", 0), x.get("width", 0), x.get("height", 0)
        pts = [(ox + p[0], oy + p[1]) for p in x["points"]] if x.get("points") else \
            [(ox, oy), (ox + w, oy), (ox, oy + h), (ox + w, oy + h)]
        angle = x.get("angle") or 0
        if turned and angle:                 # Excalidraw turns an element about the middle of its box
            cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
            cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
            c, s = math.cos(angle), math.sin(angle)
            pts = [(cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c) for px, py in pts]
        xs += [p[0] for p in pts]
        ys += [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


def _overlap(a: tuple, b: tuple, margin: float = 40) -> bool:
    return a[0] < b[2] + margin and b[0] < a[2] + margin and a[1] < b[3] + margin and b[1] < a[3] + margin


def _paste(payload: str, pt: tuple[int, int], timeout: float, old: set[str]) -> list[dict] | None:
    _copy(payload.encode())
    screen.hover(*pt)                        # Excalidraw pastes where the pointer last was
    screen.press_keys(f"{screen.MOD}+v")
    time.sleep(0.4)
    return _copied(payload, timeout, old)


def _delete(landed: list[dict]) -> None:
    """Delete the paste, which must still be the selection: never something the user picked meanwhile."""
    probe = f"auren-excalidraw-probe-{time.time()}"
    _copy(probe.encode())
    now = _copied(probe, 1.5)
    if now is None or {x.get("id") for x in now} != {x.get("id") for x in landed}:
        raise ExcalidrawError("the selection changed while I was drawing, so I left the drawing where it landed: "
                              "look at the screen")
    screen.press_keys("Delete")


def _pan(w: dict, dx: float, dy: float) -> None:
    """Move the canvas (not the drawing) by (dx, dy) screen pixels: middle-button drags inside the window,
    as many as it takes."""
    (x, y), (width, height) = w["at"], w["size"]
    box = (x + min(300, width * 0.3), y + min(150, height * 0.2), x + width * 0.9, y + height * 0.85)
    reach_x, reach_y = (box[2] - box[0]) * 0.9, (box[3] - box[1]) * 0.9
    while abs(dx) >= 1 or abs(dy) >= 1:
        sx, sy = max(-reach_x, min(reach_x, dx)), max(-reach_y, min(reach_y, dy))
        x1 = box[0] if sx > 0 else box[2]
        y1 = box[1] if sy > 0 else box[3]
        screen.drag_layout(x1, y1, x1 + sx, y1 + sy, button="middle")
        dx, dy = dx - sx, dy - sy


def _move(landed: list[dict], target: tuple[float, float], w: dict, pt: tuple[int, int], payload: str,
          timeout: float, old: set[str]) -> list[dict]:
    """Put the drawing's top-left corner at `target` on the canvas. Excalidraw always pastes at the pointer, so:
    take the paste away again, move the canvas under the pointer by the difference, and paste again. The zoom
    was reset to 100 %, so a screen pixel is a canvas pixel; if it's off anyway, the miss tells by how much."""
    zoom = 1.0
    for tries in range(4):
        x1, y1, _, _ = _extent(landed)
        sx, sy = target[0] - x1, target[1] - y1
        log.debug("drawing at %.0f, %.0f; wanted at %.0f, %.0f (zoom %.2f)", x1, y1, *target, zoom)
        if abs(sx) <= 2 and abs(sy) <= 2:
            break
        if tries == 3:
            raise ExcalidrawError(f"the drawing landed {max(abs(sx), abs(sy)):.0f} canvas pixels from its place and "
                                  "moving it didn't help: look at the screen")
        old = old | {x.get("id") for x in landed}
        _delete(landed)                      # the paste is still selected
        _pan(w, -sx * zoom, -sy * zoom)
        again = _paste(payload, pt, timeout, old)
        if again is None:
            raise ExcalidrawError("Excalidraw didn't take the drawing the second time: look at the screen")
        nx, ny, _, _ = _extent(again)
        moved, wanted = (nx - x1, sx) if abs(sx) >= abs(sy) else (ny - y1, sy)
        if abs(wanted) > 20 and abs(moved) > 1:
            zoom *= wanted / moved
        landed = again
    return landed


def draw(elements: list | None = None, mermaid: str = "", clear: bool = False, place: str = "beside") -> str:
    """Paste a drawing into Excalidraw (opening it if needed). place: "beside" what's on the canvas already,
    or "exact" (the elements' own coordinates are canvas coordinates). Returns what landed."""
    if bool(elements) == bool(mermaid.strip()):
        raise ExcalidrawError("give either elements or mermaid (one of them)")
    if place not in ("beside", "exact"):
        raise ExcalidrawError("place must be beside or exact")
    reach = outside(elements) if elements else set()
    if reach and (place != "exact" or clear):
        raise ExcalidrawError(f"arrows start or end at {', '.join(sorted(reach))}, which aren't in this drawing: to "
                              "join shapes already on the canvas, use place exact (and no clear)")

    def make(canvas: dict | None) -> tuple[str, tuple | None, tuple | None]:
        if mermaid.strip():
            return check_mermaid(mermaid), None, None
        skeletons, (x1, y1, x2, y2) = build(elements, canvas)
        return (json.dumps({"type": "excalidraw-api/clipboard", "elements": skeletons}, ensure_ascii=False),
                (x2 - x1, y2 - y1), (x1, y1))

    # check it all before touching the screen (shapes on the canvas stand in until it has been read)
    payload, size, corner = make({ref: ("rectangle", 0, 0, 1, 1) for ref in reach})
    timeout = 12 if mermaid.strip() else 3
    probe = f"auren-excalidraw-probe-{time.time()}"
    _need_clipboard()
    saved = clipboard.save()
    try:
        _copy(probe.encode())
        w, pt, opened = _ready(open_it=True)
        screen.press_keys(f"{screen.MOD}+a")
        time.sleep(0.1)
        existing = _copied(probe, 2) or []
        screen.press_keys("Escape")
        old = {x.get("id") for x in existing}
        if reach:
            missing = reach - old
            if missing:
                raise ExcalidrawError(f"there's nothing with the id {', '.join(sorted(missing))} on the canvas: read it "
                                      "again (excalidraw_read)")
            payload, size, corner = make(boxes_of(existing))
        if existing:
            screen.press_code("shift+0")      # zoom 100 %: moving the canvas by a pixel moves it a canvas pixel
        landed = None
        for attempt in range(2 if opened else 1):     # a page that was still loading drops the first paste
            if attempt:
                time.sleep(3)
                screen.click_layout(*pt)
            landed = _paste(payload, pt, timeout, old)
            if landed is not None:
                break
        if landed is None:
            raise ExcalidrawError("Excalidraw didn't take the drawing (nothing new got selected). Look at the "
                                  "screen: a dialog or a text box may be open")
        if mermaid.strip() and all(x.get("type") == "text" for x in landed):
            _delete(landed)                   # Excalidraw couldn't read the Mermaid and pasted it as text
            raise ExcalidrawError("Excalidraw couldn't read that Mermaid definition: check its syntax")
        if clear:
            # only now the new drawing is known to paste: empty the canvas (it with it) and paste it again
            screen.press_keys(f"{screen.MOD}+a")
            screen.press_keys("Delete")
            _drawn.clear()
            old |= {x.get("id") for x in landed}
            landed = _paste(payload, pt, timeout, old)
            if landed is None:
                raise ExcalidrawError("I cleared the canvas but Excalidraw didn't take the drawing again: Ctrl+Z in "
                                      "Excalidraw brings the old drawing back")
            existing = []
        if existing:
            there = _extent(existing, turned=True)
            if place == "exact" and corner:
                landed = _move(landed, corner, w, pt, payload, timeout, old)
            elif _overlap(_extent(landed, turned=True), there):
                landed = _move(landed, (there[2] + 100, there[1]), w, pt, payload, timeout, old)
        _drawn.update({x["id"]: x.get("version", 0) for x in landed if x.get("id")})
        screen.press_code("shift+2")          # into view, zoomed out if it's bigger than the window
        time.sleep(0.2)
        screen.press_keys("Escape")
    finally:
        clipboard.restore(saved)
    kinds = _count(landed)
    picture = " (as one picture: Mermaid only makes editable shapes for flowchart, sequence, class, state and ER " \
              "diagrams)" if mermaid.strip() and set(kinds) == {"image"} else ""
    sized = f", about {size[0]:.0f}x{size[1]:.0f} canvas pixels" if size else ""
    where = " beside what was there" if existing and place == "beside" else ""
    loose = " Arrows to shapes that were already there touch them but aren't attached." if reach else ""
    cleared = "cleared the canvas, then " if clear else ""
    return (f"{cleared}drew {sum(kinds.values())} elements in Excalidraw{where} ({_describe(kinds)}){sized}{picture}. "
            f"It's in view; look at the screen to check it looks right.{loose}")


def scene() -> list[dict]:
    """Everything on the canvas, as Excalidraw's elements (empty if there's nothing)."""
    probe = f"auren-excalidraw-probe-{time.time()}"
    _need_clipboard()
    saved = clipboard.save()
    try:
        _copy(probe.encode())
        _ready(open_it=False)
        screen.press_keys(f"{screen.MOD}+a")
        time.sleep(0.1)
        elements = _copied(probe, 2) or []
        screen.press_keys("Escape")
    finally:
        clipboard.restore(saved)
    return elements


def drawn_by_auren(elements: list[dict]) -> bool:
    """Is everything on the canvas something Auren drew and nobody changed since (so clearing it loses none of
    the user's work)? Excalidraw raises an element's version whenever it's edited."""
    return all(x.get("id") in _drawn and x.get("version", 0) == _drawn[x["id"]] for x in elements)


def fingerprint(elements: list[dict]) -> list[tuple[str, int]]:
    """What's on the canvas, to tell whether it changed: each element's id and version."""
    return sorted((x.get("id", ""), x.get("version", 0)) for x in elements)


_FONT_NAMES = {1: "hand", 5: "hand", 2: "normal", 6: "normal", 3: "code", 8: "code"}


def summary(elements: list[dict]) -> dict:
    """The canvas for the brain: each element with what's needed to redraw or change it, in the same form
    excalidraw_draw takes (absolute points, labels folded into their shapes, styles that aren't the usual)."""
    labels = {x.get("containerId"): x for x in elements if x.get("type") == "text" and x.get("containerId")}
    children: dict[str, list] = {}
    for x in elements:
        if x.get("frameId"):
            children.setdefault(x["frameId"], []).append(x.get("id"))
    items = []
    for x in elements:
        if x.get("type") == "text" and x.get("containerId"):
            continue
        item = {"id": x.get("id"), "type": x.get("type"), "x": round(x.get("x", 0)), "y": round(x.get("y", 0)),
                "width": round(x.get("width", 0)), "height": round(x.get("height", 0))}
        if x.get("type") == "text":
            item["text"] = x.get("text", "")
        text = labels.get(x.get("id")) or (x if x.get("type") == "text" else None)
        if text is not x and text:
            item["label"] = text.get("text", "")
        if text and text.get("fontSize", 20) != 20:
            item["fontSize"] = text["fontSize"]
        if text and _FONT_NAMES.get(text.get("fontFamily"), "hand") != "hand":
            item["font"] = _FONT_NAMES[text["fontFamily"]]
        for key in ("strokeColor", "backgroundColor"):
            if x.get(key) and x[key] not in ("#1e1e1e", "transparent"):
                item[key] = x[key]
        if x.get("type") in ("rectangle", "diamond"):
            item["rounded"] = bool(x.get("roundness"))
        if x.get("backgroundColor", "transparent") != "transparent" and x.get("fillStyle", "solid") != "solid":
            item["fillStyle"] = x["fillStyle"]
        for key, usual in (("roughness", 1), ("strokeStyle", "solid"), ("strokeWidth", 2), ("opacity", 100)):
            if x.get(key, usual) != usual:
                item[key] = x[key]
        if x.get("angle"):
            item["rotate"] = round(math.degrees(x["angle"])) % 360
        if x.get("type") in ("arrow", "line", "freedraw") and x.get("points"):
            pts = x["points"]
            if len(pts) > 24:
                pts = pts[:: math.ceil(len(pts) / 24)] + [pts[-1]]
            item["points"] = [[round(x["x"] + p[0]), round(x["y"] + p[1])] for p in pts]
            del item["width"], item["height"]
        if x.get("type") in ("arrow", "line"):
            if x.get("elbowed"):
                item["elbow"] = True
            elif x.get("roundness"):
                item["smooth"] = True
            ends = (None, "arrow") if x["type"] == "arrow" else (None, None)
            for key, usual in zip(("startArrowhead", "endArrowhead"), ends, strict=True):
                if x.get(key, usual) != usual:
                    item[key] = x.get(key)
        for key, name in (("startBinding", "start"), ("endBinding", "end")):
            if isinstance(x.get(key), dict) and x[key].get("elementId"):
                item[name] = x[key]["elementId"]
        if x.get("type") == "frame":
            item["name"] = x.get("name") or ""
            item["children"] = children.get(x.get("id"), [])
        if x.get("groupIds"):
            item["group"] = x["groupIds"][-1]
        items.append(item)
    kinds = _count(elements)
    note = "images can't be redrawn: drawing with clear would lose them" if "image" in kinds else ""
    return {"elements": items, "count": len(elements), "drawn_by_auren": drawn_by_auren(elements),
            **({"note": note} if note else {})}


def _count(elements: list[dict]) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for x in elements:
        if x.get("type") == "text" and x.get("containerId"):
            continue                          # a label, counted with its shape
        kinds[x.get("type", "?")] = kinds.get(x.get("type", "?"), 0) + 1
    return kinds


def _describe(kinds: dict[str, int]) -> str:
    return ", ".join(f"{n} {k}{'s' if n != 1 else ''}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
