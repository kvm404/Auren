"""Excalidraw: building drawings, aiming arrows, placing pastes, reading the canvas, and the clear question."""

import json

import pytest

from auren import tools
from auren.hands import excalidraw as ex
from auren.hands import screen


def _by_id(skeletons):
    return {e["id"]: e for e in skeletons}


def test_shapes_take_colour_names_plain_labels_and_fit_them():
    sk, _ = ex.build([{"type": "rectangle", "id": "a", "x": 0, "y": 0, "label": "A rather long label",
                       "fill": "light-blue", "stroke": "dark-blue"},
                      {"type": "ellipse", "id": "b", "x": 400, "y": 0, "backgroundColor": "#ABC"}])
    a, b = _by_id(sk)["a"], _by_id(sk)["b"]
    assert a["label"] == {"text": "A rather long label"}
    assert (a["backgroundColor"], a["strokeColor"], a["fillStyle"]) == ("#a5d8ff", "#1971c2", "solid")
    assert a["width"] > ex.text_size("A rather long label", 20)[0] and a["roundness"] == {"type": 3}
    assert "fill" not in a and "stroke" not in a
    assert (b["width"], b["height"]) == (100, 100) and "roundness" not in b      # no label: picture part


def test_bad_input_says_which_element_and_why():
    cases = [
        ([{"type": "circle", "x": 0, "y": 0}], "type must be one of"),
        ([{"type": "rectangle", "x": 0}], "needs a number for 'y'"),
        ([{"type": "rectangle", "x": 0, "y": 0, "strokeColor": "sunset"}], "unknown colour"),
        ([{"type": "rectangle", "id": "a", "x": 0, "y": 0}, {"type": "ellipse", "id": "a", "x": 0, "y": 0}],
         "used twice"),
        ([{"type": "arrow", "start": "nope", "end": "nada"}], "isn't the id of a shape"),
        ([{"type": "line", "points": [[0, 0]]}], "at least 2 points"),
        ([{"type": "arrow", "points": "up"}], "points must be"),
        ([{"type": "frame", "children": ["ghost"]}], "no elements with the ids ghost"),
    ]
    for elements, message in cases:
        with pytest.raises(ex.ExcalidrawError, match=message):
            ex.build(elements)


def test_an_arrow_between_shapes_runs_edge_to_edge_and_is_bound():
    sk, _ = ex.build([{"type": "rectangle", "id": "a", "x": 0, "y": 0, "width": 100, "height": 50},
                      {"type": "ellipse", "id": "b", "x": 300, "y": 0, "width": 100, "height": 50},
                      {"type": "arrow", "id": "ab", "start": "a", "end": "b", "label": "go"}])
    arrow = _by_id(sk)["ab"]
    assert (arrow["x"], arrow["y"]) == (100 + ex.GAP, 25)                  # leaves a's right side
    assert arrow["points"][-1] == [300 - ex.GAP - arrow["x"], 0]           # meets b's left side
    assert arrow["start"] == {"id": "a"} and arrow["end"] == {"id": "b"} and arrow["label"] == {"text": "go"}


def test_elbow_arrows_go_straight_when_shapes_face_each_other_and_around_others_when_they_can():
    a, b = ("rectangle", 0, 0, 100, 50), ("diamond", 10, 200, 100, 80)
    assert ex.route(a, b, None, elbow=True) == [(50, 50 + ex.GAP), (50, pytest.approx(200 - ex.GAP + 8))]
    c = ("rectangle", 400, 300, 100, 50)
    z = ex.route(a, c, None, elbow=True)
    assert len(z) == 4 and z[0][1] == z[1][1] and z[1][0] == z[2][0]     # out, across, in: right angles
    blocker = ("rectangle", 120, -20, 200, 120)                           # in the way of the sideways route
    around = ex.route(a, c, None, elbow=True, others=[blocker])
    assert ex._crossings(around, [blocker]) == 0


