import requests
import pandas as pd
import streamlit as st
import plotly.express as px
from streamlit_autorefresh import st_autorefresh
from datetime import datetime
from pathlib import Path
import re
import html

API = "http://127.0.0.1:8787"

st.set_page_config(page_title="Smart Cooking Assistant", layout="wide")

if "pending_cmd" not in st.session_state:
    st.session_state.pending_cmd = None
if "pending_val" not in st.session_state:
    st.session_state.pending_val = None
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "event_log" not in st.session_state:
    st.session_state.event_log = []
if "prev_snapshot" not in st.session_state:
    st.session_state.prev_snapshot = {}
if "fault_latched_ui" not in st.session_state:
    st.session_state.fault_latched_ui = False


def queue_cmd(cmd: str, value: str | None = None):
    st.session_state.pending_cmd = cmd
    st.session_state.pending_val = value


def post_cmd(cmd: str, value: str | None = None):
    payload = {"cmd": cmd, "value": value}
    return requests.post(f"{API}/command", json=payload, timeout=2.0).json()


def get_state():
    return requests.get(f"{API}/state", timeout=1.0).json()


def flag_set(flags_hex: str | None, bit: int) -> bool:
    try:
        return ((int(flags_hex or "0", 16) >> bit) & 1) == 1
    except Exception:
        return False


def safe_float(v):
    try:
        return float(v)
    except Exception:
        return None


def title_case_or_dash(v):
    return "—" if not v else str(v).replace("_", " ").title()


def clean_ui_text(v, default="—"):
    if v is None:
        return default
    text = str(v)

    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded

    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if text and text.lower() not in {"none", "null"} else default


def now_hms():
    return datetime.now().strftime("%H:%M:%S")


def add_event(msg: str):
    entry = f"{now_hms()} — {msg}"
    log = st.session_state.event_log
    if not log or log[0] != entry:
        log.insert(0, entry)
    st.session_state.event_log = log[:60]


def file_mtime_str(path_str):
    try:
        p = Path(path_str)
        if not p.exists():
            return None
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return None


def get_food_type(s: dict):
    v = s.get("food_type")
    if v is None:
        return None
    v = str(v).strip().lower()
    return None if v in {"", "none", "unknown", "null", "n/a"} else v


def get_stage(s: dict):
    v = s.get("stage")
    if v is None:
        return None
    v = str(v).strip().lower()
    return None if v in {"", "none", "unknown", "null", "n/a"} else v


def get_food_confidence(s: dict):
    c = s.get("food_confidence")
    try:
        c = float(c)
        return c * 100.0 if c <= 1.0 else c
    except Exception:
        return None


def get_stage_confidence(s: dict):
    c = s.get("stage_confidence")
    try:
        c = float(c)
        return c * 100.0 if c <= 1.0 else c
    except Exception:
        return None


def get_pan_present(s: dict, mass_g, food_type):
    pan = s.get("pan_present")
    if isinstance(pan, bool):
        return pan
    if mass_g is not None and mass_g > 20:
        return True
    if food_type is not None:
        return True
    return False


def get_comms_ok(s: dict):
    if "comms_ok" in s:
        return bool(s["comms_ok"])
    return bool(s.get("last_uart_ok", False))


FAULT_BITS = {
    0: "T1 invalid",
    1: "T2 invalid",
    2: "Sensor disagreement",
    3: "Over-temperature",
    4: "E-STOP",
    5: "Comms lost",
    6: "Safety fault",
}


def decode_faults_from_flags(flags_hex: str | None):
    try:
        v = int(flags_hex or "0", 16)
    except Exception:
        return []
    return [name for bit, name in FAULT_BITS.items() if ((v >> bit) & 1) == 1]


def get_fault_list(s: dict):
    faults = s.get("faults") or []
    if isinstance(faults, list) and len(faults) > 0:
        return [str(f) for f in faults]
    return decode_faults_from_flags(s.get("flags_hex"))


def get_primary_fault_text(s: dict):
    faults = get_fault_list(s)
    if not faults:
        return "No active fault"
    return faults[0]


def get_assistant_border_class(cooking, fault_active):
    if fault_active:
        return "assistant-danger"
    status = (cooking or {}).get("assistant_status", "idle")
    return {
        "danger": "assistant-danger",
        "ready": "assistant-ready",
        "paused": "assistant-paused",
        "cooking": "assistant-cooking",
        "watch": "assistant-watch",
        "idle": "assistant-idle",
    }.get(status, "assistant-idle")


