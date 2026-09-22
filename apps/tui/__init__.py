"""Human TUI client boundary for the Web control plane.

This package is a *client*: it renders the projections that ``web/backend``
already serves and owns no workflow logic of its own.  Keeping one contract for
both the browser dashboard and the terminal is deliberate -- two independent
projections of the same workflow state would drift.
"""
