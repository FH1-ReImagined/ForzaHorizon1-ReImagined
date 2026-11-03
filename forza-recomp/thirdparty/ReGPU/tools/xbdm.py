"""Talks to a real Xbox 360's debug monitor (XBDM, TCP 730) for reference captures.

  xbdm.py [--host IP] cmd TEXT       send one raw command, print the reply
  xbdm.py [--host IP] info           console name, XBDM version, running title
  xbdm.py [--host IP] peek ADDR [N]  N bytes (default 64) of memory at ADDR (hex), as hex dump

Host: --host, else XBDM_HOST, else 192.168.0.116. Read-only commands only, except what `cmd` is given.
Replies: 200 OK (one line), 201 connected, 202 multiline (until a lone "."), 203 binary.
"""
import os
import socket
import sys


class Xbdm:
    def __init__(self, host, port=730, timeout=5.0):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.f = self.s.makefile("rb")
        banner = self._line()
        if not banner.startswith("201"):
            raise RuntimeError(f"unexpected banner: {banner}")

    def _line(self):
        line = self.f.readline()
        if not line:
            raise ConnectionError("connection closed")
        return line.decode("latin-1").rstrip("\r\n")

    def send(self, cmd):
        """One command: (status line, extra lines for a 202 reply)."""
        self.s.sendall(cmd.encode("latin-1") + b"\r\n")
        status = self._line()
        lines = []
        if status.startswith("202"):
            while (l := self._line()) != ".":
                lines.append(l)
        return status, lines

    def getmem(self, addr, length):
        """Memory as bytes; '?' bytes (unmapped) come back as None."""
        status, lines = self.send(f"getmem addr=0x{addr:08X} length=0x{length:X}")
        if not status.startswith("202"):
            raise RuntimeError(status)
        hexs = "".join(lines)
        return [None if hexs[i:i + 2] == "??" else int(hexs[i:i + 2], 16) for i in range(0, len(hexs), 2)]

    def screenshot(self):
        """(info dict, raw framebuffer bytes): XBDM sends a key=value line, then framebuffersize bytes."""
        self.s.sendall(b"screenshot\r\n")
        status = self._line()
        if not status.startswith("203"):
            raise RuntimeError(status)
        info = {}
        for kv in self._line().replace(",", " ").split():
            k, _, v = kv.partition("=")
            info[k] = int(v, 0) if v[:2] in ("0x", "0X") or v.isdigit() else v
        size = info["framebuffersize"]
        data = bytearray()
        while len(data) < size:
            chunk = self.f.read(min(1 << 16, size - len(data)))
            if not chunk:
                raise ConnectionError("screenshot cut short")
            data += chunk
        return info, bytes(data)

    def close(self):
        try:
            self.s.sendall(b"bye\r\n")
        except OSError:
            pass
        self.s.close()


def main(argv):
    host = os.environ.get("XBDM_HOST", "192.168.0.116")
    if len(argv) >= 2 and argv[0] == "--host":
        host, argv = argv[1], argv[2:]
    if not argv:
        print(__doc__)
        return 1
    x = Xbdm(host)
    try:
        if argv[0] == "cmd":
            status, lines = x.send(" ".join(argv[1:]))
            print(status)
            for l in lines:
                print(l)
        elif argv[0] == "info":
            for c in ("dbgname", "dmversion", "xbeinfo running"):
                status, lines = x.send(c)
                print(f"{c}: {status}")
                for l in lines:
                    print("   ", l)
        elif argv[0] == "peek":
            addr = int(argv[1], 16)
            n = int(argv[2], 0) if len(argv) > 2 else 64
            data = x.getmem(addr, n)
            for i in range(0, len(data), 16):
                row = data[i:i + 16]
                print(f"{addr + i:08X}  " + " ".join("??" if b is None else f"{b:02X}" for b in row))
        elif argv[0] == "screenshot":
            info, data = x.screenshot()
            out = argv[1] if len(argv) > 1 else "out/xbox_fb.raw"
            open(out, "wb").write(data)
            print(info, len(data), "bytes ->", out)
        else:
            print(f"unknown command {argv[0]}")
            return 1
    finally:
        x.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
