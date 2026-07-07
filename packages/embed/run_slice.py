"""Fatia vertical (skynet02): parse -> chunk -> embed -> Qdrant -> query de prova.

Prova o sistema RAG completo sobre a amostra de Boletins já baixada. Uso:

    uv run python run_slice.py --data ../data --query "licença capacitação"
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from uff_core.catalog import Catalog
from uff_core.chunking import chunk_document
from uff_core.schemas import Chunk, DocStatus, Document, Source
from uff_embed.embedder import Bge
from uff_embed.index import ensure_collection, hybrid_search, open_local, upsert
from uff_embed.parse import parse_pdf

COLLECTION = "uff_chunks"


def _payload(doc: Document, chunk: Chunk) -> dict:
    return {
        "doc_id": doc.id,
        "source": doc.source.value,
        "numero": doc.numero,
        "publish_date": doc.publish_date.isoformat() if doc.publish_date else None,
        "url": doc.url,
        "chunk_index": chunk.index,
        "text": chunk.text,
        "context_prefix": chunk.context_prefix,
    }


def build_index(data_dir: str) -> None:
    data = Path(data_dir)
    catalog = Catalog(str(data / "catalog.db"))
    docs = [d for d in catalog.list_by_status(DocStatus.FETCHED) if d.source is Source.BOLETIM]
    print(f"[slice] {len(docs)} boletins FETCHED para processar")

    bge = Bge()
    client = open_local(str(data / "qdrant"))
    ensure_collection(client, COLLECTION)

    point_id = 0
    for doc in docs:
        pdf = data / "raw" / "boletim" / f"{doc.id}.pdf"
        if not pdf.exists():
            print(f"  [skip] {pdf} ausente")
            continue
        t0 = time.time()
        markdown = parse_pdf(pdf)
        chunks = chunk_document(doc, markdown, target_chars=1200, overlap_chars=150)
        if not chunks:
            print(f"  [warn] doc {doc.id} sem texto extraído")
            continue
        encoded = bge.encode([c.embedding_text for c in chunks])
        for chunk, enc in zip(chunks, encoded, strict=True):
            point_id += 1
            upsert(client, COLLECTION, point_id, enc, _payload(doc, chunk))
        catalog.set_status(doc.id, DocStatus.INDEXED)
        print(
            f"  [ok] doc {doc.id} nº{doc.numero} {doc.publish_date}: "
            f"{len(markdown)} chars -> {len(chunks)} chunks ({time.time() - t0:.1f}s)"
        )
    print(f"[slice] total de pontos no índice: {point_id}")
    catalog.close()
    client.close()  # storage local do Qdrant só admite uma instância por vez


def run_query(data_dir: str, query: str, limit: int) -> None:
    client = open_local(str(Path(data_dir) / "qdrant"))
    bge = Bge()
    enc = bge.encode_query(query)
    hits = hybrid_search(client, COLLECTION, enc, limit=limit)
    print(f"\n[query] {query!r} -> {len(hits)} resultados\n" + "=" * 70)
    for i, h in enumerate(hits, 1):
        p = h.payload
        print(f"#{i} (rrf={h.score:.4f}) Boletim nº{p['numero']} de {p['publish_date']}")
        print(f"    {p['url']}")
        snippet = " ".join(p["text"].split())[:280]
        print(f"    {snippet}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data", default="../data", help="diretório de dados (raw/, catalog.db, qdrant/)"
    )
    ap.add_argument("--query", default=None, help="se informado, roda só a busca")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--reindex", action="store_true", help="parse+embed+index antes de buscar")
    args = ap.parse_args()

    if args.query is None or args.reindex:
        build_index(args.data)
    if args.query:
        run_query(args.data, args.query, args.limit)


if __name__ == "__main__":
    main()
