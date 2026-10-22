"""Brand identities and message templates; no files, credentials or network access."""

import re

IO_MARQUES = "io_marques"
MARQUES_BRECHO = "marques_brecho"
PROFILE_NAMES = {IO_MARQUES: "Io Marques", MARQUES_BRECHO: "Marques Brechó"}
MAX_MESSAGE_LENGTH = 10000
MESSAGE_TOKENS = frozenset({"cliente", "pecas", "total"})

IO_MESSAGE_TEMPLATE = "\n".join([
    "{cliente}", "", "Oi amada!😃", "Você arrematou na LIVE ",
    "as seguintes peças:", "{pecas}", "e ficou o total de {total}.", "",
    "👉 Você pode optar por fazer Pix",
    "👉 Ou link de pagamento para pagar no cartão de crédito.", "",
    "Espero seu retorno com brevidade.", "Pois temos muitas vezes fila nas peças.", "",
    "Aaaa não esqueça de mandar o comprovante.", " Combinado?😉",
])
MARQUES_MESSAGE_TEMPLATE = "\n".join([
    "{cliente}", "", "Olá! Você arrematou na live do Marques Brechó:",
    "{pecas}", "", "Total: {total}.", "",
    "O pagamento pode ser feito por Pix ou link de cartão de crédito.",
    "Responda esta mensagem para combinar o pagamento e envie o comprovante.",
    "Obrigado!",
])
DEFAULT_MESSAGES = {
    IO_MARQUES: IO_MESSAGE_TEMPLATE,
    MARQUES_BRECHO: MARQUES_MESSAGE_TEMPLATE,
}
MONOCHROME_COLORS = {
    "app_bg": "#F2F2F2", "primary": "#151515", "secondary": "#DEDEDE",
    "text": "#191919", "button_text": "#303030", "table_bg": "#FFFFFF",
    "table_header": "#E5E5E5", "table_header_text": "#202020", "grid": "#BDBDBD",
    "sold_row": "#E1E1E1", "active_border": "#151515", "timer_bg": "#FFFFFF",
    "warning_bg": "#E5E5E5", "warning_text": "#292929", "duplicate_cell": "#D5D5D5",
}


def normalize_profile(value):
    # Missing metadata belongs to the original brand; unknown IDs are not silently retagged.
    if value is None:
        return IO_MARQUES
    if not isinstance(value, str) or value not in PROFILE_NAMES:
        raise ValueError("Perfil da live inválido.")
    return value


def validate_message_template(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_MESSAGE_LENGTH:
        raise ValueError("A mensagem deve ter entre 1 e 10.000 caracteres.")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError("A mensagem contém caracteres inválidos.")
    tokens = re.findall(r"\{([^{}]*)\}", value)
    if set(tokens) - MESSAGE_TOKENS:
        raise ValueError("Use somente {cliente}, {pecas} e {total} na mensagem.")
    if "{pecas}" not in value or "{total}" not in value:
        raise ValueError("Mantenha {pecas} e {total} para preencher as compras automaticamente.")
    return value


def render_client_message(template, cliente, pecas, total):
    validate_message_template(template)
    handle = "@" + cliente.strip().lstrip("@").strip()
    values = {"cliente": handle, "pecas": pecas, "total": total}
    # One pass: user names or piece codes are data, never interpreted as template tokens.
    message = re.sub(r"\{(cliente|pecas|total)\}", lambda match: values[match.group(1)], template)
    return message if "{cliente}" in template else f"{handle}\n\n{message}"