st.markdown("""
<style>
.block-container {
    padding-top: 1.0rem;
    padding-bottom: 1rem;
    max-width: 1500px;
}
.big-banner-ok {
    padding: 1rem 1.2rem;
    border-radius: 14px;
    background: #e8f5e9;
    border: 2px solid #2e7d32;
    color: #1b5e20;
    margin-bottom: 0.9rem;
}
.big-banner-fault {
    padding: 1.05rem 1.2rem;
    border-radius: 14px;
    background: #ffebee;
    border: 3px solid #c62828;
    color: #7f0000;
    margin-bottom: 0.9rem;
}
.card {
    border: 1px solid #ddd;
    border-radius: 16px;
    padding: 0.95rem 1rem;
    background: #fff;
    color: #111 !important;
}
.card * { color: #111 !important; }
.compact-card {
    border: 1px solid #ddd;
    border-radius: 14px;
    padding: 0.8rem 0.9rem;
    background: #fafafa;
    min-height: 110px;
}
.metric-strip {
    padding: 0.35rem 0.7rem;
    border-radius: 999px;
    font-weight: 600;
    display: inline-block;
    margin: 0.15rem 0.25rem 0.15rem 0;
    font-size: 0.92rem;
}
.metric-good { background: #e8f5e9; color: #1b5e20; }
.metric-bad { background: #ffebee; color: #7f0000; }
.metric-neutral { background: #eceff1; color: #263238; }
.metric-warn { background: #fff8e1; color: #8d6e00; }
.small-muted { color: #666; font-size: 0.9rem; }
.log-box {
    border: 1px solid #ddd;
    border-radius: 14px;
    padding: 0.8rem 1rem;
    background: #ffffff;
    color: #111111 !important;
    max-height: 360px;
    overflow-y: auto;
}
.assistant-card {
    border-radius: 18px;
    padding: 1rem 1rem;
    border: 2px solid #ddd;
    background: #fff;
    color: #111 !important;
}
.assistant-card * { color: #111 !important; }
.assistant-danger { border-color: #c62828; background: #fff5f5; }
.assistant-ready { border-color: #2e7d32; background: #f5fff7; }
.assistant-cooking { border-color: #1565c0; background: #f5faff; }
.assistant-watch { border-color: #ef6c00; background: #fffaf3; }
.assistant-paused { border-color: #6d4c41; background: #faf7f5; }
.assistant-idle { border-color: #90a4ae; background: #fafcfd; }
.warning-pill {
    display: inline-block;
    padding: 0.28rem 0.65rem;
    border-radius: 999px;
    font-weight: 700;
    margin-bottom: 0.55rem;
}
.warning-live { background: #ffebee; color: #b71c1c; }
.warning-ack { background: #eceff1; color: #37474f; }
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Display")
    live_update = st.checkbox("Live update", value=True)
    refresh_ms = st.slider("Refresh (ms)", min_value=300, max_value=2000, value=500, step=100)
    st.caption("Use slower refresh if clicking controls frequently.")

if live_update:
    st_autorefresh(interval=refresh_ms, key="refresh")

st.title("Smart Cooking Assistant — Live UI")

if st.session_state.pending_cmd is not None:
    try:
        r = post_cmd(st.session_state.pending_cmd, st.session_state.pending_val)
        st.session_state.last_result = (st.session_state.pending_cmd, r)
    except Exception as e:
        st.session_state.last_result = (st.session_state.pending_cmd, {"ok": False, "msg": str(e)})
    finally:
        st.session_state.pending_cmd = None
        st.session_state.pending_val = None

try:
    s = get_state()
except Exception as e:
    st.error(f"Daemon not reachable at {API}.\n\n{e}")
    st.stop()

faults = get_fault_list(s)
fault_active = len(faults) > 0
primary_fault = get_primary_fault_text(s)

t1 = safe_float(s.get("t1"))
t2 = safe_float(s.get("t2"))
t_avg = safe_float(s.get("t_avg"))
mass_g = safe_float(s.get("mass_g"))
ts = s.get("ts_iso")

food_type = get_food_type(s)
stage = get_stage(s)
food_conf = get_food_confidence(s)
stage_conf = get_stage_confidence(s)
pan_present = get_pan_present(s, mass_g, food_type)
comms_ok = get_comms_ok(s)

flags_hex = s.get("flags_hex")
estop_on = flag_set(flags_hex, 4)
heater_on = bool(s.get("heater", 0))
logging_on = bool(s.get("logging", False))
cooking = s.get("cooking") or {}

snapshot = {
    "faults": tuple(faults),
    "food_type": food_type,
    "stage": stage,
    "pan_present": pan_present,
    "heater_on": heater_on,
    "logging_on": logging_on,
    "estop_on": estop_on,
    "comms_ok": comms_ok,
    "cook_session": cooking.get("session_id"),
    "cook_stage": cooking.get("stage"),
    "cook_warning": cooking.get("warning_code"),
    "cook_warning_ack": cooking.get("warning_acknowledged"),
}

prev = st.session_state.prev_snapshot
if not prev:
    add_event("UI session started")
    if pan_present:
        add_event("Pan detected")
    if food_type:
        add_event(f"Food classified: {food_type}")
    if stage:
        add_event(f"Stage classified: {stage}")
    if cooking.get("is_active"):
        add_event("Cooking timer started")
else:
    if prev.get("pan_present") != pan_present:
        add_event("Pan detected" if pan_present else "Pan removed")
    if prev.get("food_type") != food_type:
        add_event("Food classification cleared" if food_type is None else f"Food classified: {food_type}")
    if prev.get("stage") != stage:
        add_event("Stage classification cleared" if stage is None else f"Stage classified: {stage}")
    if prev.get("faults") != tuple(faults):
        if fault_active:
            add_event(f"Fault injected / detected: {primary_fault}")
            add_event("RoP activated")
        else:
            add_event("Faults cleared")
    if prev.get("heater_on") != heater_on:
        add_event("Heating enabled" if heater_on else "Heating disabled")
    if prev.get("logging_on") != logging_on:
        add_event("Logging started" if logging_on else "Logging stopped")
    if prev.get("estop_on") != estop_on:
        add_event("E-STOP activated" if estop_on else "E-STOP released")
    prev_comms = prev.get("comms_ok")
    if prev_comms is not None and prev_comms != comms_ok:
        add_event("Comms restored" if comms_ok else "Comms lost")
    if prev.get("cook_session") != cooking.get("session_id") and cooking.get("is_active"):
        add_event("Cooking session reset")
    if prev.get("cook_stage") != cooking.get("stage") and cooking.get("stage") is not None:
        add_event(f"Cooking assistant stage: {cooking.get('stage')}")
    if prev.get("cook_warning") != cooking.get("warning_code") and cooking.get("warning_active"):
        add_event("Cooking warning raised")
    if prev.get("cook_warning_ack") != cooking.get("warning_acknowledged") and cooking.get("warning_acknowledged"):
        add_event("Cooking warning acknowledged")

st.session_state.prev_snapshot = snapshot

if st.session_state.last_result:
    cmd, r = st.session_state.last_result
    if r.get("ok"):
        st.success(f"{cmd}: {r}")
    else:
        st.error(f"{cmd} failed: {r.get('msg', r)}")

if fault_active:
    st.markdown(f"""
    <div class="big-banner-fault">
        <h2 style="margin:0;">FAULT: {primary_fault}</h2>
        <div style="font-size:1.02rem; margin-top:0.35rem;"><b>RoP ACTIVE — Heater disabled</b></div>
        <div style="margin-top:0.2rem;">System cooling / safe state</div>
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown("""
    <div class="big-banner-ok">
        <h2 style="margin:0;">System status: NORMAL</h2>
        <div style="font-size:1.0rem; margin-top:0.35rem;">Monitoring sensors and cooking state.</div>
    </div>
    """, unsafe_allow_html=True)

strip_parts = []
strip_parts.append(f"<span class='metric-strip {'metric-good' if pan_present else 'metric-neutral'}'>Pan: {'Present' if pan_present else 'Absent'}</span>")
strip_parts.append(f"<span class='metric-strip {'metric-good' if comms_ok else 'metric-bad'}'>Comms: {'OK' if comms_ok else 'Lost'}</span>")
strip_parts.append(f"<span class='metric-strip {'metric-bad' if fault_active else 'metric-good'}'>Status: {'FAULT' if fault_active else 'Normal'}</span>")
strip_parts.append(f"<span class='metric-strip {'metric-bad' if estop_on else 'metric-neutral'}'>E-STOP: {'Active' if estop_on else 'Inactive'}</span>")
strip_parts.append(f"<span class='metric-strip metric-neutral'>Heater: {'On' if heater_on else 'Off'}</span>")
if cooking.get("warning_active"):
    strip_parts.append(f"<span class='metric-strip {'metric-neutral' if cooking.get('warning_acknowledged') else 'metric-warn'}'>Cooking warning: {'Acknowledged' if cooking.get('warning_acknowledged') else 'Active'}</span>")

st.markdown("".join(strip_parts), unsafe_allow_html=True)

st.subheader("Live cooking area")
left, right = st.columns([1.35, 1.05])

with left:
    upper_metrics = st.columns(4)
    upper_metrics[0].metric("Food", title_case_or_dash(food_type))
    upper_metrics[1].metric("Stage", title_case_or_dash(stage))
    upper_metrics[2].metric("Temperature", f"{t_avg:.1f} °C" if t_avg is not None else "—")
    upper_metrics[3].metric("Mass", f"{mass_g:.1f} g" if mass_g is not None else "—")

    st.markdown("<div class='small-muted'>Live camera view</div>", unsafe_allow_html=True)
    frame_path = Path(s.get("frame_path") or "/home/leomcgriskin/smartcook_cv/pi_deploy/latest_frame.jpg")
    if frame_path.exists():
        try:
            image_bytes = frame_path.read_bytes()
            frame_mtime = frame_path.stat().st_mtime
            st.image(
                image_bytes,
                caption=f"Live camera view — updated {datetime.fromtimestamp(frame_mtime).strftime('%H:%M:%S.%f')[:-3]}",
                use_container_width=True,
            )
        except Exception as e:
            st.warning(f"Could not read frame: {e}")
    else:
        st.info(f"Waiting for camera frame... Expected: {frame_path}")

with right:
    metric_row1 = st.columns(2)
    metric_row2 = st.columns(2)
    metric_row1[0].metric("Food timer", cooking.get("total_elapsed_text", "00:00"))
    metric_row1[1].metric("Stage timer", cooking.get("stage_elapsed_text", "00:00"))
    metric_row2[0].metric("Food confidence", f"{food_conf:.1f}%" if food_conf is not None else "—")
    metric_row2[1].metric("Stage confidence", f"{stage_conf:.1f}%" if stage_conf is not None else "—")

    border_class = get_assistant_border_class(cooking, fault_active)
    warning_active = bool(cooking.get("warning_active"))
    warning_ack = bool(cooking.get("warning_acknowledged"))

    headline_text = clean_ui_text(cooking.get("headline"), "Cooking assistant idle")
    body_text = clean_ui_text(cooking.get("body"), "Waiting for food recognition.")
    recipe_text = clean_ui_text(cooking.get("recipe"), "No serving suggestion available.")
    warning_detail_text = clean_ui_text(cooking.get("warning_message"), "None")

    st.markdown("### Cooking assistant")
    if warning_active and warning_ack:
        st.caption("Warning acknowledged")
    elif warning_active:
        st.caption("Action needed")

    # Use native Streamlit text rendering here so any stray HTML coming from upstream
    # is displayed as plain cleaned text rather than leaking into the UI.
    st.markdown(f"**{headline_text}**")
    st.write(body_text)
    st.markdown(f"**Food:** {title_case_or_dash(cooking.get('food_type'))}")
    st.markdown(f"**Stage:** {title_case_or_dash(cooking.get('stage'))}")
    st.markdown(f"**Suggested serving idea:** {recipe_text}")
    st.markdown(f"**Warning detail:** {warning_detail_text}")

    c1, c2, c3 = st.columns(3)
    pause_label = "Resume timer" if cooking.get("manual_paused") else "Pause timer"
    pause_cmd = "COOKING_RESUME" if cooking.get("manual_paused") else "COOKING_PAUSE"
    c1.button(pause_label, on_click=queue_cmd, kwargs={"cmd": pause_cmd}, use_container_width=True)
    c2.button("Reset cooking", on_click=queue_cmd, kwargs={"cmd": "COOKING_RESET"}, use_container_width=True)
    c3.button("Acknowledge warning", on_click=queue_cmd, kwargs={"cmd": "ACK_COOKING_WARNING"}, use_container_width=True)

st.subheader("Operational controls")
ops1, ops2, ops3, ops4, ops5, ops6 = st.columns(6)
ops1.button("Tare mass", on_click=queue_cmd, kwargs={"cmd": "TARE_MASS"}, use_container_width=True)
ops2.button("Start logging", on_click=queue_cmd, kwargs={"cmd": "START_LOG"}, use_container_width=True)
ops3.button("Stop logging", on_click=queue_cmd, kwargs={"cmd": "STOP_LOG"}, use_container_width=True)
ops4.button("Reset faults", on_click=queue_cmd, kwargs={"cmd": "RESET_FAULTS"}, use_container_width=True)
ops5.button("Acknowledge alarm", on_click=queue_cmd, kwargs={"cmd": "ACK"}, use_container_width=True)
ops6.button("E-STOP: RELEASE" if estop_on else "E-STOP: TRIGGER", on_click=queue_cmd, kwargs={"cmd": "ESTOP", "value": ("0" if estop_on else "1")}, use_container_width=True)

st.subheader("System status")
status_cols = st.columns(4)
status_cols[0].metric("System", "FAULT" if fault_active else "NORMAL")
status_cols[1].metric("RoP", "Active" if fault_active else "Inactive")
status_cols[2].metric("Heating", "Disabled" if fault_active else ("Enabled" if heater_on else "Off"))
status_cols[3].metric("Logging", "On" if logging_on else "Off")
st.caption(f"STM32 timestamp: {ts or '—'}")

st.subheader("Last 2 minutes")
hist = s.get("history", [])
df = pd.DataFrame(hist, columns=["t_epoch", "t_avg", "mass_g"])
if len(df) > 0:
    df["time"] = pd.to_datetime(df["t_epoch"], unit="s")
    df["t_smooth"] = df["t_avg"].rolling(window=8, min_periods=1).mean()
    df["m_smooth"] = df["mass_g"].rolling(window=5, min_periods=1).mean()
    g1, g2 = st.columns(2)
    with g1:
        fig_t = px.line(df, x="time", y="t_smooth", title="Temperature (avg °C)")
        fig_t.add_scatter(x=df["time"], y=df["t_avg"], mode="lines", name="raw", opacity=0.25)
        fig_t.update_layout(xaxis_title="Time (mm:ss)", yaxis_title="°C", margin=dict(l=60, r=10, t=40, b=50), showlegend=False)
        fig_t.update_xaxes(tickformat="%M:%S", nticks=8, showgrid=True)
        fig_t.update_yaxes(showgrid=True, ticks="outside", showticklabels=True)
        st.plotly_chart(fig_t, use_container_width=True)
    with g2:
        fig_m = px.line(df, x="time", y="m_smooth", title="Mass (g)")
        fig_m.add_scatter(x=df["time"], y=df["mass_g"], mode="lines", name="raw", opacity=0.25)
        fig_m.update_layout(xaxis_title="Time (mm:ss)", yaxis_title="g", margin=dict(l=60, r=10, t=40, b=50), showlegend=False)
        fig_m.update_xaxes(tickformat="%M:%S", nticks=8, showgrid=True)
        fig_m.update_yaxes(showgrid=True, ticks="outside", showticklabels=True)
        st.plotly_chart(fig_m, use_container_width=True)
else:
    st.info("Waiting for data...")

st.subheader("Event log")
st.markdown("<div class='log-box'>" + "<br>".join(st.session_state.event_log if st.session_state.event_log else ["No events yet."]) + "</div>", unsafe_allow_html=True)

with st.expander("Technical details"):
    st.write({
        "seq": s.get("seq"),
        "ms": s.get("ms"),
        "T1": s.get("t1"),
        "T2": s.get("t2"),
        "t_avg": s.get("t_avg"),
        "mass_g": s.get("mass_g"),
        "flags_hex": s.get("flags_hex"),
        "faults": s.get("faults"),
        "heater": s.get("heater"),
        "uart_ok": s.get("last_uart_ok"),
        "uart_lines": s.get("uart_lines"),
        "uart_err": s.get("uart_err"),
        "logging": s.get("logging"),
        "food_type": s.get("food_type"),
        "food_confidence": s.get("food_confidence"),
        "stage": s.get("stage"),
        "stage_confidence": s.get("stage_confidence"),
        "pan_present": s.get("pan_present"),
        "frame_path": s.get("frame_path"),
        "frame_mtime": file_mtime_str(s.get("frame_path") or "/home/leomcgriskin/smartcook_cv/pi_deploy/latest_frame.jpg"),
        "cooking": cooking,
    })
