#!/usr/bin/env python3
"""A virtual DualSense over /dev/uhid that records what the adaptive triggers are told (#692).

    tools/virtual_dualsense.py [--bus usb|bt] [--edge] [--log FILE] [--seconds N]

The kernel makes it a real HID device with a /dev/hidrawN node, the reports and sizes of a
DualSense, so hidapi and the launcher's haptics writer open and write it like a controller
(tools/trigger_harness.py points the writer at this node only). --identity sony gives it Sony's
VID/PID: hid-playstation binds it as it binds a real pad and every Sony-aware program (SDL, Steam
Input, remappers) takes it for one. The default test identity (pid.codes 1209:0001, the same reports
on a vendor usage page) gets a hidraw node and no joystick, so Steam Input, remappers and games
on the desktop never see it (Steam Input mirrors even an unknown joystick as an Xbox pad). Every output report written to it is decoded (valid flags, rumble
motors, and each trigger's effect: mode, active zones, per-zone force or amplitude, frequency)
and written to the log as one JSON line with a monotonic time stamp. It also sends input
reports: set_triggers() moves L2/R2, so a test can move the triggers and watch whether the
effects follow them.

Docs: docs/telemetry.md "Measuring the trigger effects". tools/trigger_harness.py drives it with
the launcher's real haptics service and a running game.

Safety: the virtual device has its own unique ID (HID_UNIQ) and lives under
/sys/devices/virtual/misc/uhid; hidraw_path() returns only that node, never a real controller.
The USB descriptor is a real DualSense's (273 bytes); the Bluetooth one declares the reports a
DualSense has over Bluetooth (0x01, 0x31, the feature reports hid-playstation reads), with CRCs
where the pad has them. With --identity sony, the DualSense Edge PID (--edge) keeps a game's SDL off
the user's own DualSense: allow only 054C/0DF2 there (SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT).
"""
import argparse
import glob
import json
import os
import struct
import sys
import threading
import time
import zlib

SONY = 0x054C
TEST_VID, TEST_PID = 0x1209, 0x0001  # pid.codes' test IDs: no driver, Steam or remapper claims it
DUALSENSE, DUALSENSE_EDGE = 0x0CE6, 0x0DF2
BUS_USB, BUS_BLUETOOTH = 0x03, 0x05

# A real DualSense's USB report descriptor (/sys/class/hidraw/*/device/report_descriptor).
USB_DESCRIPTOR = bytes.fromhex(
    "05010905a1018501093009310932093509330934150026ff007508950681020600ff09209501810205010939"
    "150025073500463b016514750495018142650005091901290f150025017501950f81020600ff0921950d8102"
    "0600ff0922150026ff0075089534810285020923952f9102850509339528b10285080934952fb10285090924"
    "9513b102850a0925951ab10285200926953fb102852109279504b10285220940953fb10285800928953fb102"
    "85810929953fb1028582092a9509b1028583092b953fb1028584092c953fb1028585092d9502b10285a0092e"
    "9501b10285e0092f953fb10285f00930953fb10285f10931953fb10285f20932950fb10285f40935953fb102"
    "85f509369503b102c0")


def _bt_descriptor():
    """Report 0x01 (the short report a pad sends before it is set up), 0x31 in and out (78 bytes
    with the report ID and CRC), and the feature reports, with the sizes a DualSense has."""
    d = bytes.fromhex(
        "05010905a101"                                # Generic Desktop, Game Pad, Application
        "8501"                                        # report 0x01
        "0930093109320935150026ff0075089504" "8102"   # 4 stick axes
        "0939150025073500463b01651475049501" "8142"   # d-pad
        "650005091901290e150025017501950e" "8102"     # 14 buttons
        "750695018101"                                # padding
        "050109330934150026ff0075089502" "8102"       # L2, R2
        "0600ff" "8531" "09317508954d" "8102"         # report 0x31 in: 77 bytes
        "093b954d" "9102")                            # report 0x31 out: 77 bytes
    for report, size in ((0x05, 40), (0x08, 47), (0x09, 19), (0x20, 63), (0x22, 63), (0x80, 63),
                         (0x81, 63), (0xF0, 63), (0xF1, 63), (0xF2, 15)):  # FEATURES
        d += bytes([0x85, report, 0x09, 0x40, 0x95, size, 0xB1, 0x02])
    return d + b"\xc0"


