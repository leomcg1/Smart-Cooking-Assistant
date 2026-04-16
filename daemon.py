#!/usr/bin/env python3
import os
import re
import csv
import time
import json
import threading
from datetime import datetime, timezone
from collections import deque

import serial
from fastapi import FastAPI
from pydantic import BaseModel

from hx711_lgpio import HX711

# -----------------------------
# Config
# -----------------------------
SERIAL_PORT = os.environ.get("STM32_PORT", "/dev/ttyAMA0")
BAUD = int(os.environ.get("STM32_BAUD", "115200"))
UART_STALE_S = float(os.environ.get("UART_STALE_S", "1.5"))

HB_PERIOD_S = 0.5
HB_LINE = "HB"

HZ = 5.0
DT = 1.0 / HZ
WINDOW_S = 120
MAX_POINTS = int(WINDOW_S * HZ)

LOG_DIR = os.environ.get("LOG_DIR", os.path.expanduser("~/logs"))
os.makedirs(LOG_DIR, exist_ok=True)

CAL_PATH = os.environ.get("HX711_CAL", os.path.expanduser("~/hx711_cal.json"))

CV_JSON_PATH = os.environ.get(
    "CV_JSON_PATH",
    os.path.expanduser("~/smartcook_cv/pi_deploy/latest_cv.json")
)

CV_FRAME_PATH = os.environ.get(
    "CV_FRAME_PATH",
    os.path.expanduser("~/smartcook_cv/pi_deploy/latest_frame.jpg")
)

FOOD_STABLE_S = float(os.environ.get("COOK_FOOD_STABLE_S", "2.0"))
STAGE_STABLE_S = float(os.environ.get("COOK_STAGE_STABLE_S", "2.0"))
SAME_FOOD_RESUME_S = float(os.environ.get("COOK_RESUME_S", "10.0"))

# -----------------------------
# Parse STM32 line
# -----------------------------
LINE_RE = re.compile(
    r"^(?P<ts>\S+)\s+\|\s+seq=(?P<seq>\d+)\s+ms=(?P<ms>\d+)\s+"
    r"T1=(?P<t1>[-+]?\d+(\.\d+)?)\(\d+\)\s+T2=(?P<t2>[-+]?\d+(\.\d+)?)\(\d+\)\s+"
    r"flags=(?P<flags>0x[0-9A-Fa-f]+)\s+heater=(?P<heater>[01])\s*$"
)

SCA_RE = re.compile(
    r"^SCA,(?P<seq>\d+),(?P<ms>\d+),"
    r"(?P<t1>[-+]?\d+(\.\d+)?),(?P<t2>[-+]?\d+(\.\d+)?),"
    r"(?P<rest>.*?),(?P<flags>0x[0-9A-Fa-f]+),(?P<heater>[01])\*[0-9A-Fa-f]+$"
)

FAULT_BITS = {
    0: "T1 invalid",
    1: "T2 invalid",
    2: "Sensor disagree",
    3: "Overtemp",
    4: "E-stop",
    5: "Comms lost",
    6: "Safety fault",
}

