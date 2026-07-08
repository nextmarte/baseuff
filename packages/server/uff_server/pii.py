"""Anonimização de PII na SAÍDA das tools (LGPD).

O texto cru (com CPF) permanece no índice Qdrant — necessário para o reranker e
auditável na base privada. Aqui mascaramos o CPF apenas no que é ENTREGUE ao agente/
cliente. Alvo preciso: CPF formatado (NNN.NNN.NNN-NN) e CPF cru de 11 dígitos quando
explicitamente rotulado (para não confundir com SIAPE/nº de processo).
"""

from __future__ import annotations

import re

MASK = "***.***.***-**"

_CPF_FMT = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_CPF_BARE_LABELED = re.compile(r"(CPF[\s:]*?)(\d{11})\b", re.IGNORECASE)


def mask_cpf(text: str | None) -> str | None:
    """Substitui CPFs por ``***.***.***-**``. Preserva ``None``/``""`` e não toca em
    números de processo ou matrículas SIAPE."""
    if not text:
        return text
    text = _CPF_FMT.sub(MASK, text)
    text = _CPF_BARE_LABELED.sub(lambda m: m.group(1) + MASK, text)
    return text
