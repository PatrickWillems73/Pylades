"""Pylades eval-harnas: her-uitvoerbare detectie-evaluatie.

Dit pakket staat los van de runtime (`proxy/`, `shared/`, `ui/`). Het leest
gepinde, span-level gelabelde datasets, draait de detectie-pijplijn (of een
NER-adapter) eroverheen en berekent precision/recall/F1, leak-rate en
generalisatie-correctheid. Zie `TESTPLAN.md` voor het volledige plan.
"""