def test_a_closed_line_can_be_filled_and_points_become_relative():
    sk, bounds = ex.build([{"type": "line", "points": [[10, 10], [60, 10], [35, 50], [10, 10]], "fill": "yellow",
                            "smooth": True}])
    tri = sk[0]
    assert tri["polygon"] and tri["roundness"] == {"type": 2} and tri["backgroundColor"] == "#fab005"
    assert (tri["x"], tri["y"], tri["points"][1], tri["width"], tri["height"]) == (10, 10, [50, 0], 50, 40)
    assert bounds == (10, 10, 60, 50)


def test_arrows_may_reach_shapes_already_on_the_canvas_unbound():
    assert ex.outside([{"type": "rectangle", "id": "new", "x": 0, "y": 0},
                       {"type": "arrow", "start": "old", "end": "new"}]) == {"old"}
    canvas = {"old": ("rectangle", -300, 0, 100, 50)}
    sk, _ = ex.build([{"type": "rectangle", "id": "new", "x": 0, "y": 0, "width": 100, "height": 50},
                      {"type": "arrow", "id": "x", "start": "old", "end": "new"}], canvas)
    arrow = _by_id(sk)["x"]
    assert arrow["x"] == -200 + ex.GAP and "start" not in arrow and arrow["end"] == {"id": "new"}
    with pytest.raises(ex.ExcalidrawError, match="place exact"):
        ex.draw([{"type": "rectangle", "id": "new", "x": 0, "y": 0}, {"type": "arrow", "start": "old", "end": "new"}])


def test_mermaid_must_name_its_diagram_type():
    assert ex.check_mermaid("%% a note\nflowchart LR\n A --> B\n") .startswith("%% a note")
    assert ex.check_mermaid("sequenceDiagram\n A->>B: hi")
    with pytest.raises(ex.ExcalidrawError, match="diagram type"):
        ex.check_mermaid("A --> B")
    with pytest.raises(ex.ExcalidrawError, match="either elements or mermaid"):
        ex.draw()


def test_a_copy_of_what_was_selected_before_is_not_the_paste(monkeypatch):
    """Mermaid lands after a moment: a Ctrl+C before that must not be taken for it (it would be deleted)."""
    clips = iter([json.dumps({"type": "excalidraw/clipboard", "elements": [{"id": "users"}]}),
                  json.dumps({"type": "excalidraw/clipboard", "elements": [{"id": "new1"}, {"id": "new2"}]})])
    monkeypatch.setattr(screen, "press_keys", lambda keys: None)
    monkeypatch.setattr(ex.clipboard, "text", lambda: next(clips))
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    landed = ex._copied("payload", timeout=5, old={"users"})
    assert [x["id"] for x in landed] == ["new1", "new2"]


def test_a_paste_is_moved_to_its_place_even_when_the_zoom_isnt_what_was_assumed(monkeypatch):
    """Excalidraw pastes at the pointer: moving it means panning the canvas and pasting again. Here the real
    zoom is 1.25, not 1: the first try misses and the miss corrects the next."""
    view = {"x": 0.0, "y": 0.0}                       # where the canvas has been panned to, on screen
    zoom = 1.25
    pasted = []

    def paste(payload, pt, timeout, old):
        x, y = 500 - view["x"] / zoom, 300 - view["y"] / zoom     # the pointer's place on the canvas
        pasted.append((x, y))
        return [{"id": f"p{len(pasted)}", "type": "rectangle", "x": x, "y": y, "width": 100, "height": 50}]

    def pan(w, dx, dy):
        view["x"] += dx
        view["y"] += dy

    monkeypatch.setattr(ex, "_paste", paste)
    monkeypatch.setattr(ex, "_pan", pan)
    monkeypatch.setattr(ex, "_delete", lambda landed: None)
    first = paste("", None, 0, set())
    landed = ex._move(first, (1200, 100), {}, (0, 0), "", 3, set())
    assert abs(landed[0]["x"] - 1200) <= 2 and abs(landed[0]["y"] - 100) <= 2
    assert len(pasted) == 3                            # the first paste, a miss, then on target

    monkeypatch.setattr(ex, "_pan", lambda w, dx, dy: None)                  # panning does nothing at all
    with pytest.raises(ex.ExcalidrawError, match="moving it didn't help"):
        ex._move(paste("", None, 0, set()), (0, 0), {}, (0, 0), "", 3, set())


