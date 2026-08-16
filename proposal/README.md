# Proposal source of truth

The current proposal is built from `proposal.tex` and `sections/*.tex`.

Current title:

> A Web-Based System for Continuous Hotel Room Rate Monitoring and Short-Term Forecasting in Vietnam Using Machine Learning

Build from this directory with:

```powershell
latexmk -pdf -interaction=nonstopmode -halt-on-error proposal.tex
```

The generated submission preview is `proposal.pdf`.

`detailed/` is an archived earlier draft. It still contains superseded assumptions such as a
24-week schedule and four required model families; do not copy scope, targets or timeline from it
without reconciling them against `CLAUDE.md`, `ROADMAP.md` and the current `sections/` files.

