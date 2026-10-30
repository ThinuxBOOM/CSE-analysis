"""
Prove, inside the Linux CI container and before any test runs, that it has no network at all (docs/CI_TEST_STRATEGY.md).
A failed DNS lookup alone would not prove that; this checks the network itself:
- the only interface is loopback (/sys/class/net);
- the kernel has no route (/proc/net/route lists none);
- a connection to 192.0.2.1 (TEST-NET-1, RFC 5737: reserved for documentation, never routed) fails at once with
  ENETUNREACH: the kernel has nowhere to send it, so no packet leaves.
Run only in the container (docker run --network none); never on a host with a network. Exits 1 if a network exists.
"""
import errno
import os
import socket
import sys

interfaces = sorted(os.listdir("/sys/class/net"))
with open("/proc/net/route") as f:
    routes = [line for line in f.read().splitlines()[1:] if line.strip()]
try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(("192.0.2.1", 9))
    outcome = "connected"
except OSError as exc:
    outcome = errno.errorcode.get(exc.errno or 0, str(exc))
isolated = interfaces == ["lo"] and not routes and outcome == "ENETUNREACH"
print(f"interfaces {interfaces}; routes {len(routes)}; connect to 192.0.2.1: {outcome}: "
      f"{'no network' if isolated else 'NETWORK PRESENT'}")
sys.exit(0 if isolated else 1)