def test_only_the_paste_is_deleted_never_what_the_user_selected_meanwhile(monkeypatch):
    pressed = []
    monkeypatch.setattr(ex, "_copy", lambda data: None)
    monkeypatch.setattr(screen, "press_keys", pressed.append)
    monkeypatch.setattr(ex, "_copied", lambda sentinel, timeout, old=frozenset(): [{"id": "theirs"}])
    with pytest.raises(ex.ExcalidrawError, match="selection changed"):
        ex._delete([{"id": "p1"}, {"id": "p2"}])
    assert pressed == []
    monkeypatch.setattr(ex, "_copied", lambda sentinel, timeout, old=frozenset(): [{"id": "p2"}, {"id": "p1"}])
    ex._delete([{"id": "p1"}, {"id": "p2"}])
    assert pressed == ["Delete"]


@pytest.fixture
def desk(monkeypatch):
    """Excalidraw on a fake desktop: the canvas holds the user's drawing; pastes come back as `pastes` says."""
    d = {"keys": [], "pastes": []}
    monkeypatch.setattr(ex, "_need_clipboard", lambda: None)
    monkeypatch.setattr(ex.clipboard, "save", lambda: ("text/plain", b"theirs"))
    monkeypatch.setattr(ex.clipboard, "restore", lambda saved: d.setdefault("restored", saved))
    monkeypatch.setattr(ex, "_copy", lambda data: None)
    monkeypatch.setattr(ex, "_ready", lambda open_it: ({}, (0, 0), False))
    monkeypatch.setattr(ex, "_copied", lambda sentinel, timeout, old=frozenset(): [
        {"id": "theirs", "type": "rectangle", "x": 0, "y": 0, "width": 100, "height": 50}])
    monkeypatch.setattr(ex, "_paste", lambda payload, pt, timeout, old: d["pastes"].pop(0))
    monkeypatch.setattr(screen, "press_keys", d["keys"].append)
    monkeypatch.setattr(screen, "press_code", d["keys"].append)
    return d


def test_clearing_deletes_the_old_drawing_only_once_the_new_one_has_pasted(desk):
    desk["pastes"] = [None]
    with pytest.raises(ex.ExcalidrawError, match="didn't take"):
        ex.draw([{"type": "rectangle", "x": 0, "y": 0}], clear=True)
    assert "Delete" not in desk["keys"] and desk["restored"] == ("text/plain", b"theirs")

    desk["keys"].clear()
    new = [{"id": "n1", "type": "rectangle", "x": 0, "y": 0, "width": 100, "height": 50, "version": 3}]
    desk["pastes"] = [new, new]
    assert ex.draw([{"type": "rectangle", "x": 0, "y": 0}], clear=True).startswith("cleared the canvas")
    assert desk["keys"].count("Delete") == 1 and ex._drawn == {"n1": 3}