BT_DESCRIPTOR = _bt_descriptor()
FEATURES = ((0x05, 40), (0x08, 47), (0x09, 19), (0x20, 63), (0x22, 63), (0x80, 63), (0x81, 63),
            (0xF0, 63), (0xF1, 63), (0xF2, 15))


def vendor_descriptor(bus):
    """The test identity: a DualSense's report IDs and sizes on a vendor usage page only, so the
    kernel makes a hidraw node and no joystick (no game, Steam Input or remapper picks it up)."""
    reports = [(0x81, 0x01, 9), (0x81, 0x31, 77), (0x91, 0x31, 77)] if bus == BUS_BLUETOOTH \
        else [(0x81, 0x01, 63), (0x91, 0x02, 47)]
    d = bytes.fromhex("0600ff0901a101150026ff007508")
    for kind, report, size in reports + [(0xB1, r, n) for r, n in FEATURES]:
        d += bytes([0x85, report, 0x09, 0x01, 0x95, size, kind, 0x02])
    return d + b"\xc0"

# linux/uhid.h
UHID_DESTROY, UHID_START, UHID_STOP, UHID_OPEN, UHID_CLOSE, UHID_OUTPUT = 1, 2, 3, 4, 5, 6
UHID_GET_REPORT, UHID_GET_REPORT_REPLY, UHID_CREATE2, UHID_INPUT2 = 9, 10, 11, 12
UHID_SET_REPORT, UHID_SET_REPORT_REPLY = 13, 14
UHID_FEATURE_REPORT = 0
EVENT_SIZE = 4 + 4372  # struct uhid_event: type and the largest member (uhid_create2_req)

# CRC32 seeds of the Bluetooth reports: input 0xA1, output 0xA2, feature 0xA3.
def _crc(seed, data):
    return struct.pack("<I", zlib.crc32(bytes([seed]) + bytes(data)) & 0xFFFFFFFF)


MODES = {0x05: "off", 0x00: "off", 0x01: "simple_feedback", 0x02: "simple_weapon",
         0x06: "simple_vibration", 0x21: "feedback", 0x22: "slope_feedback", 0x25: "weapon",
         0x26: "vibration", 0x27: "machine"}


def _zones(params):
    """Active-zone mask (u16) and 3-bit strengths (u32, value-1): per-zone 0-8."""
    active = params[0] | params[1] << 8
    strength = params[2] | params[3] << 8 | params[4] << 16 | params[5] << 24
    return [((strength >> 3 * i) & 7) + 1 if active >> i & 1 else 0 for i in range(10)]


def decode_trigger(block):
    """One trigger's 11 effect bytes: mode and parameters, by the community DualSense docs
    (Nielk1's trigger effect factory, nondebug/dualsense)."""
    mode, p = block[0], list(block[1:11])
    out = {"mode": MODES.get(mode, "0x%02x" % mode)}
    if mode in (0x21, 0x26):
        zones = _zones(p)
        out["zones"] = zones
        out["force" if mode == 0x21 else "amplitude"] = max(zones)
        active = [i for i, z in enumerate(zones) if z]
        out["start"], out["end"] = (active[0], active[-1]) if active else (None, None)
        if mode == 0x26:
            out["frequency"] = p[8]
    elif mode == 0x01:
        out.update(start=p[0], force=p[1])
    elif mode == 0x06:
        out.update(frequency=p[0], amplitude=p[1], start=p[2])
    elif mode not in (0x05, 0x00):
        out["raw"] = bytes(p).hex()
    return out


