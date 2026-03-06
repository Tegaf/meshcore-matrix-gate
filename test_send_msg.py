#!/usr/bin/env python3
"""Test: send message to Heltec (0x03 send_chan_msg)"""
import serial
import time

PORT = "/dev/ttyUSB0"
BAUD = 115200
CMD = 0x03  # send channel msg

def send_frame(ser, data: bytes):
    frame = bytes([0x3c]) + len(data).to_bytes(2, "little") + data
    ser.write(frame)
    ser.flush()
    print(f"TX: {len(data)} B")

def read_response(ser, timeout=8):
    start = time.time()
    buf = b""
    while time.time() - start < timeout:
        if ser.in_waiting > 0:
            buf += ser.read(ser.in_waiting)
        idx = buf.find(b"\x3e")
        if idx >= 0 and len(buf) >= idx + 3:
            size = int.from_bytes(buf[idx+1:idx+3], "little")
            if size > 500:
                buf = buf[idx+1:]
                continue
            while len(buf) < idx + 3 + size:
                if ser.in_waiting > 0:
                    buf += ser.read(ser.in_waiting)
                time.sleep(0.05)
            payload = buf[idx+3:idx+3+size]
            print(f"  RX payload: {payload.hex()} type={payload[0] if payload else '?'}")
            return payload[0] if payload else None  # packet type
        time.sleep(0.1)
    print("  (timeout - no response)")
    return None

def main():
    import sys
    msg = sys.argv[1] if len(sys.argv) > 1 else "test"
    print("Connecting...")
    ser = serial.Serial(PORT, BAUD, timeout=1)
    ser.reset_input_buffer()
    time.sleep(0.3)

    ts = int(time.time())
    data = bytes([CMD, 0x00, 0]) + ts.to_bytes(4, "little") + msg.encode("utf-8")
    print(f"Sending: {msg!r} ({data.hex()})")
    send_frame(ser, data)
    # Raw dump - whatever arrives
    start = time.time()
    all_rx = b""
    while time.time() - start < 5:
        if ser.in_waiting:
            all_rx += ser.read(ser.in_waiting)
        time.sleep(0.05)
    print(f"Received {len(all_rx)} B: {all_rx.hex() if all_rx else '(none)'}")
    if all_rx and all_rx.find(b"\x3e") >= 0:
        idx = all_rx.find(b"\x3e")
        if len(all_rx) >= idx + 3:
            sz = int.from_bytes(all_rx[idx+1:idx+3], "little")
            if sz < 100:
                payload = all_rx[idx+3:idx+3+sz]
                print(f"Frame type={payload[0]} (0=OK 1=ERR 6=MSG_SENT)")
    ser.close()

if __name__ == "__main__":
    main()
