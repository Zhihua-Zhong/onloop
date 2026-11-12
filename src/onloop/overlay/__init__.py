"""The overlay ledger: one SQLite event log and the projections rebuilt from it (decision 0006).

    schema.py       the ledger's tables and their forward-only migrations
    projections.py  how each event kind changes the projection tables; pure, so replay rebuilds them exactly
    ledger.py       Ledger: append an event, apply it, replay, read the projections
    importer.py     forward-only import of the older stores (onloop.db, the work graph) into the log
    constraint.py   the constraint: which waited-on thing holds up the most waiting

Every write appends one event first and then updates the projections in the same transaction. The projections can
be dropped and rebuilt from the log at any time, and a rebuild gives the same rows byte for byte.
"""
