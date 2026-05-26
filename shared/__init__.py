"""Gedeelde basis voor proxy en UI: config, vocabulaire, crypto, DB-helpers.

Importeert *uitsluitend* uit de stdlib en externe libraries — nooit uit
`proxy/` of `ui/`. Dat houdt deze package cyclevrij en unit-tests snel.
"""
