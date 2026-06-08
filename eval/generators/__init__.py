"""Dataset-generatoren voor het eval-harnas.

`bootstrap` levert een deterministische, offline gelabelde dataset (geen API
nodig) zodat het harnas direct draaibaar is. De LLM-gedreven generatoren
(synthetische dossiers + adversariële cases) komen in een latere fase en
schrijven hetzelfde JSONL-formaat (`eval/schema.py`).
"""
