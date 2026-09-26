"""Exercise 6: the evaluation report card.

Everything the lecture asked for, produced from a results directory: the cell
(model, harness, tasks, protocol), outcome distributions with uncertainty,
conduct, cost, and the two integrity checks. This is the recipe as code.

The card itself lives in `harnesslab/core/reportcard.py` so that this script, the platform's
Report card page and its `/report.md` download are the same implementation — three copies
of "what pass@1 means" is three chances to disagree.

    python exercises/ex6_report_card.py --harness baseline
    python exercises/ex6_report_card.py --results data/runs/mine --harness baseline > my_report.md
"""
from _common import parse
from harnesslab.core.analysis import load_index, filter_rows
from harnesslab.core.reportcard import build_card, card_markdown
from harnesslab.core.trajtest import load_runs

args = parse(__doc__)
rows = filter_rows(load_index(args.results), harness_id=args.harness)
if not rows:
    raise SystemExit("no runs")
traj = load_runs(args.results, harness_id=args.harness)
print(card_markdown(build_card(rows, traj, source=args.results)))
