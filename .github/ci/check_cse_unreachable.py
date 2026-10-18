"""
Check, before any test runs, that CSE cannot be reached from this CI job (docs/CI_TEST_STRATEGY.md).

Each host name CSE's code uses must either not resolve (the Linux container has no network at all) or resolve only to
0.0.0.0 / :: (the Windows jobs block the names in the hosts file). The check never opens a connection: a name that
resolves to any other address fails the job at once, so the check itself can never contact CSE. Prints one line per
host; exits 1 if any name resolves to a real address.
"""
import socket
import sys

HOSTS = ("www.cse.lk", "cdn.cse.lk", "cse.lk")
BLOCKED = {"0.0.0.0", "::"}

reachable = []
for host in HOSTS:
    try:
        addrs = sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
    except OSError as exc:
        print(f"{host}: does not resolve ({type(exc).__name__}): unreachable")
        continue
    if set(addrs) <= BLOCKED:
        print(f"{host}: resolves only to {addrs}: unreachable")
    else:
        reachable.append(host)
        print(f"{host}: resolves to {addrs}: NOT BLOCKED (no connection attempted)")
sys.exit(1 if reachable else 0)
