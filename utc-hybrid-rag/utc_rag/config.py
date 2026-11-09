from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path = ROOT / ".env") -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


@dataclass
class Settings:
    data_dir: Path = Path(os.getenv("UTC_DATA_DIR", str(ROOT / "data")))
    source_root: Path = Path(os.getenv("UTC_SOURCE_ROOT", str(ROOT.parent / "Tailieu")))
    embedding_model: str = os.getenv("UTC_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    embedding_revision: str = os.getenv("UTC_EMBEDDING_REVISION", "main")
    model_cache: Path = ROOT / ".cache" / "huggingface"
    ollama_url: str = os.getenv("UTC_OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("UTC_OLLAMA_MODEL", "hf.co/Qwen/Qwen3-4B-GGUF:Q4_K_M")
    admin_token: str = os.getenv("UTC_ADMIN_TOKEN", "")
    timeout: float = float(os.getenv("UTC_LLM_TIMEOUT", "180"))
    chunk_size: int = 700
    overlap: int = 100
    context_window: int = 4096
    output_tokens: int = 512
    safety_tokens: int = 128

    def prepare(self):
        self.data_dir = self.data_dir.resolve()
        self.source_root = self.source_root.resolve()
        for name in ("snapshots", "staging/jobs", "datasets", "bundles", "traces"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)
        self.model_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(self.model_cache))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


settings = Settings()
