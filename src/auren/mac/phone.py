"""The phone remote's view of a Mac's network: its private Wi-Fi/LAN addresses, with the rules of
phone.lan_addresses on Linux (private IPv4 only, no /32, no loopback, tunnels, VPNs or VM bridges).

`ifconfig` lists every interface with its flags, its IPv4 addresses (the netmask in hex) and, for most,
a `status:` line. `networksetup -listallhardwareports` names the interfaces that are network hardware
(Wi-Fi, Ethernet, a USB or Thunderbolt adapter, an iPhone over USB): an address is kept only on one of
those, so a VPN or VM interface with a name no list knows is still left out. If networksetup doesn't
answer or lists none, only interfaces named like the Mac's own network hardware (en0, en1...) are kept:
when in doubt, fewer addresses, never more.
"""

from __future__ import annotations

import re
import subprocess

__all__ = ["lan_addresses"]

IFCONFIG = "/sbin/ifconfig"
NETWORKSETUP = "/usr/sbin/networksetup"
# Not the room's Wi-Fi or Ethernet: loopback, VPN and Tailscale tunnels (utun, ipsec, ppp), bridges (the
# Thunderbolt Bridge, Internet Sharing, VMs), AirDrop and its low-latency link, the Apple network
# coprocessor (anpi), the personal hotspot's access point, legacy tunnels, and VM/container NICs.
VIRTUAL = ("lo", "utun", "ipsec", "ppp", "bridge", "awdl", "llw", "anpi", "ap", "gif", "stf", "vmenet", "vmnet",
           "vnic", "vboxnet", "feth", "tun", "tap", "zt", "wg", "docker")
HARDWARE_NAME = re.compile(r"en\d+")      # what macOS names Ethernet and Wi-Fi: the fallback without networksetup


def _run(cmd: list[str]) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def hardware_ports(text: str | None) -> set[str] | None:
    """The devices `networksetup -listallhardwareports` lists (en0, en1...), bridges left out. None: no answer;
    an empty set: it answered, but listed nothing usable (both mean nothing is verified)."""
    if text is None:
        return None
    found = set(re.findall(r"^Device:\s*(\S+)", text, re.M))
    return {d for d in found if not d.startswith(VIRTUAL)}


def parse_ifconfig(text: str) -> list[dict]:
    """`ifconfig` output as [{"name", "flags": set, "inet": [(ip, prefix length)], "status": str | None}]."""
    out: list[dict] = []
    for line in text.splitlines():
        if m := re.match(r"^([A-Za-z0-9_.-]+): flags=\w+<([^>]*)>", line):
            out.append({"name": m[1], "flags": set(filter(None, m[2].split(","))), "inet": [], "status": None})
        elif not out or not line[:1].isspace():
            continue
        elif m := re.match(r"\s+inet (\d+\.\d+\.\d+\.\d+)(?: --> \S+)? netmask (0x[0-9a-fA-F]+|[\d.]+)", line):
            mask = m[2]
            bits = (bin(int(mask, 16)).count("1") if mask.startswith("0x")
                    else sum(bin(int(o)).count("1") for o in mask.split(".")))
            out[-1]["inet"].append((m[1], bits))
        elif m := re.match(r"\s+status: (\S+)", line):
            out[-1]["status"] = m[1]
    return out


def pick_lan(links: list[dict], hardware: set[str] | None = None) -> list[str]:
    """The kept addresses of parse_ifconfig's interfaces (see the module's rules). hardware: the interfaces
    that are network hardware (hardware_ports); None or empty when that isn't known, and then only en<N>
    interfaces are kept."""
    from ..phone import is_lan  # here: auren.phone imports this module at its end
    found: list[str] = []
    for link in links:
        name, flags = link["name"], link["flags"]
        if ("UP" not in flags or "RUNNING" not in flags or "LOOPBACK" in flags or "POINTOPOINT" in flags
                or name.startswith(VIRTUAL) or link.get("status") not in (None, "active")):
            continue
        if name not in hardware if hardware else not HARDWARE_NAME.fullmatch(name):
            continue
        for ip, plen in link["inet"]:
            if plen < 32 and is_lan(ip) and ip not in found:
                found.append(ip)
    return found


def lan_addresses() -> tuple[list[str], str]:
    """The Mac's private IPv4 addresses on real Wi-Fi or Ethernet, or ([], why none)."""
    text = _run([IFCONFIG])
    if text is None:
        return [], "couldn't list the network interfaces (ifconfig didn't answer)"
    return pick_lan(parse_ifconfig(text), hardware_ports(_run([NETWORKSETUP, "-listallhardwareports"]))), ""