COOK_RULES = {
    "egg": {
        "display_name": "Egg",
        "recipe": "Suggestion: add chilli flakes and black pepper. Serving idea: egg on toast.",
        "stages": {
            "raw": {
                "headline": "Egg detected",
                "body": "Egg has just gone in. Let the white begin setting.",
                "warning_after_s": 45,
                "warning": "Egg has stayed raw for a while. Check the heat or reposition the pan.",
                "status": "watch",
            },
            "cooking": {
                "headline": "Egg cooking",
                "body": "White is setting. Keep monitoring texture and edge colour.",
                "warning_after_s": 90,
                "warning": "Egg has been cooking for a long time. Consider reducing heat or serving soon.",
                "status": "cooking",
            },
            "done": {
                "headline": "Egg ready",
                "body": "Egg looks ready to serve.",
                "warning_after_s": 35,
                "warning": "Egg has stayed at done for a while. Remove from the pan to avoid overcooking.",
                "status": "ready",
            },
            "burnt": {
                "headline": "Egg overcooked",
                "body": "Egg appears burnt.",
                "warning_after_s": 0,
                "warning": "Remove from heat immediately.",
                "status": "danger",
            },
        },
    },
    "pancake": {
        "display_name": "Pancake",
        "recipe": "Suggestion: add berries or maple syrup. Serving idea: stack and serve warm.",
        "stages": {
            "batter": {
                "headline": "Pancake batter detected",
                "body": "Pour complete. Wait for the surface to settle and bubbles to appear.",
                "warning_after_s": 35,
                "warning": "Batter has sat for a while. Check heat and watch for bubbles.",
                "status": "watch",
            },
            "raw": {
                "headline": "Pancake setting",
                "body": "Mixture is still raw. Let the underside set before flipping.",
                "warning_after_s": 55,
                "warning": "Pancake is staying raw too long. Increase heat slightly or wait for stronger setting.",
                "status": "watch",
            },
            "cooking": {
                "headline": "Pancake cooking",
                "body": "Surface is setting. Prepare to flip when bubbles and edges look firm.",
                "warning_after_s": 40,
                "warning": "Pancake has been in the cooking stage for a while. Flip soon or lower the heat.",
                "status": "cooking",
            },
            "done": {
                "headline": "Pancake ready",
                "body": "Pancake appears ready.",
                "warning_after_s": 25,
                "warning": "Pancake has been ready for a while. Remove now to avoid burning.",
                "status": "ready",
            },
            "burnt": {
                "headline": "Pancake overcooked",
                "body": "Heat may be too high.",
                "warning_after_s": 0,
                "warning": "Remove from heat immediately.",
                "status": "danger",
            },
        },
    },



    "steak": {
        "display": "Steak",
        "recipe": "Finish with butter and pepper. Rest before slicing.",
        "stages": {
            "raw": {
                "headline": "Steak is still raw",
                "body": "Continue cooking and monitor the surface.",
                "warning_after_s": 60,
                "warning": "Steak has remained raw for a while. Check heat and pan contact.",
                "status": "watch",
            },
            "medium": {
                "headline": "Steak has reached medium",
                "body": "Remove soon if this is the target doneness.",
                "warning_after_s": 45,
                "warning": "Steak has been at medium for a while. Remove soon to avoid overcooking.",
                "status": "ready",
            },
            "medium well": {
                "headline": "Steak is medium well",
                "body": "Remove now unless further cooking is intended.",
                "warning_after_s": 30,
                "warning": "Steak has been at medium well for a while. Remove now to avoid overcooking.",
                "status": "watch",
            },
            "well done": {
                "headline": "Steak is well done",
                "body": "Remove immediately to avoid drying it out.",
                "warning_after_s": 20,
                "warning": "Steak has been at well done for a while. Remove immediately.",
                "status": "danger",
            },
        },
    },
}


def heartbeat_thread():
    while True:
        try:
            uart_cmd.send(HB_LINE)
        except Exception:
            pass
        time.sleep(HB_PERIOD_S)


def decode_faults(flags_hex: str):
    try:
        v = int(flags_hex, 16)
    except Exception:
        return []
    return [name for bit, name in FAULT_BITS.items() if (v >> bit) & 1]


def normalise_stage_label(stage):
    if stage is None:
        return None
    s = str(stage).strip().lower().replace("_", " ")
    s = " ".join(s.split())
    return s


