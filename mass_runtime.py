#!/usr/bin/env python3
import time, json, csv, sys, select, tty, termios
from collections import deque
import RPi.GPIO as GPIO

CAL_PATH = "/home/leomcgriskin/hx711_cal.json"
LOG_PATH = "/home/leomcgriskin/mass_log.csv"

# ---- GPIO (BCM numbering) ----
DOUT = 5  # Pi GPIO5, physical pin 29 -> HX711 DT
SCK  = 6  # Pi GPIO6, physical pin 31 -> HX711 SCK

# ---- sampling / loop ----
READ_AVG_N = 10           # raw samples per displayed reading
LOOP_PERIOD_S = 0.20      # update rate (seconds)

# ---- stability detection ----
STABLE_WINDOW_S = 2.0     # how long to consider for stability
STABLE_SPAN_G = 2.5       # stable if max-min in window <= this (grams)

# ---- display-only "auto zero" (does NOT change tare) ----
DISPLAY_ZERO_ENABLED = True
DISPLAY_ZERO_BAND_G = 8.0      # if stable and within +/- this band -> display 0.00g
LOAD_LOCKOUT_THRESHOLD_G = 30.0  # if |g| exceeds this, disable display-zero until back near zero & stable

GPIO.setmode(GPIO.BCM)
GPIO.setup(SCK, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(DOUT, GPIO.IN)

def wait_ready(timeout=2.0):
    t0 = time.time()
    while GPIO.input(DOUT) == 1:
        if time.time() - t0 > timeout:
            return False
    return True

def read_raw_once():
    if not wait_ready(2.0):
        raise TimeoutError("HX711 not ready (DT stayed high). Check power/wiring.")
    data = 0
    for _ in range(24):
        GPIO.output(SCK, 1)
        data = (data << 1) | GPIO.input(DOUT)
        GPIO.output(SCK, 0)

    # 25th pulse: Channel A, Gain 128
    GPIO.output(SCK, 1)
    GPIO.output(SCK, 0)

    # sign extend 24-bit two's complement
    if data & 0x800000:
        data -= 1 << 24
    return data

def read_avg(n=READ_AVG_N):
    s = 0
    for _ in range(n):
        s += read_raw_once()
    return s / n

def load_cal(path):
    with open(path, "r") as f:
        cal = json.load(f)
    offset = float(cal["offset_counts"])
    a = float(cal["fit"]["a_grams_per_count"])
    b = float(cal["fit"]["b_grams"])
    if a == 0:
        raise ValueError("Calibration has a=0. Something is wrong with hx711_cal.json")
    return offset, a, b

def grams_from_raw(raw, offset, a, b, tare_extra_counts):
    # grams = a*(raw - offset - tare_extra) + b
    net = raw - offset - tare_extra_counts
    return a * net + b

def desired_tare_extra(raw_now, offset, a, b):
    # Choose tare_extra so that current reading becomes exactly 0 g:
    # 0 = a*(raw - offset - tare_extra) + b  => tare_extra = raw - offset + (b/a)
    return (raw_now - offset) + (b / a)

# --- non-blocking single-key input ---
def setup_keyboard():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    return fd, old

def get_key():
    if select.select([sys.stdin], [], [], 0.0)[0]:
        return sys.stdin.read(1)
    return None

def main():
    offset, a, b = load_cal(CAL_PATH)
    print(f"Loaded calibration from {CAL_PATH}")
    print("Controls:  t = tare now,  q = quit")
    print(f"Logging to {LOG_PATH}")
    if DISPLAY_ZERO_ENABLED:
        print(f"Display-zero: ON (stable span ≤{STABLE_SPAN_G} g for {STABLE_WINDOW_S}s, snap band ±{DISPLAY_ZERO_BAND_G} g, lockout >{LOAD_LOCKOUT_THRESHOLD_G} g)")
    else:
        print("Display-zero: OFF")
    print("\nTip: press 't' once when your plate/pan is in the desired zero state.\n")

    # runtime tare (counts), separate from stored calibration
    tare_extra = 0.0

    # display-zero lockout state
    load_lockout = False

    # stability tracking
    window_len = max(3, int(STABLE_WINDOW_S / LOOP_PERIOD_S))
    recent_g = deque(maxlen=window_len)

    # CSV log
    new_file = False
    try:
        with open(LOG_PATH, "r"):
            pass
    except FileNotFoundError:
        new_file = True

    logf = open(LOG_PATH, "a", newline="")
    writer = csv.writer(logf)
    if new_file:
        writer.writerow([
            "timestamp",
            "raw_counts",
            "grams_raw",
            "grams_display",
            "stable",
            "tare_extra_counts",
            "load_lockout"
        ])

    fd, old = setup_keyboard()

    try:
        while True:
            k = get_key()
            if k == "q":
                print("\nQuit.")
                break
            if k == "t":
                raw_now = read_avg(20)
                tare_extra = desired_tare_extra(raw_now, offset, a, b)
                load_lockout = False
                print("\nTared (runtime).")

            raw = read_avg(READ_AVG_N)
            g_raw = grams_from_raw(raw, offset, a, b, tare_extra)

            # stability
            recent_g.append(g_raw)
            stable = False
            if len(recent_g) == recent_g.maxlen:
                span = max(recent_g) - min(recent_g)
                stable = (span <= STABLE_SPAN_G)

            # lockout display-zero when a real load is present
            if abs(g_raw) > LOAD_LOCKOUT_THRESHOLD_G:
                load_lockout = True
            # unlock when back near zero AND stable
            if stable and abs(g_raw) <= DISPLAY_ZERO_BAND_G:
                load_lockout = False

            # display-only zero snap (does NOT alter tare)
            if DISPLAY_ZERO_ENABLED and (not load_lockout) and stable and abs(g_raw) <= DISPLAY_ZERO_BAND_G:
                g_disp = 0.0
            else:
                g_disp = g_raw

            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([ts, f"{raw:.2f}", f"{g_raw:.2f}", f"{g_disp:.2f}", int(stable), f"{tare_extra:.2f}", int(load_lockout)])
            logf.flush()

            flag = "STABLE" if stable else "     "
            print(f"\r{g_disp:9.2f} g  {flag}  (raw {raw:10.2f})", end="", flush=True)
            time.sleep(LOOP_PERIOD_S)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        logf.close()

if __name__ == "__main__":
    try:
        main()
    finally:
        GPIO.cleanup()
