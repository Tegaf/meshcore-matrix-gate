#!/usr/bin/env python3
"""Raw serial dump - what Heltec returns on get_msg (0x0a)"""
import serial
import time

PORT = "/dev/ttyUSB0"
BAUD = 115200

def send_frame(ser, data: bytes):
    frame = bytes([0x3c]) + len(data).to_bytes(2, "little") + data
    ser.write(frame)
    ser.flush()
    print(f"TX: {frame.hex()}")

def main():
    print("Connecting...")
    ser = serial.Serial(PORT, BAUD, timeout=0.5)
    ser.reset_input_buffer()
    time.sleep(0.3)

    print("Sending get_msg (0x0a), waiting 8 s for response...")
    send_frame(ser, bytes([0x0a]))

    start = time.time()
    all_data = b""
    while time.time() - start < 8:
        if ser.in_waiting > 0:
            chunk = ser.read(ser.in_waiting)
            all_data += chunk
            print(f"  RX ({len(chunk)} B): {chunk.hex()}")
        time.sleep(0.1)

    print(f"\nTotal received: {len(all_data)} B")
    if all_data:
        # Look for 0x3e frames
        idx = 0
        while True:
            idx = all_data.find(b"\x3e", idx)
            if idx < 0:
                break
            if len(all_data) >= idx + 3:
                size = int.from_bytes(all_data[idx+1:idx+3], "little")
                print(f"  Frame 0x3e @ {idx}: size={size}")
                if len(all_data) >= idx + 3 + size:
                    payload = all_data[idx+3:idx+3+size]
                    print(f"    Payload: {payload.hex()}")
                    print(f"    Type={payload[0] if payload else '?'} (8=CHAN_MSG, 10=NO_MORE)")
            idx += 1

    ser.close()

if __name__ == "__main__":
    main()
