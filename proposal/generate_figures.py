"""
Generates the figures embedded in proposal.docx from this project's actual
recorded results (analytical_model/FINDINGS.md and champsim_custom/
PHASE2_RESULTS.md v4). No numbers here are invented -- every value below is
transcribed from those two files' printed tables. Run with:

    python3 proposal/generate_figures.py

Outputs PNGs to proposal/figures/. Palette per the dataviz skill's validated
default (references/palette.md): categorical hues in fixed order, one
sequential hue (blue) for magnitude-only charts, status colors reserved for
trustworthy/untrustworthy framing.
"""

import os

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

matplotlib.use("Agg")

OUT_DIR = os.path.join(os.path.dirname(__file__), "figures")
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------------------------------------------------------- palette --
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

BLUE = "#2a78d6"
AQUA = "#1baf7a"
YELLOW = "#eda100"
VIOLET = "#4a3aa7"
RED = "#e34948"

STATUS_GOOD = "#0ca30c"
STATUS_CRITICAL = "#d03b3b"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
    "text.color": INK_PRIMARY,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": INK_SECONDARY,
    "axes.facecolor": SURFACE,
    "figure.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "xtick.color": INK_MUTED,
    "ytick.color": INK_MUTED,
    "grid.color": GRIDLINE,
    "font.size": 11,
})


def style_axes(ax, hide_spines=("top", "right")):
    for s in hide_spines:
        ax.spines[s].set_visible(False)
    for s in ax.spines:
        if s not in hide_spines:
            ax.spines[s].set_color(BASELINE)
    ax.tick_params(colors=INK_MUTED, length=0)


# =============================================================== Figure 1 ==
# Calibration fix: spike v1 (uncalibrated) vs model v2 (calibrated) vs
# Magellan's own measured ceiling. Source: analytical_model/FINDINGS.md
# section 1 ("Worst-case wasted bandwidth ... bounded above by 15%") and
# spike_v1's own headline ~40% worst case, vs Magellan ISCA'25 Fig.19's
# measured ~10% (swept 5-15% in the v2 model).
def fig1_calibration():
    labels = ["Uncalibrated spike\n(v1, pre-audit)", "Calibrated model\n(v2, this project)", "Magellan's own\nmeasured ceiling"]
    values = [40.0, 13.3, 10.0]

    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=200)
    bars = ax.bar(labels, values, width=0.55, color=BLUE, zorder=3)
    bars[1].set_color(STATUS_GOOD)  # the corrected, validated figure

    for rect, v in zip(bars, values):
        ax.annotate(f"{v:.1f}%", (rect.get_x() + rect.get_width() / 2, v), xytext=(0, 6),
                    textcoords="offset points", ha="center", fontsize=11, color=INK_PRIMARY, fontweight="bold")

    ax.set_ylabel("Worst-case wasted bandwidth\n(% of DRAM channel / total prefetch traffic)")
    ax.set_ylim(0, 48)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.grid(axis="y", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    style_axes(ax)
    ax.set_title("Fixing an unanchored estimate: calibrating against a primary source", fontsize=12, color=INK_PRIMARY, pad=14)
    fig.tight_layout(rect=(0, 0.14, 1, 1))
    fig.text(0.5, 0.02, "The v1 spike's headline number was ~4x higher than what a real system of this class actually measures (Magellan, ISCA'25).\nv2 anchors total prefetch overhead to that measurement, so it can never repeat the error.",
              ha="center", fontsize=8.5, color=INK_MUTED)
    fig.savefig(os.path.join(OUT_DIR, "fig1_calibration_fix.png"), bbox_inches="tight")
    plt.close(fig)


# =============================================================== Figure 2 ==
# Composed end-to-end residual bandwidth sweep. Source: analytical_model/
# FINDINGS.md section 2 (the printed table from model.py).
def fig2_composed_sweep():
    rows = [
        ("MPKI 5, gate 1\n(light)", 0.0, False),
        ("MPKI 10, gate 2\n(typical, alpha=2)", 0.1, False),
        ("MPKI 10, gate 2\n(typical, alpha=1)", 0.5, False),
        ("MPKI 10, gate 2\n(typical, alpha=0)", 0.9, False),
        ("MPKI 20, gate 3\n(elevated)", 2.0, False),
        ("MPKI 20, gate 3\n(elevated, +overhead)", 3.0, False),
        ("MPKI 30, gate 5\n(stress case)", 7.7, True),
    ]
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    is_stress = [r[2] for r in rows]
    colors = [RED if s else BLUE for s in is_stress]

    fig, ax = plt.subplots(figsize=(8.4, 4.6), dpi=200)
    y_pos = range(len(labels))
    bars = ax.barh(list(y_pos), values, color=colors, height=0.6, zorder=3)
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels, fontsize=9.5)
    ax.invert_yaxis()

    for rect, v in zip(bars, values):
        ax.annotate(f"{v:.1f}%", (v, rect.get_y() + rect.get_height() / 2), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=10, color=INK_PRIMARY, fontweight="bold")

    ax.set_xlabel("Residual wasted bandwidth after reactive throttling\n(% of one DRAM channel)")
    ax.set_xlim(0, 9)
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.grid(axis="x", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    style_axes(ax, hide_spines=("top", "right", "left"))
    ax.tick_params(axis="y", length=0)

    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE), plt.Rectangle((0, 0), 1, 1, color=RED)]
    ax.legend(handles, ["Representative case", "Stress case (worst swept combination)"], loc="upper right",
              bbox_to_anchor=(1.0, 0.95), frameon=False, fontsize=8.5, labelcolor=INK_SECONDARY)
    ax.set_title("Composed model output: wrong-path bandwidth surviving existing mitigation", fontsize=12, color=INK_PRIMARY, pad=14)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig2_composed_sweep.png"))
    plt.close(fig)


