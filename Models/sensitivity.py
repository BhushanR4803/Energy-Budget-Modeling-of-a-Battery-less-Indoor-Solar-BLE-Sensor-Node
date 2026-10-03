"""
Sensitivity check for the self-powered BLE node model.
Imports energy_model.py (keep both files in the same folder).

Varies ONE input at a time (0.5x, 0.75x, 1x, 1.5x, 2x of the assumed value) and
records three results for the office profile, 0.47 F cap, adaptive duty cycle:
  1. Weekday energy margin (harvested - consumed on a Wednesday, J)
  2. Time to first wake from an EMPTY cap (hours after lights on)
  3. Dark survival from a FULL cap (hours)
Then runs one combined worst case.

Run:  python sensitivity.py
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dataclasses import replace

from energy_model import Params, simulate, office_lux, smooth_noise, adaptive, DARK_LUX

DT = 10.0
STEPS_PER_DAY = int(86400 / DT)
NEVER = 72.0     # plotting cap for "never woke up within 72 h"

# fixed light profiles (same seeds as energy_model.py)
t8 = np.arange(0, 8 * 86400, DT)
lux8 = office_lux(t8, smooth_noise(len(t8), np.random.default_rng(7)))
t3 = np.arange(8 * 3600, 8 * 3600 + 3 * 86400, DT)
lux3 = office_lux(t3, smooth_noise(len(t3), np.random.default_rng(3)))
DARK = np.full(int(500 * 3600 / 30.0), DARK_LUX)


def metrics(p):
    r = simulate(lux8, DT, p, adaptive, v0=0.0)
    wed = slice(2 * STEPS_PER_DAY, 3 * STEPS_PER_DAY)
    margin = (r["p_in"][wed].sum() - r["p_drain"][wed].sum()) * DT * 1e-6

    r3 = simulate(lux3, DT, p, adaptive, v0=0.0)
    first = np.argmax(r3["conn"]) * DT / 3600 if r3["conn"].any() else np.nan

    rd = simulate(DARK, 30.0, p, adaptive, v0=p.v_max, stop_when_offline=True)
    surv = rd["n"] * 30.0 / 3600
    return margin, first, surv


if __name__ == "__main__":
    base = Params()
    inputs = {
        "Panel output": "pv_uW_per_cm2_per_lux",
        "Sleep power": "p_sleep_uW",
        "Wake energy": "e_wake_mJ",
    }
    factors = [0.5, 0.75, 1.0, 1.5, 2.0]
    names = ["Weekday margin (J)", "First wake after lights on (h)", "Dark survival (h)"]

    res = {lab: {} for lab in inputs}
    base_m = metrics(base)
    lines = []
    lines.append(f"Base case: margin {base_m[0]:.2f} J | first wake {base_m[1]:.1f} h | dark survival {base_m[2]:.1f} h")
    lines.append("")
    for lab, attr in inputs.items():
        lines.append(f"{lab} (base {getattr(base, attr)}):")
        for f in factors:
            m = base_m if f == 1.0 else metrics(replace(base, **{attr: getattr(base, attr) * f}))
            res[lab][f] = m
            fw = "never (>72)" if np.isnan(m[1]) else f"{m[1]:.1f}"
            lines.append(f"  x{f:<5} margin {m[0]:+6.2f} J | first wake {fw:>11} h | dark survival {m[2]:6.1f} h")
        lines.append("")

    worst = replace(base, pv_uW_per_cm2_per_lux=base.pv_uW_per_cm2_per_lux * 0.5,
                    p_sleep_uW=base.p_sleep_uW * 2.0, e_wake_mJ=base.e_wake_mJ * 1.5)
    w = metrics(worst)
    fw = "never (>72)" if np.isnan(w[1]) else f"{w[1]:.1f} h"
    worst_txt = (f"Combined worst case (panel x0.5, sleep x2, wake x1.5): "
                 f"margin {w[0]:+.2f} J | first wake {fw} | dark survival {w[2]:.1f} h")
    lines.append(worst_txt)

    # ---------------- tornado chart: 0.5x vs 2x of each input
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4))
    c_lo, c_hi = "#E69F00", "#0072B2"
    labels = list(inputs)
    for k, (ax, nm) in enumerate(zip(axes, names)):
        b = base_m[k]
        for j, lab in enumerate(labels):
            lo, hi = res[lab][0.5][k], res[lab][2.0][k]
            lo_plot = NEVER if (k == 1 and np.isnan(lo)) else lo
            hi_plot = NEVER if (k == 1 and np.isnan(hi)) else hi
            y = len(labels) - 1 - j
            ax.barh(y, lo_plot - b, left=b, color=c_lo, height=0.55, label="input x0.5" if j == 0 else None)
            ax.barh(y, hi_plot - b, left=b, color=c_hi, height=0.55, label="input x2" if j == 0 else None)
            for val, plot, col in ((lo, lo_plot, c_lo), (hi, hi_plot, c_hi)):
                txt = ">72" if (k == 1 and np.isnan(val)) else f"{val:.1f}"
                ha = "left" if plot >= b else "right"
                ax.annotate(txt, (plot, y), xytext=(4 if plot >= b else -4, 0),
                            textcoords="offset points", ha=ha, va="center", fontsize=8.5, color=col)
        ax.margins(x=0.12)
        ax.axvline(b, color="black", lw=1)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels[::-1])
        ax.set_title(f"{nm}\nbase = {b:.1f}", fontsize=10.5, loc="left")
        ax.grid(axis="x", alpha=0.25)
        if k == 0:
            ax.axvline(0, color="#D55E00", ls="--", lw=1)
            ax.legend(frameon=False, fontsize=8.5, loc="lower right")

    fig.text(0.01, 0.005, "Behavioral model. One input changed at a time, others at base. " + worst_txt,
             fontsize=7.5, color="gray")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig("fig4_sensitivity.png", dpi=160)

    text = "\n".join(lines)
    print(text)
    with open("sensitivity_summary.txt", "w") as f:
        f.write(text + "\n")
