"""Access from outside the home, through Tailscale (a private network between your own devices).

The web app never faces the open internet. With `--remote` it also listens on this PC's Tailscale
address (100.x.y.z), which only devices signed in to the owner's Tailscale account can reach, and it
refuses connections from any other address (defence in depth, in case the PC also sits on a network
where a port is open). No router configuration, no public port.
"""
from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import subprocess

TAILNET = ipaddress.ip_network("100.64.0.0/10")        # Tailscale's address range (CGNAT)
TAILNET6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_WINDOWS_CLI = r"C:\Program Files\Tailscale\tailscale.exe"


def tailscale_cli() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    return _WINDOWS_CLI if os.path.isfile(_WINDOWS_CLI) else None


def tailscale_ips(run=subprocess.run) -> list[str]:
    """This PC's Tailscale IPv4 addresses ([] when Tailscale is not installed or not signed in)."""
    ips: list[str] = []
    cli = tailscale_cli()
    if cli:
        try:
            out = run([cli, "ip", "-4"], capture_output=True, text=True, timeout=10).stdout
            ips = [s.strip() for s in out.splitlines() if s.strip()]
        except (OSError, subprocess.SubprocessError):
            ips = []
    if not ips:  # the CLI can be missing from PATH; the address still shows on the network adapter
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
            ips = sorted({i[4][0] for i in infos})
        except OSError:
            ips = []
    return [ip for ip in ips if _in_tailnet(ip)]


def _in_tailnet(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr in TAILNET or addr in TAILNET6


def client_allowed(ip: str, allow_lan: bool = False) -> bool:
    """Who may talk to the server at all: this PC, the owner's Tailscale devices, and (opt-in) the home LAN."""
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_loopback or _in_tailnet(str(addr)):
        return True
    return allow_lan and addr.is_private
