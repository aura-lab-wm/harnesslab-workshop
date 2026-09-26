"""harnesslab.core (formerly agentlab): a deliberately small coding-agent harness built for teaching
how to *measure* agents, not for solving hard tasks.

Modules
  providers   LLM back-ends (OpenAI-compatible, Anthropic, and an offline mock)
  tools       tool surface, sandbox, and the permission policy
  ledger      the measurement ledger: one JSONL span per consequential action
  harness     the control loop (context, tools, permissions, stopping rule)
  grader      hidden-test grading of the workspace the agent leaves behind
  runner      repeated runs over tasks x harnesses -> results directory
  analysis    pass@k, pass^k, bootstrap CIs, tokens per solve, kappa, ...
  judge       LLM-as-judge with position swap and repeat calibration
  trajtest    load ledgers and write assertions over trajectories
"""
__version__ = "0.1.0"
