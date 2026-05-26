"""FastAPI-proxy: detectie, generalisering, pseudonimisering en HTTP-routing.

Wordt door `uvicorn proxy.main:app` als ASGI-applicatie geladen. Mag wel
uit `shared/` importeren, maar nooit uit `ui/` — de UI is de consument,
niet andersom.
"""
