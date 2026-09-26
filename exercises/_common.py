"""Shared helpers for the exercise scripts."""
import argparse, os, sys

LAB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB_ROOT)
DEFAULT_RESULTS = os.path.join(LAB_ROOT, "data", "runs", "llma4se_live")


def parse(description, extra=None):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--results", default=os.environ.get("HARNESSLAB_RESULTS", os.environ.get("AGENTLAB_RESULTS", DEFAULT_RESULTS)),
                    help="results directory produced by harnesslab.core.runner (default: llma4se_live, 1,632 real runs recorded through OpenRouter)")
    ap.add_argument("--harness", default="baseline", help="harness id to focus on")
    ap.add_argument("--out", default=os.path.join(LAB_ROOT, "data", "figures"), help="where to write figures")
    if extra:
        extra(ap)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    return args


def try_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except Exception:
        print("(matplotlib not available: skipping figures)")
        return None


def hr(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)
