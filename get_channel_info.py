#!/usr/bin/env python3
"""Query channels on MeshCore device."""
import serial
import time

PORT = "/dev/ttyUSB0"
BAUD = 115200
CMD_GET_CHANNEL = 0x1F
FRAME_RECV = 0x3E
PACKET_CHANNEL_INFO = 18

def send_frame(ser, data: bytes):
    frame = bytes([0x3c]) + len(data).to_bytes(2, "little") + data
    ser.write(frame)
    ser.flush()

def read_frame(ser, timeout=5):
    start = time.time()
    buf = b""
    while time.time() - start < timeout:
        if ser.in_waiting > 0:
            buf += ser.read(ser.in_waiting)
        idx = buf.find(b"\x3e")
        if idx >= 0 and len(buf) >= idx + 3:
            size = int.from_bytes(buf[idx + 1 : idx + 3], "little")
            if size > 500:
                buf = buf[idx+1:]
                continue
            while len(buf) < idx + 3 + size:
                if ser.in_waiting > 0:
                    buf += ser.read(ser.in_waiting)
                time.sleep(0.05)
            payload = buf[idx + 3 : idx + 3 + size]
            return payload
        time.sleep(0.1)
    return None

def main():
    print("Connecting to", PORT, "...")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
    except Exception as e:
        print("Error:", e)
        return
    ser.reset_input_buffer()
    time.sleep(0.3)

    for ch_idx in range(4):
        cmd = bytes([CMD_GET_CHANNEL, ch_idx])
        send_frame(ser, cmd)
        payload = read_frame(ser)
        time.sleep(0.5)
        if payload is None:
            print(f"  Channel {ch_idx}: (timeout)")
            continue
        pkt_type = payload[0] if len(payload) > 0 else 0xFF
        if pkt_type == 1:  # ERROR
            print(f"  Channel {ch_idx}: (error)")
            continue
        if pkt_type != PACKET_CHANNEL_INFO or len(payload) < 50:
            print(f"  Channel {ch_idx}: (unknown response, type={pkt_type})")
            continue
        idx = payload[1]
        name_bytes = payload[2:34]
        null_pos = name_bytes.find(0)
        name = name_bytes[:null_pos if null_pos >= 0 else 32].decode("utf-8", "ignore").strip()
        secret = payload[34:50]
        if name or secret != b"\x00" * 16:
            print(f"  Channel {ch_idx}: \"{name}\"  secret={secret.hex()}")
        else:
            print(f"  Channel {ch_idx}: (empty)")

    ser.close()
    print("\nDone.")

if __name__ == "__main__":
    main()
