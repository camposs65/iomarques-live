"""Export only the public connection settings required by the installed app."""

import argparse
import json
from pathlib import Path

from supabase_sync import _atomic_write, load_sync_config


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist" / "integracao_supabase.json"


def load_values(root=ROOT):
    """Use overrides first, then the public configuration included in a ZIP."""
    root = Path(root)
    config = load_sync_config(root, root)
    if config is None:
        raise ValueError(
            "Configuração pública do Supabase ausente ou inválida. "
            "São necessários uma URL HTTPS, uma chave publicável/anon e o e-mail-base. "
            "Chaves secretas, service_role e sessões de usuário não são permitidas."
        )
    return {
        "supabase_url": config.url,
        "supabase_publishable_key": config.publishable_key,
        "auth_email_base": config.auth_email_base,
    }


def write_config(output=OUTPUT, root=ROOT):
    """Write an atomic allowlisted config; never copy sessions or credentials."""
    output = Path(output)
    values = load_values(root)
    _atomic_write(output, json.dumps(values, ensure_ascii=False, indent=2) + "\n")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    try:
        output = write_config(args.output)
    except (ValueError, OSError) as exc:
        print(f"Não foi possível preparar a integração: {exc}")
        return 1
    print(f"Configuração pública preparada em: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
