"""Isolated ML stages. Models exit with their process; no ML imports in the web service."""

import json
import sys
from collections import defaultdict
from pathlib import Path

from notes_bot.domain import textbook_pages

E5_MODEL = "intfloat/multilingual-e5-base"
BGE_MODEL = "BAAI/bge-reranker-v2-m3"


def report(text):
    print(json.dumps({"progress": text}), flush=True)


def consecutive(values):
    ordered = sorted(set(values))
    longest = run = 0
    previous = None
    for value in ordered:
        run = run + 1 if previous is not None and value == previous + 1 else 1
        longest = max(longest, run)
        previous = value
    return longest


def context_from_results(chunks, results):
    """Preserve the previous pipeline's top-five plus adjacent-backup selection."""
    backups = defaultdict(list)
    for i, row in enumerate(results):
        for item in row[5:15]:
            backups[(item["page"], item["chunk"])].append(i)
    approved = {key for key, occurrences in backups.items() if consecutive(occurrences) >= 2}
    selected = [
        [item for item in row[:15] if item["bge_rank"] <= 5 or (item["page"], item["chunk"]) in approved]
        for row in results
    ]
    library = {(item["page"], item["chunk"]): item for row in selected for item in row}
    ids = {key: f"REF-{i:03d}" for i, key in enumerate(sorted(library), 1)}
    lines = [
        "LECTURE + TEXTBOOK RETRIEVAL CONTEXT",
        "The transcript is the primary source. Textbook passages are supporting material only.",
        "Ranks are relevance signals, not proof. Ignore irrelevant references and never add a topic",
        "only because it appears in the textbook. BACKUP passages recur in adjacent lecture chunks.",
        "",
    ]
    for i, (text, row) in enumerate(zip(chunks, selected, strict=True), 1):
        lines.extend([f"### LECTURE CHUNK {i}", "TRANSCRIPT:", text, "CANDIDATE REFERENCES:"])
        for item in row:
            ref = ids[(item["page"], item["chunk"])]
            label = " | BACKUP" if item["bge_rank"] > 5 else ""
            lines.append(
                f"- {ref}{label} | BGE #{item['bge_rank']} | Page {item['page']} | "
                f"Textbook chunk {item['chunk']} | E5 #{item['e5_rank']}"
            )
        lines.append("")
    lines.append("TEXTBOOK REFERENCE LIBRARY")
    for key in sorted(library):
        item = library[key]
        lines.extend([f"### {ids[key]}", f"Page {key[0]} / Textbook chunk {key[1]}", item["text"], ""])
    return "\n\n".join(lines)


def main():
    import numpy as np
    import torch

    torch.set_num_threads(1)
    mode, input_file, output_file = sys.argv[1:]
    data = json.loads(Path(input_file).read_text(encoding="utf-8"))
    if mode in {"index", "candidates"}:
        from sentence_transformers import SentenceTransformer

        report("Loading E5")
        model = SentenceTransformer(E5_MODEL, device="cpu")
        tokenizer = model.tokenizer
        if mode == "index":
            items = []
            pages = textbook_pages(data["text"])
            for page_index, (page, text) in enumerate(pages, 1):
                tokens = tokenizer.encode(text, add_special_tokens=False, truncation=False, verbose=False)
                for number, start in enumerate(range(0, len(tokens), 400), 1):
                    chunk = tokenizer.decode(tokens[start : start + 400], skip_special_tokens=True).strip()
                    if chunk:
                        items.append({"page": page, "chunk": number, "text": chunk})
                report(f"Chunking textbook — page {page_index}/{len(pages)}")
            for start in range(0, len(items), 8):
                batch = items[start : start + 8]
                vectors = model.encode(
                    ["passage: " + x["text"] for x in batch], batch_size=8, normalize_embeddings=True
                )
                for item, vector in zip(batch, vectors, strict=True):
                    item["embedding"] = vector.tolist()
                report(f"Embedding textbook — {min(start + 8, len(items))}/{len(items)} chunks")
            result = items
        else:
            tokens = tokenizer.encode(
                data["transcript"], add_special_tokens=False, truncation=False, verbose=False
            )
            chunks = [
                tokenizer.decode(tokens[i : i + 250], skip_special_tokens=True).strip()
                for i in range(0, len(tokens), 250)
            ]
            chunks = [chunk for chunk in chunks if chunk]
            matrix = np.array([x["embedding"] for x in data["items"]], dtype=np.float32)
            candidates = []
            for i, chunk in enumerate(chunks, 1):
                query = model.encode("query: " + chunk, normalize_embeddings=True)
                indexes = np.argsort(query @ matrix.T)[-30:][::-1]
                candidates.append(
                    [
                        {
                            **{k: v for k, v in data["items"][int(j)].items() if k != "embedding"},
                            "e5_rank": rank,
                        }
                        for rank, j in enumerate(indexes, 1)
                    ]
                )
                report(f"E5 retrieval — {i}/{len(chunks)} transcript chunks")
            result = {"chunks": chunks, "candidates": candidates}
    elif mode == "rerank":
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        report("Loading BGE reranker")
        tokenizer = AutoTokenizer.from_pretrained(BGE_MODEL)
        model = AutoModelForSequenceClassification.from_pretrained(BGE_MODEL).eval()
        results = []
        for i, (chunk, row) in enumerate(zip(data["chunks"], data["candidates"], strict=True), 1):
            scores = []
            for start in range(0, len(row), 2):
                pairs = [[chunk, x["text"]] for x in row[start : start + 2]]
                inputs = tokenizer(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt")
                with torch.inference_mode():
                    scores.extend(model(**inputs).logits.reshape(-1).tolist())
            ranked = sorted(zip(row, scores, strict=True), key=lambda x: x[1], reverse=True)
            results.append(
                [
                    {**item, "bge_rank": rank, "bge_score": float(score)}
                    for rank, (item, score) in enumerate(ranked[:15], 1)
                ]
            )
            report(f"BGE reranking — {i}/{len(data['chunks'])} transcript chunks")
        result = context_from_results(data["chunks"], results)
    else:
        raise ValueError("Unknown ML stage")
    Path(output_file).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