# =============================================================== Figure 3 ==
# Latency-uplift framing: the same residual buys more delay-proxy reduction
# at higher baseline channel utilization. Source: FINDINGS.md section 3.
def fig3_latency_uplift():
    alphas = ["alpha = 0.0\n(diffuse waste)", "alpha = 1.0\n(mid)", "alpha = 2.0\n(concentrated)"]
    rho_50 = [5.4, 2.7, 0.5]
    rho_70 = [6.5, 3.2, 0.6]
    rho_85 = [10.8, 5.3, 1.0]

    x = range(len(alphas))
    width = 0.26
    fig, ax = plt.subplots(figsize=(7.0, 4.4), dpi=200)
    b1 = ax.bar([i - width for i in x], rho_50, width=width, color=BLUE, label="rho_base = 0.50", zorder=3)
    b2 = ax.bar([i for i in x], rho_70, width=width, color=AQUA, label="rho_base = 0.70", zorder=3)
    b3 = ax.bar([i + width for i in x], rho_85, width=width, color=VIOLET, label="rho_base = 0.85", zorder=3)

    for bars in (b1, b2, b3):
        for rect in bars:
            h = rect.get_height()
            ax.annotate(f"{h:.1f}%", (rect.get_x() + rect.get_width() / 2, h), xytext=(0, 4),
                        textcoords="offset points", ha="center", fontsize=8, color=INK_SECONDARY)

    ax.set_xticks(list(x))
    ax.set_xticklabels(alphas, fontsize=9.5)
    ax.set_ylabel("Queueing-delay-proxy reduction\nfrom eliminating the residual")
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.set_ylim(0, 13)
    ax.grid(axis="y", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    style_axes(ax)
    ax.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=INK_SECONDARY)
    ax.set_title("The same residual bandwidth matters more as channel contention rises", fontsize=12, color=INK_PRIMARY, pad=14)
    fig.text(0.5, -0.02, "rho_base = baseline DRAM channel utilization from demand + useful-prefetch traffic (M/M/1-style proxy; directional, not a calibrated latency prediction).",
              ha="center", fontsize=8.5, color=INK_MUTED, wrap=True)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig3_latency_uplift.png"), bbox_inches="tight")
    plt.close(fig)


