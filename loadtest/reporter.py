"""PNG-отчёт через matplotlib."""

import json
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

DATA_DIR = Path("/app/data")
REPORTS_DIR = DATA_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

plt.rcParams.update({
    "figure.facecolor": "#0a0a0a",
    "axes.facecolor": "#0f0f0f",
    "axes.edgecolor": "#2a2a2a",
    "axes.labelcolor": "#e8e8e8",
    "axes.titlecolor": "#ffffff",
    "xtick.color": "#888888",
    "ytick.color": "#888888",
    "text.color": "#e8e8e8",
    "grid.color": "#1f1f1f",
    "grid.linestyle": "--",
    "grid.linewidth": 0.5,
    "font.family": "DejaVu Sans",
    "font.size": 10,
})


def _load_metrics() -> list[dict]:
    path = DATA_DIR / "metrics.jsonl"
    if not path.exists():
        return []
    points = []
    with open(path) as f:
        for line in f:
            try:
                obj = json.loads(line)
                if "STOP" not in obj:
                    points.append(obj)
            except json.JSONDecodeError:
                continue
    return points


def generate_report(test_name: str = "updo", target_url: str = "") -> str:
    points = _load_metrics()
    if len(points) < 2:
        return ""

    ts = [datetime.fromisoformat(p["ts"]) for p in points]
    start = ts[0]
    times = [(t - start).total_seconds() for t in ts]

    vus = [p.get("vu", 0) for p in points]
    avg = [p.get("avg", 0) for p in points]
    p50 = [p.get("p50", 0) for p in points]
    p95 = [p.get("p95", 0) for p in points]
    p99 = [p.get("p99", 0) for p in points]
    err_rate = [p.get("error_rate", 0) * 100 for p in points]

    total_req = points[-1].get("requests", 0)
    total_err = points[-1].get("errors", 0)
    final_err = (total_err / total_req * 100) if total_req else 0
    max_vu = max(vus) if vus else 0
    max_p95 = max(p95) if p95 else 0
    duration = times[-1] if times else 0

    fig = plt.figure(figsize=(14, 10), dpi=100)
    gs = GridSpec(3, 2, figure=fig, hspace=0.4, wspace=0.25,
                  left=0.07, right=0.97, top=0.92, bottom=0.06)

    fig.text(0.07, 0.97, f"LOAD TEST · {test_name.upper()}",
             fontsize=20, fontweight="bold", color="#00ff88", va="top")
    fig.text(0.07, 0.945,
             f"{target_url} · {start.strftime('%Y-%m-%d %H:%M UTC')} · {duration:.0f}s",
             fontsize=10, color="#888888", va="top")

    # VU
    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(times, vus, color="#00ff88", linewidth=2)
    ax1.fill_between(times, vus, alpha=0.15, color="#00ff88")
    ax1.set_ylabel("Virtual Users", color="#00ff88")
    ax1.set_xlabel("Time (s)")
    ax1.grid(True, alpha=0.3)
    ax1.set_title("Load profile", loc="left", fontsize=12)

    # Latency
    ax2 = fig.add_subplot(gs[1, :])
    ax2.plot(times, avg, color="#4a9eff", linewidth=1.5, label="avg")
    ax2.plot(times, p50, color="#00ff88", linewidth=1.5, label="p50")
    ax2.plot(times, p95, color="#ffd700", linewidth=1.5, label="p95")
    ax2.plot(times, p99, color="#ff3355", linewidth=1.5, label="p99")
    ax2.axhline(y=1500, color="#ff3355", linestyle="--",
                linewidth=1, alpha=0.5, label="p95 threshold")
    ax2.set_ylabel("Latency (ms)")
    ax2.set_xlabel("Time (s)")
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper left", fontsize=9, framealpha=0.2)
    ax2.set_title("Latency · avg / p50 / p95 / p99", loc="left", fontsize=12)

    # Error rate
    ax3 = fig.add_subplot(gs[2, 0])
    ax3.plot(times, err_rate, color="#ff3355", linewidth=1.5)
    ax3.fill_between(times, err_rate, alpha=0.2, color="#ff3355")
    ax3.axhline(y=2, color="#ff3355", linestyle="--",
                linewidth=1, alpha=0.5, label="2%")
    ax3.set_ylabel("Errors (%)")
    ax3.set_xlabel("Time (s)")
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc="upper left", fontsize=9, framealpha=0.2)
    ax3.set_title("Error rate", loc="left", fontsize=12)

    # Summary
    ax4 = fig.add_subplot(gs[2, 1])
    ax4.axis("off")
    summary = [
        ("Total requests", f"{total_req:,}", "#ffffff"),
        ("Total errors", f"{total_err:,}", "#ff3355" if total_err else "#fff"),
        ("Error rate", f"{final_err:.2f}%", "#ff3355" if final_err > 2 else "#00ff88"),
        ("Max VU", f"{max_vu:,}", "#00ff88"),
        ("Max p95", f"{max_p95:.0f} ms", "#ffd700"),
        ("Duration", f"{duration:.0f} s", "#888"),
    ]
    ax4.text(0, 1.0, "SUMMARY", fontsize=12, color="#00ff88",
             fontweight="bold", va="top")
    y = 0.9
    for label, value, color in summary:
        ax4.text(0, y, label, fontsize=11, color="#888", va="top")
        ax4.text(0.55, y, value, fontsize=11, color=color,
                 fontweight="bold", va="top", family="monospace")
        y -= 0.14

    out = REPORTS_DIR / f"report_{test_name}_{int(datetime.utcnow().timestamp())}.png"
    plt.savefig(out, facecolor=fig.get_facecolor(), dpi=100)
    plt.close(fig)
    return str(out)
