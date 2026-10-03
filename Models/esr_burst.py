"""
ESR vs BLE-burst model for the supercap -> BQ25570 VSTOR node.
Supercap (internal voltage V0) -- ESR -- VSTOR node (buffer cap) -- BQ25570 buck -- 3.3 V load burst.
The buck is modelled as a constant-power load on VSTOR during the burst.

ALL VALUES ARE ASSUMPTIONS (datasheet-class), not measurements.
Run:  python esr_burst.py
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

V_OUT = 3.3          # buck output rail (V)
I_BURST = 12e-3      # nRF52840 board current during TX + sensor read, at 3.3 V (A)  [assumption]
T_BURST = 6e-3       # burst duration (s)                                          [assumption]
BUCK_EFF = 0.90
V_CUTOFF = 2.2       # BQ25570 VBAT_UV (load disconnect threshold, as in the energy model)
V0 = 2.6             # supercap voltage in a "low but still running" state (V)
P_BURST = V_OUT * I_BURST / BUCK_EFF   # power drawn from VSTOR during the burst (W)


def burst(esr, c_buf, v0=V0, dt=2e-6, t_pre=2e-3, t_total=50e-3):
    t = np.arange(-t_pre, t_total, dt)
    v = np.empty_like(t)
    vs = v0
    for k, tk in enumerate(t):
        i_in = P_BURST / max(vs, 0.3) if 0 <= tk < T_BURST else 0.0
        vs += dt * ((v0 - vs) / esr - i_in) / c_buf
        vs = max(vs, 0.0)        # rail collapse: cannot go below 0 V
        v[k] = vs
    return t * 1e3, v


if __name__ == "__main__":
    esr_list = [1, 10, 30, 100]
    buffers = [(10e-6, "10 uF (PMIC minimum only)"), (100e-6, "100 uF"), (470e-6, "470 uF")]
    cols = ["#D55E00", "#E69F00", "#009E73"]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 4.9))

    # (a) waveform at ESR = 30 ohm
    for (c, lab), col in zip(buffers, cols):
        t, v = burst(30, c)
        a1.plot(t, v, color=col, label=lab, lw=1.8)
    a1.axhline(V_CUTOFF, color="#D55E00", ls="--", lw=1)
    a1.text(30, V_CUTOFF + 0.03, "2.2 V node shuts off", fontsize=8.5, color="#D55E00")
    a1.axvspan(0, T_BURST * 1e3, color="#EEEEEE", zorder=0)
    a1.text(0.2, 0.35, "BLE burst", fontsize=8.5, color="gray")
    a1.set_ylim(0.3, 2.8)
    a1.set_xlabel("Time (ms)")
    a1.set_ylabel("VSTOR (V)")
    a1.set_title(f"(a) Supercap ESR = 30 ohm, cap at {V0} V\n{I_BURST*1e3:.0f} mA / {T_BURST*1e3:.0f} ms burst",
                 fontsize=10.5, loc="left")
    a1.legend(frameon=False, fontsize=9, loc="lower right")
    a1.grid(alpha=0.25)

    # (b) minimum VSTOR vs ESR
    print(f"Burst: {I_BURST*1e3:.0f} mA at {V_OUT} V for {T_BURST*1e3:.0f} ms -> "
          f"{I_BURST*V_OUT*T_BURST*1e3:.3f} mJ out, {P_BURST*1e3:.1f} mW from VSTOR")
    print(f"Supercap at {V0} V.  Minimum VSTOR during burst (V):")
    print("ESR(ohm) | " + " | ".join(lab.split(' (')[0] for _, lab in buffers))
    for (c, lab), col in zip(buffers, cols):
        mins = [burst(e, c)[1].min() for e in esr_list]
        a2.plot(esr_list, mins, marker="o", color=col, label=lab, lw=1.8)
    for e in esr_list:
        print(f"{e:>8} | " + " | ".join(f"{burst(e, c)[1].min():.2f}" for c, _ in buffers))
    a2.axhline(V_CUTOFF, color="#D55E00", ls="--", lw=1)
    a2.text(1.1, V_CUTOFF + 0.03, "2.2 V node shuts off", fontsize=8.5, color="#D55E00")
    a2.set_xscale("log")
    a2.set_xticks(esr_list)
    a2.set_xticklabels([f"{e} ohm" for e in esr_list])
    a2.set_ylim(0.3, 2.8)
    a2.set_xlabel("Supercap ESR")
    a2.set_ylabel("Lowest VSTOR during burst (V)")
    a2.set_title("(b) Dip depth vs ESR and buffer size", fontsize=10.5, loc="left")
    a2.grid(alpha=0.25)

    fig.text(0.01, 0.005,
             "Behavioral model; burst current, duration and ESR are assumptions - check your supercap datasheet.",
             fontsize=8, color="gray")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig("fig3_esr_burst.png", dpi=160)