def decode_output(data):
    """A DualSense output report (USB 0x02 or Bluetooth 0x31) as a dict, or None."""
    data = bytes(data)
    if not data:
        return None
    if data[0] == 0x02 and len(data) >= 48:
        common, transport = data[1:], "usb"
    elif data[0] == 0x31 and len(data) >= 78:
        common, transport = data[2:], "bt"
    else:
        return {"report": data[0], "size": len(data)}
    out = {"transport": transport, "flags0": common[0], "flags1": common[1]}
    if transport == "bt":
        out["crc_ok"] = data[74:78] == _crc(0xA2, data[:74])
    if common[0] & 0x03:
        out["motors"] = [common[3], common[2]]  # left (low frequency), right
    if common[0] & 0x04:
        out["right"] = decode_trigger(common[10:21])
    if common[0] & 0x08:
        out["left"] = decode_trigger(common[21:32])
    if common[1] & 0x04:
        out["lightbar"] = list(common[44:47])
    return out


class VirtualDualSense:
    """One virtual pad. start() creates it, close() removes it. Thread safe."""

    def __init__(self, bus="usb", edge=False, log=None, name=None, identity="test"):
        self.bus = BUS_BLUETOOTH if bus == "bt" else BUS_USB
        self.identity = identity
        if identity == "sony":
            self.vendor, self.product = SONY, DUALSENSE_EDGE if edge else DUALSENSE
        else:
            self.vendor, self.product = TEST_VID, TEST_PID
        self.uniq = "fh1-virtual-%d-%d" % (os.getpid(), id(self) & 0xFFFF)
        self.name = name or ("FH1 virtual DualSense (test ID)" if identity != "sony" else
                             "DualSense Edge Wireless Controller" if edge else
                             "DualSense Wireless Controller")
        self.log = log  # a file, or a callable(record)
        self.reports = []  # (monotonic time, decoded) of every output report
        self.lock = threading.Lock()
        self.triggers = [0, 0]  # L2, R2 positions 0-255
        self.opened = 0
        self._fd = -1
        self._running = False
        self._seq = 0

    # -- device lifetime --
    def start(self):
        self._fd = os.open("/dev/uhid", os.O_RDWR)
        if self.identity != "sony":
            descriptor = vendor_descriptor(self.bus)
        else:
            descriptor = BT_DESCRIPTOR if self.bus == BUS_BLUETOOTH else USB_DESCRIPTOR
        mac = "02:00:00:%02x:%02x:%02x" % (os.getpid() & 0xFF, id(self) >> 8 & 0xFF, id(self) & 0xFF)
        self.mac = bytes(int(x, 16) for x in mac.split(":"))
        req = struct.pack("<128s64s64sHHIIII", self.name.encode(), b"fh1-virtual-dualsense",
                          self.uniq.encode(), len(descriptor), self.bus, self.vendor, self.product,
                          0x0100, 0) + descriptor
        self._write(UHID_CREATE2, req)
        self._running = True
        threading.Thread(target=self._events, name="vds-events", daemon=True).start()
        threading.Thread(target=self._inputs, name="vds-inputs", daemon=True).start()
        return self

    def close(self):
        self._running = False
        if self._fd >= 0:
            try:
                self._write(UHID_DESTROY, b"")
            finally:
                os.close(self._fd)
                self._fd = -1

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    def hidraw_path(self, timeout=5.0):
        """The /dev/hidrawN of this virtual pad, and only of it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
                device = os.path.realpath(os.path.join(node, "device"))
                try:
                    uevent = open(os.path.join(device, "uevent")).read()
                except OSError:
                    continue
                # hid-playstation replaces HID_UNIQ with the MAC of feature report 0x09.
                mac = ":".join("%02x" % b for b in self.mac)
                if "/virtual/misc/uhid/" in device and any(
                        "HID_UNIQ=%s\n" % u in uevent for u in (self.uniq, mac)):
                    path = "/dev/" + os.path.basename(node)
                    if os.access(path, os.R_OK | os.W_OK):
                        return path
            time.sleep(0.05)
        raise RuntimeError("the virtual DualSense got no usable hidraw node")

    # -- input --
    def set_triggers(self, left, right):
        with self.lock:
            self.triggers = [max(0, min(255, int(left))), max(0, min(255, int(right)))]

    def _input_report(self):
        with self.lock:
            l2, r2 = self.triggers
        self._seq = (self._seq + 1) & 0xFF
        state = bytearray(72)  # dualsense_input_report, padded to the Bluetooth size
        state[0:4] = b"\x80\x80\x80\x80"   # sticks centred
        state[4], state[5], state[6] = l2, r2, self._seq
        state[7] = 0x08                    # d-pad neutral, no buttons
        if l2: state[8] |= 0x04            # L2 and R2 digital bits follow the analog value
        if r2: state[8] |= 0x08
        if self.bus == BUS_BLUETOOTH:
            data = b"\x31" + bytes([self._seq << 4 & 0xF0]) + bytes(state)
            return data + _crc(0xA1, data)
        return b"\x01" + bytes(state[:63])

    def _inputs(self):
        while self._running:
            if self.opened:
                try:
                    data = self._input_report()
                    self._write(UHID_INPUT2, struct.pack("<H", len(data)) + data)
                except OSError:
                    pass
            time.sleep(0.008)  # 125 Hz

    # -- events --
    def _write(self, kind, payload):
        os.write(self._fd, struct.pack("<I", kind) + payload.ljust(EVENT_SIZE - 4, b"\0"))

    def _feature(self, report):
        size = {0x05: 41, 0x08: 48, 0x09: 20, 0x20: 64}.get(report, 64)
        data = bytearray(size)
        data[0] = report
        if report == 0x09:
            data[1:7] = self.mac[::-1]  # pairing info: the pad's MAC, little endian
        if self.bus == BUS_BLUETOOTH:
            data[-4:] = _crc(0xA3, data[:-4])
        return bytes(data)

    def _events(self):
        while self._running:
            try:
                ev = os.read(self._fd, EVENT_SIZE)
            except OSError:
                return
            if len(ev) < 4:
                continue
            kind = struct.unpack_from("<I", ev)[0]
            body = ev[4:]
            if kind == UHID_OPEN:
                self.opened += 1
            elif kind == UHID_CLOSE:
                self.opened = max(0, self.opened - 1)
            elif kind == UHID_OUTPUT:
                size = struct.unpack_from("<H", body, 4096)[0]
                self._record(body[:size])
            elif kind == UHID_GET_REPORT:
                rid, rnum, rtype = struct.unpack_from("<IBB", body)
                data = self._feature(rnum)
                self._write(UHID_GET_REPORT_REPLY, struct.pack("<IHH", rid, 0, len(data)) + data)
            elif kind == UHID_SET_REPORT:
                rid = struct.unpack_from("<I", body)[0]
                self._write(UHID_SET_REPORT_REPLY, struct.pack("<IH", rid, 0))

    def _record(self, data):
        now = time.monotonic()
        decoded = decode_output(data)
        with self.lock:
            self.reports.append((now, decoded))
        if self.log is not None:
            record = {"t": round(now, 4), "pad": self.uniq, "out": decoded}
            if callable(self.log):
                self.log(record)
            else:
                self.log.write(json.dumps(record) + "\n")
                self.log.flush()

    def trigger_reports(self):
        """(time, left, right) of the reports that set a trigger effect."""
        with self.lock:
            return [(t, r.get("left"), r.get("right")) for t, r in self.reports
                    if r and ("left" in r or "right" in r)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bus", choices=("usb", "bt"), default="bt")
    ap.add_argument("--edge", action="store_true", help="DualSense Edge PID (0DF2), with sony")
    ap.add_argument("--identity", choices=("test", "sony"), default="test",
                    help="sony: Sony's VID/PID (Steam Input and remappers take it too)")
    ap.add_argument("--log", help="JSON lines of the decoded output reports (default stdout)")
    ap.add_argument("--seconds", type=float, default=0, help="remove the pad after N s (0: Ctrl-C)")
    args = ap.parse_args()
    log = open(args.log, "a") if args.log else sys.stdout
    with VirtualDualSense(args.bus, args.edge, log, identity=args.identity) as pad:
        print("virtual DualSense at %s (%s)" % (pad.hidraw_path(), args.bus), file=sys.stderr)
        try:
            end = time.monotonic() + args.seconds
            while not args.seconds or time.monotonic() < end:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
