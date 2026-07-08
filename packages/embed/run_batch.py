"""Indexação em escala (skynet01): parse híbrido -> chunk -> embed -> Qdrant.

Cada host processa um shard dos documentos (``doc_id % num_shards == shard``) com
suas 2 GPUs (FlagEmbedding faz data-parallel automático). Os pontos têm ID
determinístico (``doc_id * 10_000 + chunk_index``), então re-execuções são
upserts idempotentes. Vetores vão ao Qdrant do ultron via rede.

    uv run python run_batch.py --data ../data --qdrant-url http://10.171.69.1:6333 \
        --shard 0 --num-shards 2
"""

from __future__ import annotations

import argparse
import gc
import time
from pathlib import Path

from qdrant_client import QdrantClient
from uff_core.catalog import Catalog
from uff_core.chunking import chunk_document
from uff_core.schemas import Chunk, DocStatus, Document, Source
from uff_embed.embedder import Bge
from uff_embed.index import ensure_collection, upsert
from uff_embed.router import needs_ocr, parse_any

CHUNKS_PER_DOC_STRIDE = 10_000


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


def run(args: argparse.Namespace) -> None:
    data = Path(args.data)
    catalog = Catalog(str(data / "catalog.db"))
    wanted = {Source(s) for s in args.sources.split(",")} if args.sources else None
    docs = [
        d
        for d in catalog.list_by_status(DocStatus.FETCHED)
        if (wanted is None or d.source in wanted)
        and d.id is not None
        and d.id % args.num_shards == args.shard
    ]
    print(f"[batch] shard {args.shard}/{args.num_shards}: {len(docs)} documentos")

    bge = Bge()
    client = QdrantClient(url=args.qdrant_url, timeout=60)
    ensure_collection(client, args.collection)

    done = errors = ocr_count = 0
    t_start = time.time()
    skipped = 0
    for n, doc in enumerate(docs, 1):
        matches = list((data / "raw" / doc.source.value).glob(f"{doc.id}.*"))
        if not matches:
            continue
        path = matches[0]
        # Idempotência entre passadas: se o 1º chunk do doc já está no Qdrant, pula.
        if client.retrieve(args.collection, ids=[doc.id * CHUNKS_PER_DOC_STRIDE]):
            catalog.set_status(doc.id, DocStatus.INDEXED)
            skipped += 1
            continue
        try:
            t0 = time.time()
            used_ocr = path.suffix.lower() == ".pdf" and needs_ocr(path)
            text = parse_any(path)
            chunks = chunk_document(doc, text, target_chars=1200, overlap_chars=150)
            if not chunks:
                catalog.set_status(doc.id, DocStatus.ERROR)
                errors += 1
                continue
            encoded = bge.encode([c.embedding_text for c in chunks], batch_size=args.batch_size)
            for chunk, enc in zip(chunks, encoded, strict=True):
                upsert(
                    client,
                    args.collection,
                    doc.id * CHUNKS_PER_DOC_STRIDE + chunk.index,
                    enc,
                    _payload(doc, chunk),
                )
            catalog.set_status(doc.id, DocStatus.INDEXED)
            done += 1
            ocr_count += int(used_ocr)
            if n % 25 == 0:
                gc.collect()  # hosts têm só 15GB de RAM; contém crescimento do heap
            if n % 10 == 0 or time.time() - t0 > 30:
                rate = n / (time.time() - t_start)
                eta_min = (len(docs) - n) / rate / 60 if rate > 0 else 0
                print(
                    f"[batch] {n}/{len(docs)} ok={done} err={errors} ocr={ocr_count} "
                    f"({rate:.2f} docs/s, ETA {eta_min:.0f}min)",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001 — batch não pode morrer por 1 doc
            catalog.set_status(doc.id, DocStatus.ERROR)
            errors += 1
            print(f"[batch] ERRO doc {doc.id}: {type(exc).__name__}: {exc}", flush=True)

    elapsed = (time.time() - t_start) / 60
    print(
        f"[batch] FIM shard {args.shard}: ok={done} skip={skipped} err={errors} "
        f"ocr={ocr_count} em {elapsed:.1f}min"
    )
    catalog.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data")
    ap.add_argument("--qdrant-url", default="http://10.171.69.1:6333")
    ap.add_argument("--collection", default="uff_chunks")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--sources", default=None, help="ex.: boletim,pesquisa (default: todas)")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
