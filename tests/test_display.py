"""Waiting for the session's display under systemd (display.py)."""

from auren import display


def test_outside_systemd_or_with_a_display_it_carries_on(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert display.wait() is None
    monkeypatch.setenv("INVOCATION_ID", "x")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    assert display.wait() is None


def test_a_mac_never_waits_for_a_wayland_display(monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "x")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(display.sys, "platform", "darwin")
    assert display.wait() is None


def test_a_display_that_arrives_late_restarts_the_service(monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "x")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    envs = iter([{}, {}, {"WAYLAND_DISPLAY": "wayland-1"}])
    monkeypatch.setattr(display, "manager_env", lambda: next(envs))
    assert display.wait(timeout=60, sleep=lambda s: None) == display.RETRY


def test_no_display_at_all_stops_it(monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "x")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(display, "manager_env", lambda: {})
    assert display.wait(timeout=0, sleep=lambda s: None) == display.NO_DISPLAY


def test_with_display_runs_the_program(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    ran = []
    monkeypatch.setattr(display.os, "execvp", lambda prog, argv: ran.append(argv))
    display.main(["qs", "-p", "/x"])
    assert ran == [["qs", "-p", "/x"]]
    assert display.main([]) == 2
