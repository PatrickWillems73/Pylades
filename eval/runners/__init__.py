"""Runner-adapters: zetten een prompt om in voorspelde entities + outbound-tekst.

De `PyladesPipelineRunner` draait de echte detectie-pijplijn (regex + spaCy,
optioneel laag-3). Latere adapters (GLiNER, DEDUCE, andere spaCy-modellen)
implementeren hetzelfde `Runner`-protocol zodat de scoring model-agnostisch is.
"""
