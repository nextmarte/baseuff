"""Enriquece os artigos do STI_KB com OCR dos screenshots (roda no ultron, CPU).

Para cada HTML salvo em data/raw/sti_kb, baixa as imagens (galeriaImagens do
CITSmart, acessíveis como guest) e costura o texto extraído por OCR como
``[tela: …]`` — telas de sistema viram conteúdo pesquisável. Sobrescreve o HTML
para o pipeline de indexação (parse_any -> extract_html) já pegar o texto.

    uv run --with rapidocr-onnxruntime --with pillow --with numpy \
        python scripts/enrich_sti_kb.py
"""

from __future__ import annotations

import argparse
import io
import pathlib
import warnings

import httpx
from uff_core.config import Settings
from uff_ingest.ocr_enrich import enrich_html_with_ocr

warnings.filterwarnings("ignore")
CITSMART_BASE = (
    "https://citsmart.uff.br/citsmart/pages/knowledgeBasePortal/knowledgeBasePortal.load"
)

_engine = None


def _ocr(data: bytes) -> str:
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    import numpy as np
    from PIL import Image

    try:
        img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
    except Exception:
        return ""
    result, _elapsed = _engine(img)
    if not result:
        return ""
    return " ".join(line[1] for line in result).strip()


def run(data_dir: str) -> None:
    raw = pathlib.Path(data_dir) / "raw" / "sti_kb"
    files = sorted(raw.glob("*.html"))
    print(f"[ocr] {len(files)} artigos STI_KB para enriquecer")

    client = httpx.Client(
        headers={"User-Agent": "BaseUFF-crawler/0.1"},
        timeout=30,
        verify=False,
        follow_redirects=True,
    )

    def fetch(url: str) -> bytes | None:
        try:
            r = client.get(url)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                return r.content
        except httpx.HTTPError:
            return None
        return None

    enriched = imgs_ocr = 0
    for n, path in enumerate(files, 1):
        html = path.read_text(encoding="utf-8", errors="replace")
        if "<img" not in html:
            continue
        before = html.count("[tela:")
        out = enrich_html_with_ocr(html, base_url=CITSMART_BASE, fetch_image=fetch, ocr_image=_ocr)
        added = out.count("[tela:") - before
        if added > 0:
            path.write_text(out, encoding="utf-8")
            enriched += 1
            imgs_ocr += added
        if n % 25 == 0:
            print(
                f"[ocr] {n}/{len(files)} artigos, {enriched} enriquecidos, {imgs_ocr} telas",
                flush=True,
            )

    client.close()
    print(f"[ocr] FIM: {enriched} artigos enriquecidos, {imgs_ocr} telas com texto extraído")


def main() -> None:
    ap = argparse.ArgumentParser(description="OCR de screenshots dos tutoriais do STI")
    ap.add_argument("--data", default=None, help="diretório de dados (default: config)")
    args = ap.parse_args()
    data_dir = args.data or Settings().data_dir
    run(data_dir)


if __name__ == "__main__":
    main()
