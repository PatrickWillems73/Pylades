"""Type-safe runtime-configuratie via pydantic-settings v2.

Eén klasse, één singleton, één bron van waarheid voor alles wat zowel proxy
als UI nodig hebben. Velden mappen 1-op-1 op `.env.example`.

Eager singleton (`settings = Settings()` op module-niveau) is bewust: een
malformed `.env` faalt zo bij `import shared.config`, niet pas wanneer de
proxy z'n eerste request krijgt.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Pylades runtime-configuratie (releaseversie in ``shared.version``).

    Defaults zijn zo gekozen dat `Settings()` *zonder* `.env` werkt voor
    unit-tests en lokale dry-runs; alleen `anthropic_api_key` moet voor
    live calls expliciet gezet zijn (de proxy faalt dan met een nette 401
    van Anthropic, niet met een crypto-error halverwege de pijplijn).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Provider (alleen via .env / env var; nooit in git) ---
    anthropic_api_key: str = ""

    # --- Databases (BR-G02: strikt gescheiden bestanden) ---
    content_db_path: Path = Path("./pylades-content.db")
    vault_db_path: Path = Path("./pylades-vault.db")

    # --- Crypto (BR-C01) ---
    global_secret_path: Path = Path("./secrets/global_secret.bin")

    # --- Detectie laag 3 (optioneel/eval-only; standaard uit, proxy/detection.py) ---
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3:1.7b"

    # --- Eval-only: Ollama laag 3 via MLX-backend (Apple Silicon).
    # Vereist `OLLAMA_MLX=1 ollama serve`; zie TESTPLAN.md §8 en runner
    # `pylades_md_ollama_mlx`. Niet door de runtime gebruikt.
    ollama_mlx_model: str = "qwen3.5:2b-nvfp4"

    # --- Eval-only: alternatieve laag-3-backend via MLX (Apple Silicon).
    # Niet door de runtime gebruikt; alleen het eval-harnas (TESTPLAN §8)
    # injecteert deze backend om Ollama vs MLX te vergelijken. Start de server
    # met: `uv run --with mlx-lm python -m mlx_lm.server --model <mlx_model> --port 8081`.
    mlx_host: str = "http://localhost:8081"
    mlx_model: str = "mlx-community/Qwen3-1.7B-4bit"

    # --- Eval-only legacy (runner pylades_lg); runtime laag 2 is DEDUCE ---
    spacy_model: str = "nl_core_news_md"

    # --- Server ports ---
    proxy_port: int = Field(default=8080, ge=1, le=65535)
    ui_port: int = Field(default=8501, ge=1, le=65535)


# Singleton — gebruik `from shared.config import settings`, niet `Settings()`.
# Dat voorkomt dat een tweede instance per ongeluk een gedeeltelijk
# geconfigureerd object terugkrijgt en houdt de "één bron"-belofte hard.
settings = Settings()