def test_reading_the_canvas_folds_labels_into_shapes_and_knows_what_auren_drew(monkeypatch):
    elements = [{"id": "r", "type": "rectangle", "x": 0, "y": 0, "width": 100, "height": 50, "roundness": None,
                 "backgroundColor": "#a5d8ff", "boundElements": [{"type": "text", "id": "t"}]},
                {"id": "t", "type": "text", "x": 10, "y": 10, "width": 50, "height": 20, "text": "Box",
                 "containerId": "r"},
                {"id": "a", "type": "arrow", "x": 100, "y": 25, "points": [[0, 0], [80, 0]],
                 "startBinding": {"elementId": "r"}, "endBinding": None}]
    s = ex.summary(elements)
    r, a = s["elements"]
    assert r["label"] == "Box" and r["rounded"] is False and r["backgroundColor"] == "#a5d8ff"
    assert a["points"] == [[100, 25], [180, 25]] and a["start"] == "r" and "end" not in a
    assert len(s["elements"]) == 2 and s["count"] == 3
    monkeypatch.setattr(ex, "_drawn", {"r": 0, "t": 0})
    assert not ex.drawn_by_auren(elements)
    monkeypatch.setattr(ex, "_drawn", {"r": 0, "t": 0, "a": 0})
    assert ex.drawn_by_auren(elements)
    elements[1]["version"] = 2                                     # the user edited the label since
    assert not ex.drawn_by_auren(elements)


def test_a_drawing_read_back_redraws_as_it_was():
    elements = [{"id": "f", "type": "frame", "x": 0, "y": 0, "width": 400, "height": 300, "name": "Plan"},
                {"id": "r", "type": "rectangle", "x": 10, "y": 10, "width": 100, "height": 50, "frameId": "f",
                 "angle": 3.14159 / 2, "opacity": 60, "backgroundColor": "#a5d8ff", "fillStyle": "hachure"},
                {"id": "t", "type": "text", "x": 20, "y": 200, "width": 50, "height": 20, "text": "Hi",
                 "fontSize": 28, "fontFamily": 8, "frameId": "f"},
                {"id": "a", "type": "arrow", "x": 0, "y": 0, "points": [[0, 0], [50, 50]], "elbowed": True,
                 "startArrowhead": "dot", "endArrowhead": "arrow"}]
    f, r, t, a = ex.summary(elements)["elements"]
    assert f["name"] == "Plan" and f["children"] == ["r", "t"]
    assert (r["rotate"], r["opacity"], r["fillStyle"]) == (90, 60, "hachure")
    assert (t["fontSize"], t["font"]) == (28, "code")
    assert a["elbow"] and a["startArrowhead"] == "dot" and "endArrowhead" not in a
    ex.build([f, r, t, a])                                         # and it builds again


def test_what_is_beside_counts_rotated_shapes_as_they_reach():
    tall = {"type": "rectangle", "x": 0, "y": 0, "width": 100, "height": 1000, "angle": 3.14159265 / 2}
    assert ex._extent([tall]) == (0, 0, 100, 1000)
    x1, y1, x2, y2 = ex._extent([tall], turned=True)
    assert (round(x1), round(y1), round(x2), round(y2)) == (-450, 450, 550, 550)


@pytest.fixture
def rt(tmp_path):
    r = tools.Runtime(tmp_path / "tools.sock")
    r.new_turn("daemon")
    return r


def test_clearing_the_users_work_asks_first_but_clearing_aurens_own_does_not(rt, monkeypatch):
    drawn = []
    canvas = [{"id": "theirs", "type": "rectangle"}]
    monkeypatch.setattr(ex, "scene", lambda: list(canvas))
    monkeypatch.setattr(ex, "draw", lambda elements, mermaid, clear, place: drawn.append(clear) or "drew it")
    asked = json.loads(rt.call("daemon", "excalidraw_draw", {"mermaid": "flowchart LR\n A-->B", "clear": True})[0])
    assert asked["status"] == "needs_confirmation" and "Clear the Excalidraw canvas (1 element;" in asked["ask_user"]
    assert drawn == []
    rt.new_turn("daemon")
    canvas.append({"id": "more", "type": "ellipse"})                 # the user drew more meanwhile
    assert "canvas changed" in rt.call("daemon", "confirm_action", {"token": asked["token"]})[0]

    monkeypatch.setattr(ex, "_drawn", {"theirs": 0, "more": 0})    # all of it Auren's own drawing
    assert rt.call("daemon", "excalidraw_draw", {"mermaid": "flowchart LR\n A-->B", "clear": True}) == ["drew it"]
    assert drawn == [True]


