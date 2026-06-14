"""Core runtime services: shared mutable state, wallet/economics, audit logging.

These are the canonical, dependency-light implementations that the monolith
(``librate_casino``) delegates to. They depend on ``storage`` (the DB) and are
unit-tested in isolation, never importing ``librate_casino``.
"""
