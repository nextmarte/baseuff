"""Ativa quantização escalar int8 nos vetores densos da coleção (sem re-embed).

Reduz ~4× a RAM dos vetores densos (392k × 1024 × 4B ≈ 1,6GB → ~0,4GB) e pode
acelerar a busca; a acurácia é preservada por rescoring com os vetores originais.
O Qdrant re-otimiza em background. Uso:

    uv run python scripts/quantize.py           # ativa
    uv run python scripts/quantize.py --off      # desativa
"""

from __future__ import annotations

import argparse

from qdrant_client import QdrantClient, models
from uff_core.config import Settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--off", action="store_true", help="desativa a quantização")
    args = ap.parse_args()

    s = Settings()
    client = QdrantClient(url=s.qdrant_url, timeout=180)
    coll = s.qdrant_collection

    if args.off:
        client.update_collection(coll, quantization_config=models.Disabled())
        print(f"[quant] quantização DESATIVADA em {coll}")
        return

    client.update_collection(
        coll,
        quantization_config=models.ScalarQuantization(
            scalar=models.ScalarQuantizationConfig(
                type=models.ScalarType.INT8,
                always_ram=True,  # mantém os códigos int8 em RAM (busca rápida)
            )
        ),
    )
    info = client.get_collection(coll)
    print(f"[quant] int8 ativada em {coll} (status={info.status}); Qdrant re-otimiza em background")


if __name__ == "__main__":
    main()