# =============================================================== Figure 4 ==
# ChampSim empirical validation (Phase 0-2, v4 final). Source:
# champsim_custom/PHASE2_RESULTS.md "v4 final results" table.
def fig4_champsim_empirical():
    # (pc, wasted_fraction_pct, n, trustworthy)
    rows = [
        ("0x401671", 18.3, 9480, True),
        ("0x401669", 18.3, 9509, True),
        ("0x401660", 68.6, 2936, True),
        ("0x40166d", 37.1, 377, True),
        ("0x401682", 96.5, 172, False),
    ]
    rows.sort(key=lambda r: r[1])
    labels = [f"{r[0]}\n(n={r[2]:,})" for r in rows]
    values = [r[1] for r in rows]
    trust = [r[3] for r in rows]

    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=200)
    y_pos = range(len(labels))
    colors = [BLUE if t else INK_MUTED for t in trust]
    hatches = [None if t else "///" for t in trust]
    bars = ax.barh(list(y_pos), values, color=colors, height=0.6, zorder=3)
    for rect, h in zip(bars, hatches):
        if h:
            rect.set_hatch(h)
            rect.set_edgecolor(STATUS_CRITICAL)
            rect.set_linewidth(0.8)

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels, fontsize=9.5)

    for rect, v in zip(bars, values):
        ax.annotate(f"{v:.1f}%", (v, rect.get_y() + rect.get_height() / 2), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=10, color=INK_PRIMARY, fontweight="bold")

    # analytical model's typical predicted band, for visual cross-check
    ax.axvspan(0, 2.0, color=STATUS_GOOD, alpha=0.08, zorder=0)

    ax.set_xlabel("Measured wasted-prefetch fraction (wrong-path-equivalent), % of matched samples")
    ax.set_xlim(0, 105)
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.grid(axis="x", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    style_axes(ax, hide_spines=("top", "right", "left"))
    ax.tick_params(axis="y", length=0)

    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE), plt.Rectangle((0, 0), 1, 1, color=INK_MUTED, hatch="///", edgecolor=STATUS_CRITICAL)]
    ax.legend(handles, ["Trustworthy (clean measurement queue)", "Not trustworthy (pathological issue/match-rate mismatch)"],
              loc="lower right", frameon=False, fontsize=8, labelcolor=INK_SECONDARY)
    ax.set_title("ChampSim empirical validation: 4 of 5 tracked load sites (429.mcf)", fontsize=12, color=INK_PRIMARY, pad=14)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig4_champsim_empirical.png"), bbox_inches="tight")
    plt.close(fig)


# =============================================================== Figure 5 ==
# The debugging journey: overflow-bucket concentration across instrumentation
# iterations. Source: champsim_custom/PHASE2_RESULTS.md (v1: 87.8%, v2: 39%,
# v3: 2.0%). Included to show measurement rigor, not as a proposal claim.
def fig5_debugging_journey():
    labels = ["v1\n(unscoped)", "v2\n(branch-scoped)", "v3\n(+ warmup fix,\nIP-distance bound)"]
    values = [87.8, 39.0, 2.0]

    fig, ax = plt.subplots(figsize=(6.0, 4.0), dpi=200)
    bars = ax.bar(labels, values, width=0.55, color=[RED, YELLOW, STATUS_GOOD], zorder=3)
    for rect, v in zip(bars, values):
        ax.annotate(f"{v:.1f}%", (rect.get_x() + rect.get_width() / 2, v), xytext=(0, 6),
                    textcoords="offset points", ha="center", fontsize=11, color=INK_PRIMARY, fontweight="bold")
    ax.set_ylabel("Prefetches hitting the 32-branch\noverflow bucket (share of matched samples)")
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.grid(axis="y", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    style_axes(ax)
    ax.set_title("Instrumentation methodology converging across 3 iterations", fontsize=12, color=INK_PRIMARY, pad=14)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig5_debugging_journey.png"), bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    fig1_calibration()
    fig2_composed_sweep()
    fig3_latency_uplift()
    fig4_champsim_empirical()
    fig5_debugging_journey()
    print(f"Wrote figures to {OUT_DIR}")
