import csv
import sys
import time
from datetime import datetime
import serial

# Default based on what you found working
DEFAULT_PORT = "/dev/ttyAMA0"
BAUD = 115200
HB_PERIOD_S = 0.5

FAULT_BITS = {
    0: "T1_INVALID",
    1: "T2_INVALID",
    2: "SENSOR_DISAGREE",
    3: "OVERTEMP",
    4: "ESTOP",
    5: "COMMS_LOST",
    6: "SAFETY_FAULT",
}

def decode_faults(fault_hex: str):
    try:
        v = int(fault_hex, 16)
    except Exception:
        return ""
    names = [name for bit, name in FAULT_BITS.items() if (v >> bit) & 1]
    return "|".join(names)

def xor_checksum_ascii(s: str) -> int:
    x = 0
    for ch in s:
        x ^= ord(ch) & 0xFF
    return x

def open_serial(port: str) -> serial.Serial:
    return serial.Serial(
        port=port,
        baudrate=BAUD,
        timeout=1.0,          # readline timeout
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    ser = open_serial(port)

    fname = f"stm_telemetry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    print(f"[+] Logging from {port} @ {BAUD} baud")
    print(f"[+] Output CSV: {fname}")
    print("[+] Ctrl+C to stop\n")

    with open(fname, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "pi_time_iso",
            "seq",
            "stm_ms",
            "temp1_c",
            "temp2_c",
            "t1_valid",
            "t2_valid",
            "fault_flags_hex",
            "fault_summary",
            "heater_out",
            "checksum_ok",
            "raw_line"
        ])

        last_hb = 0.0

        try:
            while True:
                # Heartbeat to keep STM out of COMMS_LOST
                now = time.time()
                if now - last_hb >= HB_PERIOD_S:
                    ser.write(b"HB\n")
                    last_hb = now

                line = ser.readline().decode(errors="ignore").strip()
                if not line:
                    continue

                pi_time = datetime.now().isoformat(timespec="milliseconds")

                checksum_ok = 0
                seq = stm_ms = None
                t1 = t2 = None
                v1 = v2 = None
                fault_hex = None
                heater = None

                # Expected: payload*CS
                if "*" in line:
                    payload, cs_hex = line.rsplit("*", 1)
                    try:
                        got = int(cs_hex, 16) & 0xFF
                        calc = xor_checksum_ascii(payload) & 0xFF
                        checksum_ok = 1 if (got == calc) else 0
                    except ValueError:
                        checksum_ok = 0

                    # Parse payload fields
                    parts = payload.split(",")
                    # Expected:
                    # SCA,seq,ms,temp1,temp2,valid1,valid2,0xXXXXXXXX,heater_out
                    if len(parts) >= 9 and parts[0] == "SCA":
                        try:
                            seq = int(parts[1])
                            stm_ms = int(parts[2])
                            t1 = float(parts[3])
                            t2 = float(parts[4])
                            v1 = int(parts[5])
                            v2 = int(parts[6])
                            fault_hex = parts[7]
                            heater = int(parts[8])
                            fault_summary = decode_faults(fault_hex) if fault_hex else ""
                        except ValueError:
                            pass

                w.writerow([
                    pi_time,
                    seq,
                    stm_ms,
                    t1,
                    t2,
                    v1,
                    v2,
                    fault_hex,
                    fault_summary,
                    heater,
                    checksum_ok,
                    line
                ])
                f.flush()

                # Optional: print live (only when checksum is OK)
                if checksum_ok and seq is not None and t1 is not None and t2 is not None:
                    print(f"{pi_time} | seq={seq} ms={stm_ms} T1={t1:.2f}({v1}) T2={t2:.2f}({v2}) flags={fault_hex} heater={heater}")

        except KeyboardInterrupt:
            print("\n[+] Stopped.")

if __name__ == "__main__":
    main()
