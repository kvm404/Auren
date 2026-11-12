"""macOS versions of the parts of Auren that talk to the desktop.

Auren's modules are written for Omarchy. Where macOS needs something else, the module ends with

    if MAC:
        from ..mac.desktop import *    # the macOS versions of the functions above

so the rest of Auren calls the same names on both systems. Each module here sets `__all__` to exactly
what it replaces. The Linux code and its tests stay as they are.

What macOS uses instead (docs/macos.md has the whole list):
- audio: sounddevice (PortAudio over Core Audio), for pw-record and pw-cat
- windows and apps: Quartz's window list, the Accessibility API and `open`, for hyprctl and omarchy-*
- mouse and keyboard: Quartz events, for uinput and wtype
- screenshots: `screencapture`, for grim
- clipboard: NSPasteboard, for wl-clipboard
- notifications, volume, media: AppleScript (`osascript`)
- the talk keys: an event tap in the daemon (hotkey.py), for Hyprland's bindings
- services: launchd agents (service.py), for systemd
- the mascot: PySide6 (mascot.py), for Quickshell; a menu bar icon for the Omarchy bar panel
"""