def read_cv_state():
    try:
        if not os.path.exists(CV_JSON_PATH):
            return {}
        with open(CV_JSON_PATH, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def norm_label(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    return None if s in {"", "none", "unknown", "null", "n/a"} else s


def fmt_mmss(seconds):
    try:
        seconds = max(0, int(round(float(seconds))))
    except Exception:
        seconds = 0
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


# -----------------------------
# HX711: load calibration + init
# -----------------------------
with open(CAL_PATH, "r") as f:
    CAL = json.load(f)

DOUT = int(CAL["gpio"]["dout"])
SCK = int(CAL["gpio"]["sck"])
OFFSET = float(CAL["offset_counts"])
A = float(CAL["fit"]["a_grams_per_count"])
B = float(CAL["fit"].get("b_grams", 0.0))

hx = HX711(dout=DOUT, sck=SCK, gain_pulses=1, chip=0)

tare_lock = threading.Lock()
tare_grams = 0.0


def read_mass_grams():
    global tare_grams
    raw = hx.read_mean(n=8)
    grams = (raw - OFFSET) * A + B
    with tare_lock:
        grams -= tare_grams
    return float(grams)


def do_tare():
    global tare_grams
    with state_lock:
        g = live.get("mass_g")
    if g is None:
        raise RuntimeError("No mass reading available yet")
    with tare_lock:
        tare_grams += float(g)
    return float(g)


# -----------------------------
# Shared state
# -----------------------------
state_lock = threading.Lock()

live = {
    "ts_iso": None,
    "seq": None,
    "ms": None,
    "t1": None,
    "t2": None,
    "t_avg": None,
    "mass_g": None,
    "flags_hex": "0x00000000",
    "faults": [],
    "heater": None,
    "logging": False,
    "last_uart_ok": False,
    "last_uart_rx_monotonic": None,
    "uart_err": None,
    "uart_lines": 0,
    "serial_port": SERIAL_PORT,
    "food_type": None,
    "food_confidence": None,
    "stage": None,
    "stage_confidence": None,
    "pan_present": None,
    "frame_path": CV_FRAME_PATH,
    "cooking": {},
}

history = deque(maxlen=MAX_POINTS)

cooking_state = {
    "session_id": 0,
    "manual_paused": False,
    "active_food": None,
    "active_stage": None,
    "food_candidate": None,
    "food_candidate_since": None,
    "stage_candidate": None,
    "stage_candidate_since": None,
    "food_present": False,
    "absence_started": None,
    "total_elapsed_s": 0.0,
    "stage_elapsed_s": 0.0,
    "food_started_at": None,
    "stage_started_at": None,
    "last_tick": None,
    "warning_active": False,
    "warning_acknowledged": False,
    "warning_message": None,
    "warning_code": None,
    "assistant_status": "idle",
}


def reset_cooking_session(now=None, increment_session=True):
    now = time.monotonic() if now is None else now
    if increment_session:
        cooking_state["session_id"] += 1
    cooking_state.update({
        "active_food": None,
        "active_stage": None,
        "food_candidate": None,
        "food_candidate_since": None,
        "stage_candidate": None,
        "stage_candidate_since": None,
        "food_present": False,
        "absence_started": None,
        "total_elapsed_s": 0.0,
        "stage_elapsed_s": 0.0,
        "food_started_at": None,
        "stage_started_at": None,
        "last_tick": now,
        "warning_active": False,
        "warning_acknowledged": False,
        "warning_message": None,
        "warning_code": None,
        "assistant_status": "idle",
    })


def get_rule(food, stage):
    food_rules = COOK_RULES.get(food) or {}
    return (food_rules.get("stages") or {}).get(normalise_stage_label(stage))


def build_cooking_payload(now=None):
    now = time.monotonic() if now is None else now
    food = cooking_state.get("active_food")
    stage = cooking_state.get("active_stage")
    rule = get_rule(food, stage) if food and stage else None

    is_paused_auto = bool(cooking_state.get("food_present") is False and cooking_state.get("active_food") is not None)
    is_paused = bool(cooking_state.get("manual_paused") or is_paused_auto)

    if food is None:
        headline = "Cooking assistant idle"
        body = "Waiting for food recognition to begin timing."
        status = "idle"
        recipe = "Suggestion will appear once food is recognised."
    elif stage is None:
        headline = f"{food.title()} detected"
        body = "Food recognised. Waiting for a stable cooking stage."
        status = "watch"
        recipe = COOK_RULES.get(food, {}).get("recipe", "No serving suggestion available.")
    else:
        headline = rule.get("headline", f"{food.title()} detected") if rule else f"{food.title()} detected"
        body = rule.get("body", "Monitoring cooking state.") if rule else "Monitoring cooking state."
        status = rule.get("status", "watch") if rule else "watch"
        recipe = COOK_RULES.get(food, {}).get("recipe", "No serving suggestion available.")

    if cooking_state.get("manual_paused"):
        body = "Cooking timer paused manually. Resume when you want timing to continue."
        status = "paused"
    elif is_paused_auto and cooking_state.get("absence_started") is not None:
        missing_for = now - cooking_state["absence_started"]
        remain = max(0.0, SAME_FOOD_RESUME_S - missing_for)
        body = f"Food temporarily lost from view. Timer paused and will resume if it returns within {fmt_mmss(remain)}."
        status = "paused"

    warning_active = bool(cooking_state.get("warning_active"))
    warning_message = cooking_state.get("warning_message")
    warning_ack = bool(cooking_state.get("warning_acknowledged"))

    return {
        "session_id": cooking_state.get("session_id", 0),
        "food_type": food,
        "stage": stage,
        "food_present": bool(cooking_state.get("food_present")),
        "is_active": food is not None,
        "is_paused": is_paused,
        "manual_paused": bool(cooking_state.get("manual_paused")),
        "total_elapsed_s": round(float(cooking_state.get("total_elapsed_s", 0.0)), 1),
        "stage_elapsed_s": round(float(cooking_state.get("stage_elapsed_s", 0.0)), 1),
        "total_elapsed_text": fmt_mmss(cooking_state.get("total_elapsed_s", 0.0)),
        "stage_elapsed_text": fmt_mmss(cooking_state.get("stage_elapsed_s", 0.0)),
        "warning_active": warning_active,
        "warning_acknowledged": warning_ack,
        "warning_message": warning_message,
        "warning_code": cooking_state.get("warning_code"),
        "assistant_status": "danger" if warning_active else status,
        "headline": headline,
        "body": body,
        "recipe": recipe,
    }


def update_cooking_state(cv, now=None):
    now = time.monotonic() if now is None else now
    if cooking_state.get("last_tick") is None:
        cooking_state["last_tick"] = now
    dt = max(0.0, min(1.0, now - cooking_state["last_tick"]))
    cooking_state["last_tick"] = now

    cv_food = norm_label(cv.get("food_type"))
    cv_stage = normalise_stage_label(cv.get("stage"))
    pan_present = bool(cv.get("pan_present"))
    food_seen = pan_present and cv_food is not None

    cooking_state["food_present"] = food_seen

    if cooking_state.get("active_food") is not None:
        if food_seen and cv_food == cooking_state["active_food"]:
            cooking_state["absence_started"] = None
        elif cooking_state.get("absence_started") is None:
            cooking_state["absence_started"] = now
        elif (now - cooking_state["absence_started"]) > SAME_FOOD_RESUME_S:
            reset_cooking_session(now=now)

    if food_seen:
        if cv_food != cooking_state.get("active_food"):
            if cooking_state.get("food_candidate") != cv_food:
                cooking_state["food_candidate"] = cv_food
                cooking_state["food_candidate_since"] = now
            elif (now - (cooking_state.get("food_candidate_since") or now)) >= FOOD_STABLE_S:
                reset_cooking_session(now=now)
                cooking_state["active_food"] = cv_food
                cooking_state["food_started_at"] = now
                cooking_state["stage_candidate"] = None
                cooking_state["stage_candidate_since"] = None
                cooking_state["absence_started"] = None
        else:
            cooking_state["food_candidate"] = None
            cooking_state["food_candidate_since"] = None
    else:
        cooking_state["food_candidate"] = None
        cooking_state["food_candidate_since"] = None

    active_food = cooking_state.get("active_food")
    if active_food is not None and food_seen and cv_food == active_food and cv_stage is not None:
        if cv_stage != cooking_state.get("active_stage"):
            if cooking_state.get("stage_candidate") != cv_stage:
                cooking_state["stage_candidate"] = cv_stage
                cooking_state["stage_candidate_since"] = now
            elif (now - (cooking_state.get("stage_candidate_since") or now)) >= STAGE_STABLE_S:
                cooking_state["active_stage"] = cv_stage
                cooking_state["stage_started_at"] = now
                cooking_state["stage_elapsed_s"] = 0.0
                cooking_state["warning_active"] = False
                cooking_state["warning_acknowledged"] = False
                cooking_state["warning_message"] = None
                cooking_state["warning_code"] = None
        else:
            cooking_state["stage_candidate"] = None
            cooking_state["stage_candidate_since"] = None
    else:
        if not food_seen:
            cooking_state["stage_candidate"] = None
            cooking_state["stage_candidate_since"] = None

    paused = bool(cooking_state.get("manual_paused"))
    auto_paused = bool(active_food is not None and not food_seen)
    if active_food is not None and not paused and not auto_paused:
        cooking_state["total_elapsed_s"] += dt
        if cooking_state.get("active_stage") is not None:
            cooking_state["stage_elapsed_s"] += dt

    stage = cooking_state.get("active_stage")
    rule = get_rule(active_food, stage) if active_food and stage else None
    if rule is not None:
        warn_after = rule.get("warning_after_s")
        if warn_after is not None and cooking_state.get("stage_elapsed_s", 0.0) >= float(warn_after):
            cooking_state["warning_active"] = True
            cooking_state["warning_message"] = rule.get("warning")
            cooking_state["warning_code"] = f"{active_food}:{stage}:overdue"


def cooking_pause():
    cooking_state["manual_paused"] = True


def cooking_resume():
    cooking_state["manual_paused"] = False
    cooking_state["last_tick"] = time.monotonic()


def cooking_ack_warning():
    if cooking_state.get("warning_active"):
        cooking_state["warning_acknowledged"] = True


# -----------------------------
# UART command channel
# -----------------------------
class UartCommander:
    def __init__(self):
        self._lock = threading.Lock()
        self._ser = None

    def attach(self, ser):
        self._ser = ser

    def send(self, line: str):
        if not line.endswith("\n"):
            line += "\n"
        with self._lock:
            if self._ser is not None:
                self._ser.write(line.encode("utf-8", errors="ignore"))
                self._ser.flush()


uart_cmd = UartCommander()


def cmd_reset_faults():
    uart_cmd.send("CMD RESET_FAULTS")


def cmd_estop(enable: bool):
    uart_cmd.send(f"CMD ESTOP {1 if enable else 0}\r")


def cmd_ack():
    uart_cmd.send("CMD ACK")


# -----------------------------
# Logging
# -----------------------------
log_lock = threading.Lock()
log_fp = None
log_writer = None
log_path = None


def start_logging():
    global log_fp, log_writer, log_path
    with log_lock:
        if log_fp is not None:
            return log_path
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(LOG_DIR, f"cook_assistant_{stamp}.csv")
        log_fp = open(log_path, "w", newline="")
        log_writer = csv.writer(log_fp)
        log_writer.writerow(["iso_ts", "seq", "ms", "t_avg_C", "mass_g", "flags_hex", "heater"])
        log_fp.flush()
        return log_path


def stop_logging():
    global log_fp, log_writer, log_path
    with log_lock:
        if log_fp:
            log_fp.flush()
            log_fp.close()
        log_fp = None
        log_writer = None
        p = log_path
        log_path = None
        return p


def write_log_row(row):
    with log_lock:
        if log_writer is None:
            return
        log_writer.writerow(row)
        log_fp.flush()


# -----------------------------
# Worker threads
# -----------------------------
def uart_reader_thread():
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.2)
            uart_cmd.attach(ser)
            with state_lock:
                live["uart_err"] = None

            while True:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    with state_lock:
                        last_rx = live.get("last_uart_rx_monotonic")
                        if last_rx is not None and (time.monotonic() - last_rx) > UART_STALE_S:
                            live["last_uart_ok"] = False
                    continue

                m = LINE_RE.match(line)
                if m:
                    ts_iso = m.group("ts")
                    seq = int(m.group("seq"))
                    ms = int(m.group("ms"))
                    t1 = float(m.group("t1"))
                    t2 = float(m.group("t2"))
                    flags_hex = m.group("flags")
                    heater = int(m.group("heater"))
                else:
                    m2 = SCA_RE.match(line)
                    if not m2:
                        continue
                    ts_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                    seq = int(m2.group("seq"))
                    ms = int(m2.group("ms"))
                    t1 = float(m2.group("t1"))
                    t2 = float(m2.group("t2"))
                    flags_hex = m2.group("flags")
                    heater = int(m2.group("heater"))

                t_avg = (t1 + t2) / 2.0
                faults = decode_faults(flags_hex)

                with state_lock:
                    live.update({
                        "ts_iso": ts_iso,
                        "seq": seq,
                        "ms": ms,
                        "t1": t1,
                        "t2": t2,
                        "t_avg": t_avg,
                        "flags_hex": flags_hex,
                        "faults": faults,
                        "heater": heater,
                        "last_uart_ok": True,
                        "last_uart_rx_monotonic": time.monotonic(),
                        "uart_lines": live.get("uart_lines", 0) + 1,
                    })

        except Exception as e:
            with state_lock:
                live["uart_err"] = str(e)
                live["last_uart_ok"] = False
            time.sleep(1.0)


def sampler_thread():
    reset_cooking_session(now=time.monotonic(), increment_session=False)
    while True:
        t0 = time.time()
        mono_now = time.monotonic()

        try:
            mass = read_mass_grams()
        except Exception:
            mass = None

        cv = read_cv_state()
        update_cooking_state(cv, now=mono_now)
        cooking_payload = build_cooking_payload(now=mono_now)

        with state_lock:
            t_avg = live.get("t_avg")
            seq = live.get("seq")
            ms = live.get("ms")
            flags_hex = live.get("flags_hex", "0x00000000")
            heater = live.get("heater")
            logging_on = live.get("logging")

            live["mass_g"] = mass
            live["food_type"] = cv.get("food_type")
            live["food_confidence"] = cv.get("food_confidence")
            live["stage"] = cv.get("stage")
            live["stage_confidence"] = cv.get("stage_confidence")
            live["pan_present"] = cv.get("pan_present")
            live["frame_path"] = CV_FRAME_PATH
            live["cooking"] = cooking_payload

        history.append((t0, t_avg, mass))

        if logging_on:
            iso_now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            write_log_row([iso_now, seq, ms, t_avg, mass, flags_hex, heater])

        elapsed = time.time() - t0
        time.sleep(max(0.0, DT - elapsed))


# -----------------------------
# FastAPI
# -----------------------------
app = FastAPI()


class CommandReq(BaseModel):
    cmd: str
    value: str | None = None


@app.get("/state")
def get_state():
    with state_lock:
        return {**live, "history": list(history)}


@app.post("/command")
def post_command(req: CommandReq):
    cmd = req.cmd.upper().strip()

    if cmd == "TARE_MASS":
        try:
            g = do_tare()
            return {"ok": True, "msg": f"Tared at {g:.1f} g"}
        except Exception as e:
            return {"ok": False, "msg": f"Tare failed: {e}"}

    if cmd == "START_LOG":
        p = start_logging()
        with state_lock:
            live["logging"] = True
        return {"ok": True, "log_path": p}

    if cmd == "STOP_LOG":
        p = stop_logging()
        with state_lock:
            live["logging"] = False
        return {"ok": True, "log_path": p}

    if cmd == "RESET_FAULTS":
        cmd_reset_faults()
        return {"ok": True}

    if cmd == "ACK":
        cmd_ack()
        return {"ok": True}

    if cmd == "ESTOP":
        enable = (req.value == "1" or (req.value or "").lower() in ("true", "on", "enable"))
        cmd_estop(enable)
        return {"ok": True, "sent": True, "requested_estop": enable}

    if cmd == "COOKING_PAUSE":
        cooking_pause()
        return {"ok": True}

    if cmd == "COOKING_RESUME":
        cooking_resume()
        return {"ok": True}

    if cmd == "COOKING_RESET":
        reset_cooking_session(now=time.monotonic())
        return {"ok": True}

    if cmd == "ACK_COOKING_WARNING":
        cooking_ack_warning()
        return {"ok": True}

    return {"ok": False, "msg": f"Unknown cmd: {cmd}"}


def main():
    threading.Thread(target=uart_reader_thread, daemon=True).start()
    threading.Thread(target=sampler_thread, daemon=True).start()
    threading.Thread(target=heartbeat_thread, daemon=True).start()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)


if __name__ == "__main__":
    main()
