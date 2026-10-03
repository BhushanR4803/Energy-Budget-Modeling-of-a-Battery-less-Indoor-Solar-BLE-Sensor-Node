"""
Self-Powered BLE Sensor Node - behavioral energy model
=======================================================
Chain: indoor light -> a-Si panel (55x70 mm) -> BQ25570 (MPPT + boost + buck)
       -> 0.47 F supercap -> nRF52840 (deep sleep, periodic BLE advert) + BME280

ALL NUMBERS ARE ASSUMPTIONS from datasheet-class figures, NOT measurements.
Edit the Params dataclass to match your real parts.
Run:  python energy_model.py   (needs numpy + matplotlib)
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dataclasses import dataclass, replace


# ----------------------------------------------------------------- parameters
@dataclass
class Params:
    # PV panel
    panel_area_cm2: float = 5.5 * 7.0        # 55 x 70 mm = 38.5 cm2
    pv_uW_per_cm2_per_lux: float = 0.02      # a-Si indoor: ~10 uW/cm2 at 500 lux
    mppt_eff: float = 0.90                   # how close the MPPT tracks the max power point
    # storage + PMIC
    cap_F: float = 0.47
    v_max: float = 4.2                       # BQ25570 overvoltage setpoint
    v_cutoff: float = 2.2                    # load disconnects below this (VBAT_UV)
    v_resume: float = 3.0                    # load reconnects above this (VBAT_OK hysteresis)
    leak_uA_per_F: float = 4.26              # supercap leakage (~2 uA at 0.47 F), scales with C
    pmic_iq_uA: float = 0.5                  # BQ25570 quiescent current
    cold_start_v: float = 1.8                # below this the PMIC is in inefficient cold start
    cold_eff: float = 0.15
    cold_min_uW: float = 10.0                # min panel power for cold start to work
    # load (at 3.3 V rail)
    p_sleep_uW: float = 18.0                 # nRF52840 board sleep + BME280 standby (~5.5 uA)
    buck_eff: float = 0.90
    e_wake_mJ: float = 0.25                  # BME280 forced read + 3-channel BLE advert


# PMIC boost efficiency vs input power (typical shape: poor at tiny input power)
PV_PTS = np.array([0, 5, 10, 50, 100, 400, 1000.0])
EFF_PTS = np.array([0, 0.20, 0.45, 0.70, 0.80, 0.88, 0.90])

DARK_LUX = 3.0   # standby lights / closed blinds


# ------------------------------------------------------- wake-interval policies
def fixed(T):
    return lambda v: T


def adaptive(v):
    """Adaptive duty cycle: report more often when the cap is full, back off when low."""
    if v >= 3.8:
        return 10.0
    if v >= 3.4:
        return 30.0
    if v >= 2.8:
        return 60.0
    return 300.0


# ------------------------------------------------------------- light profiles
def smooth_noise(n, rng, win=180):
    raw = rng.normal(size=n)
    s = np.convolve(raw, np.ones(win) / win, mode="same")
    f = 1 + 0.12 * s / s.std()
    return np.clip(f, 0.7, 1.3)


def office_lux(t, noise):
    """Mon-Fri 08-18 lit (350 lux lamps + midday window bump), otherwise dark."""
    day = (t // 86400).astype(int)
    hod = (t % 86400) / 3600
    dow = day % 7
    lit = (dow < 5) & (hod >= 8) & (hod < 18)
    win = np.where((hod >= 9) & (hod <= 16), 120 * np.sin(np.pi * (hod - 9) / 7), 0.0)
    return np.where(lit, (350 + win) * noise, DARK_LUX)


def home_lux(t, noise):
    """Dim home: window light 07-18 (peak ~160 lux) + lamps (~100 lux) morning/evening."""
    hod = (t % 86400) / 3600
    window = np.where((hod >= 7) & (hod <= 18), 160 * np.sin(np.pi * (hod - 7) / 11), 0.0)
    lamps = np.where(((hod >= 6.5) & (hod < 8.5)) | ((hod >= 18) & (hod < 23)), 100.0, 0.0)
    return np.maximum((window + lamps) * noise, DARK_LUX)


# ----------------------------------------------------------------- simulation
def simulate(lux, dt, p, interval_fn, v0=0.0, stop_when_offline=False):
    n = len(lux)
    C = p.cap_F
    Emax = 0.5 * C * p.v_max ** 2
    E = 0.5 * C * v0 ** 2
    leak_uA = p.leak_uA_per_F * C
    conn = v0 >= p.v_resume

    V = np.zeros(n)
    CONN = np.zeros(n, bool)
    P_IN = np.zeros(n)
    P_DRAIN = np.zeros(n)
    WAKES = np.zeros(n)
    used = n

    for i in range(n):
        v = np.sqrt(2 * E / C)
        pv = lux[i] * p.pv_uW_per_cm2_per_lux * p.panel_area_cm2 * p.mppt_eff   # uW
        if v < p.cold_start_v:
            eff = p.cold_eff if pv >= p.cold_min_uW else 0.0
        else:
            eff = np.interp(pv, PV_PTS, EFF_PTS)
        p_in = pv * eff

        loss = v * leak_uA * (v / 3.0) + v * p.pmic_iq_uA     # leakage + PMIC Iq (uW)
        if conn:
            interval = interval_fn(v)
            p_load = (p.p_sleep_uW + p.e_wake_mJ * 1000.0 / interval) / p.buck_eff
            WAKES[i] = dt / interval
        else:
            p_load = 0.0

        E = min(max(E + (p_in - loss - p_load) * dt * 1e-6, 0.0), Emax)
        v_new = np.sqrt(2 * E / C)

        was = conn
        if conn and v_new < p.v_cutoff:
            conn = False
        elif (not conn) and v_new >= p.v_resume:
            conn = True

        V[i], CONN[i], P_IN[i], P_DRAIN[i] = v_new, conn, p_in, loss + p_load
        if stop_when_offline and was and not conn:
            used = i + 1
            break

    return dict(V=V[:used], conn=CONN[:used], p_in=P_IN[:used],
                p_drain=P_DRAIN[:used], wakes=WAKES[:used], n=used)


# --------------------------------------------------------------------- plots
C_LUX, C_IN, C_OUT, C_V, C_W = "#E69F00", "#009E73", "#D55E00", "#0072B2", "#56B4E9"


def fig_week(p, path, summary):
    dt = 10.0
    days = 8                                    # Mon 00:00 -> next Mon 00:00
    t = np.arange(0, days * 86400, dt)
    rng = np.random.default_rng(7)
    lux = office_lux(t, smooth_noise(len(t), rng))
    r = simulate(lux, dt, p, adaptive, v0=0.0)

    hrs = t / 3600
    steps_h = int(3600 / dt)
    nh = days * 24

    def hourly(x, how="mean"):
        a = x[:nh * steps_h].reshape(nh, steps_h)
        return a.mean(1) if how == "mean" else a.sum(1)

    h_x = np.arange(nh) + 0.5
    fig, ax = plt.subplots(4, 1, figsize=(12, 11), sharex=True,
                           gridspec_kw=dict(height_ratios=[1, 1.2, 1.6, 1]))
    for a in ax:
        a.axvspan(5 * 24, 7 * 24, color="#EEEEEE", zorder=0)   # weekend
        a.grid(alpha=0.25)

    ax[0].fill_between(hrs, lux, color=C_LUX, alpha=0.7, linewidth=0)
    ax[0].set_ylabel("Light (lux)")
    ax[0].set_title("Self-powered BLE node: 8-day behavioral simulation, starting from an EMPTY supercap\n"
                    "Office lighting, 55x70 mm a-Si panel, BQ25570, 0.47 F supercap, nRF52840 + BME280, adaptive duty cycle",
                    fontsize=11.5, loc="left")

    ax[1].plot(h_x, hourly(r["p_in"]), color=C_IN, label="Harvested into supercap (after PMIC)")
    ax[1].plot(h_x, hourly(r["p_drain"]), color=C_OUT, label="Node + leakage drain")
    ax[1].set_ylabel("Power (uW, hourly mean)")
    ax[1].legend(loc="upper right", fontsize=9, frameon=False)

    ax[2].plot(hrs, r["V"], color=C_V, linewidth=1.4)
    ax[2].axhline(p.v_max, color="gray", ls=":", lw=1)
    ax[2].axhline(p.v_resume, color="gray", ls="--", lw=1)
    ax[2].axhline(p.v_cutoff, color=C_OUT, ls="--", lw=1)
    ax[2].text(1, p.v_max + 0.05, "4.2 V full", fontsize=8, color="gray")
    ax[2].text(1, p.v_resume + 0.05, "3.0 V node restarts", fontsize=8, color="gray")
    ax[2].text(1, p.v_cutoff - 0.2, "2.2 V node shuts off", fontsize=8, color=C_OUT)
    off = ~r["conn"]
    ax[2].fill_between(hrs, 0, 4.6, where=off, color=C_OUT, alpha=0.10, linewidth=0)
    ax[2].set_ylim(0, 4.6)
    ax[2].set_ylabel("Supercap voltage (V)")
    ax[2].text(0.5, 0.15, "red shading = node offline", transform=ax[2].transAxes, fontsize=8, color=C_OUT)

    ax[3].bar(h_x, hourly(r["wakes"], "sum"), width=1.0, color=C_W)
    ax[3].set_ylabel("Readings sent / hour")
    ax[3].set_xlabel("Day")
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon"]
    ax[3].set_xticks([24 * d + 12 for d in range(8)])
    ax[3].set_xticklabels(names)
    ax[3].set_xlim(0, days * 24)
    for d in range(1, 8):
        for a in ax:
            a.axvline(24 * d, color="gray", lw=0.4, alpha=0.5)

    fig.text(0.01, 0.005,
             "Behavioral model; all component figures are datasheet-class assumptions, not measurements.",
             fontsize=8, color="gray")
    fig.tight_layout(rect=(0, 0.015, 1, 1))
    fig.savefig(path, dpi=160)
    plt.close(fig)

    # ---- summary numbers
    first_on = np.argmax(r["conn"]) * dt / 3600
    wed = slice(2 * 86400 // int(dt), 3 * 86400 // int(dt))
    summary.append(f"First wake from empty cap (Mon 00:00 start, lights on 08:00): {first_on:.1f} h after start "
                   f"(= {first_on - 8:.1f} h after lights on)")
    summary.append(f"Wednesday: {r['wakes'][wed].sum():.0f} readings sent; "
                   f"harvested {r['p_in'][wed].sum() * dt * 1e-6:.1f} J, consumed {r['p_drain'][wed].sum() * dt * 1e-6:.1f} J")
    wk = r["conn"].reshape(-1)
    off_h = (~wk).sum() * dt / 3600
    # offline window after Friday
    fri_end = 5 * 86400 // int(dt)
    after = ~wk[fri_end:]
    if after.any():
        i0 = np.argmax(after)
        j = np.argmax(wk[fri_end + i0:]) if wk[fri_end + i0:].any() else None
        h_off = (fri_end + i0) * dt / 3600
        summary.append(f"Weekend: node shuts off at hour {h_off:.0f} "
                       f"(= {h_off - (4 * 24 + 18):.0f} h after Friday 18:00 lights-off)")
        if j is not None:
            summary.append(f"Back online Monday at hour {(fri_end + i0 + j) * dt / 3600:.1f} "
                           f"(Mon 08:00 = hour 168)")
    summary.append(f"Total offline time over the 8 days incl. initial charge: {off_h:.0f} h of {days * 24} h")
    summary.append(f"Usable energy, 4.2 V -> 2.2 V: {0.5 * p.cap_F * (p.v_max ** 2 - p.v_cutoff ** 2):.2f} J")


def fig_design(p, path, summary):
    caps = [0.1, 0.22, 0.47, 1.0]

    # (a) first wake after install from an empty cap
    dt = 10.0
    t = np.arange(8 * 3600, 8 * 3600 + 3 * 86400, dt)       # start at 08:00 on day 0 (Monday)
    rng = np.random.default_rng(3)
    prof = {"Office (~400 lux)": office_lux(t, smooth_noise(len(t), rng)),
            "Dim home (~130 lux midday)": home_lux(t, smooth_noise(len(t), rng))}
    first = {k: [] for k in prof}
    for k, lux in prof.items():
        for c in caps:
            r = simulate(lux, dt, replace(p, cap_F=c), adaptive, v0=0.0)
            if r["conn"].any():
                first[k].append(np.argmax(r["conn"]) * dt / 3600)
            else:
                first[k].append(np.nan)

    # (b) how long can it ride through darkness from full
    dt2 = 30.0
    dark = np.full(int(500 * 3600 / dt2), DARK_LUX)
    pols = {"Fixed 10 s": fixed(10.0), "Fixed 60 s": fixed(60.0), "Adaptive": adaptive}
    surv = {k: [] for k in pols}
    for k, f in pols.items():
        for c in caps:
            r = simulate(dark, dt2, replace(p, cap_F=c), f, v0=p.v_max, stop_when_offline=True)
            surv[k].append(r["n"] * dt2 / 3600)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.8))
    x = np.arange(len(caps))
    w = 0.38
    cols = [C_LUX, C_V]
    for i, (k, vals) in enumerate(first.items()):
        bars = a1.bar(x + (i - 0.5) * w, vals, w, label=k, color=cols[i])
        for b, v in zip(bars, vals):
            a1.text(b.get_x() + b.get_width() / 2, (0 if np.isnan(v) else v) + 0.3,
                    ">72" if np.isnan(v) else f"{v:.1f}", ha="center", fontsize=8)
    a1.set_xticks(x)
    a1.set_xticklabels([f"{c} F" for c in caps])
    a1.set_ylabel("Hours from empty cap to first reading")
    a1.set_title("(a) Time to first wake after install\n(starting at 08:00, empty supercap)", fontsize=10.5, loc="left")
    a1.legend(frameon=False, fontsize=9)
    a1.grid(axis="y", alpha=0.25)

    for k, c in zip(surv, [C_OUT, C_LUX, C_IN]):
        a2.plot(caps, surv[k], marker="o", label=k, color=c)
    a2.axhline(62, color="gray", ls="--", lw=1)
    a2.set_ylim(0, 68)
    a2.text(0.105, 58.5, "Office weekend: Fri 18:00 -> Mon 08:00 = 62 h", fontsize=8.5, color="gray")
    a2.set_xscale("log")
    a2.set_xticks(caps)
    a2.set_xticklabels([f"{c} F" for c in caps])
    a2.set_xlabel("Supercap size")
    a2.set_ylabel("Hours of darkness survived (from full)")
    a2.set_title("(b) Dark-period survival\n(~3 lux, leakage scales with cap size)", fontsize=10.5, loc="left")
    a2.legend(frameon=False, fontsize=9)
    a2.grid(alpha=0.25)

    fig.text(0.01, 0.005, "Behavioral model; component figures are datasheet-class assumptions, not measurements.",
             fontsize=8, color="gray")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=160)
    plt.close(fig)

    summary.append("First wake from empty (h) by cap " + str(caps) + ": " +
                   "; ".join(f"{k}: {[round(v, 1) if not np.isnan(v) else None for v in vals]}"
                             for k, vals in first.items()))
    summary.append("Dark survival (h) by cap " + str(caps) + ": " +
                   "; ".join(f"{k}: {[round(v, 1) for v in vals]}" for k, vals in surv.items()))


if __name__ == "__main__":
    p = Params()
    summary = [f"Panel {p.panel_area_cm2:.1f} cm2 -> ~{500 * p.pv_uW_per_cm2_per_lux * p.panel_area_cm2 * p.mppt_eff:.0f} uW at 500 lux (before PMIC)",
               f"Wake energy {p.e_wake_mJ} mJ, sleep {p.p_sleep_uW} uW"]
    fig_week(p, "fig1_week_simulation.png", summary)
    fig_design(p, "fig2_design_space.png", summary)
    text = "\n".join(summary)
    print(text)
    with open("model_summary.txt", "w") as f:
        f.write(text + "\n")
