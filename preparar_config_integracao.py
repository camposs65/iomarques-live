import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist" / "integracao_supabase.json"
LOCAL_CONFIG = ROOT / "integracao_supabase.local.json"
BRECHO_ENV = ROOT.parent / "brecho-controle" / ".env.local"


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_dotenv(path):
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_values():
    environment = {
        "supabase_url": os.environ.get("IOMARQUES_SUPABASE_URL", ""),
        "supabase_publishable_key": os.environ.get(
            "IOMARQUES_SUPABASE_PUBLISHABLE_KEY", ""
        ),
        "auth_email_base": os.environ.get("IOMARQUES_AUTH_EMAIL_BASE", ""),
    }
    if all(environment.values()):
        return environment

    local = read_json(LOCAL_CONFIG)
    if all(local.get(key) for key in environment):
        return {key: str(local[key]).strip() for key in environment}

    dotenv = read_dotenv(BRECHO_ENV)
    return {
        "supabase_url": dotenv.get("VITE_SUPABASE_URL", ""),
        "supabase_publishable_key": dotenv.get(
            "VITE_SUPABASE_PUBLISHABLE_KEY", ""
        ),
        "auth_email_base": dotenv.get("VITE_AUTH_EMAIL_BASE", ""),
    }


def main():
    values = load_values()
    values = {key: str(value or "").strip() for key, value in values.items()}
    valid = (
        values["supabase_url"].startswith("https://")
        and bool(values["supabase_publishable_key"])
        and "@" in values["auth_email_base"]
    )
    if not valid:
        print(
            "Aviso: integração não configurada. Crie integracao_supabase.local.json "
            "ou defina as variáveis IOMARQUES_SUPABASE_* antes de gerar o app."
        )
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(values, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Configuração local criada em: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