def test_clearing_a_canvas_that_cant_be_read_clears_nothing(rt, monkeypatch):
    """Closed (opening it brings back the user's drawing) or unreadable: draw beside, don't clear unasked."""
    drawn = []

    def closed():
        raise ex.ExcalidrawError("Excalidraw isn't open")
    monkeypatch.setattr(ex, "scene", closed)
    monkeypatch.setattr(ex, "draw", lambda elements, mermaid, clear, place: drawn.append(clear) or "drew it")
    assert rt.call("daemon", "excalidraw_draw", {"mermaid": "flowchart LR\n A-->B", "clear": True}) == ["drew it"]
    assert drawn == [False]


def test_the_brain_is_told_to_draw_with_the_tool_only_when_it_has_it():
    from auren.brain import prompt
    assert "always excalidraw_draw" in prompt.rules({"screen"})
    assert "excalidraw" not in prompt.rules(set()).lower()


def test_physical_keys_are_only_modifiers_and_digits():
    with pytest.raises(screen.ScreenError, match="modifiers and a digit"):
        screen.press_code("ctrl+q")


def test_a_shortcut_with_a_digit_is_pressed_by_physical_key(monkeypatch):
    sent = []
    monkeypatch.setattr(screen, "press_code", lambda combo: sent.append(("code", combo)) or "pressed")
    monkeypatch.setattr(screen, "_sh", lambda cmd, timeout=10: sent.append(("wtype", cmd[cmd.index("-k") + 1])) or "")
    monkeypatch.setattr(screen, "_refocus", lambda: None)
    for combo in ("ctrl+2", "Alt+9", "ctrl+t", "2"):
        screen.press_keys(combo)
    assert sent == [("code", "ctrl+2"), ("code", "Alt+9"), ("wtype", "t"), ("wtype", "2")]


def test_a_physical_key_is_let_go_even_when_pressing_it_fails(monkeypatch):
    from evdev import ecodes as e
    up = []

    class Keyboard:
        def write(self, kind, code, value):
            if value == 0:
                up.append(code)
            elif code == e.KEY_2:
                raise OSError("the device went away")

        def syn(self):
            pass
    monkeypatch.setattr(screen, "_refocus", lambda: None)
    monkeypatch.setattr(screen, "_keys", Keyboard)
    with pytest.raises(OSError):
        screen.press_code("shift+2")
    assert up == [e.KEY_2, e.KEY_LEFTSHIFT]


def test_the_paste_point_is_left_of_the_welcome_menu_in_a_half_screen_window_too():
    for width in (600, 763, 1536):
        x, _ = ex._canvas_point({"at": (100, 0), "size": (width, 800)})
        assert 100 + 40 <= x <= 100 + width / 2 - 170


def test_only_excalidraw_itself_is_taken_for_its_window(monkeypatch):
    from auren.hands import desktop
    wins = [{"class": "foot", "title": "auren on excalidraw-drawing", "focusHistoryID": 0},
            {"class": "brave-browser", "title": "Draw in Excalidraw by kvm404 · Pull Request #19 - Brave",
             "focusHistoryID": 1}]
    monkeypatch.setattr(desktop, "windows", lambda: list(wins))
    assert ex._find() is None
    wins.append({"class": "brave-browser", "title": "Excalidraw Whiteboard - Brave", "focusHistoryID": 3})
    assert ex._find()["focusHistoryID"] == 3                          # the page in a browser tab
    wins.append({"class": "brave-excalidraw.com__-Default", "title": "Excalidraw Whiteboard", "focusHistoryID": 5})
    assert ex._find()["focusHistoryID"] == 5                          # the web app comes first
