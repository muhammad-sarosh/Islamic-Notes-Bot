import sys
import json

import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer, AutoModelForSequenceClassification


TRANSCRIPT_FILE = sys.argv[1]
INDEX_FILE = sys.argv[2]

E5_MODEL = "intfloat/multilingual-e5-base"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

LECTURE_CHUNK_TOKENS = 250
TEST_CHUNKS = 18

E5_CANDIDATES = 30
SHOW_BGE_TOP = 10
BGE_MAX_LENGTH = 512


print("Loading E5-base...")

e5_model = SentenceTransformer(E5_MODEL)
e5_tokenizer = e5_model.tokenizer


with open(TRANSCRIPT_FILE, "r", encoding="utf-8") as f:
    transcript = f.read()


token_ids = e5_tokenizer.encode(
    transcript,
    add_special_tokens=False,
    truncation=False,
    verbose=False
)


lecture_chunks = []

for start in range(0, len(token_ids), LECTURE_CHUNK_TOKENS):
    chunk_ids = token_ids[start:start + LECTURE_CHUNK_TOKENS]

    chunk_text = e5_tokenizer.decode(
        chunk_ids,
        skip_special_tokens=True
    ).strip()

    if chunk_text:
        lecture_chunks.append(chunk_text)


lecture_chunks = lecture_chunks[:TEST_CHUNKS]

print(f"Lecture chunks: {len(lecture_chunks)}")


with open(INDEX_FILE, "r", encoding="utf-8") as f:
    textbook_items = json.load(f)


textbook_embeddings = np.array(
    [item["embedding"] for item in textbook_items],
    dtype=np.float32
)

print(f"Textbook chunks: {len(textbook_items)}")


print("Embedding lecture chunks...")

query_embeddings = e5_model.encode(
    [
        "query: " + chunk
        for chunk in lecture_chunks
    ],
    normalize_embeddings=True
)

e5_scores = query_embeddings @ textbook_embeddings.T


print("Loading BGE reranker...")

device = torch.device(
    "cuda" if torch.cuda.is_available() else "cpu"
)

print(f"Reranker device: {device}")

reranker_tokenizer = AutoTokenizer.from_pretrained(
    RERANKER_MODEL
)

reranker_model = AutoModelForSequenceClassification.from_pretrained(
    RERANKER_MODEL
)

reranker_model.to(device)
reranker_model.eval()


def rerank(query, candidates):

    pairs = [
        [query, candidate["text"]]
        for candidate in candidates
    ]

    inputs = reranker_tokenizer(
        pairs,
        padding=True,
        truncation=True,
        max_length=BGE_MAX_LENGTH,
        return_tensors="pt"
    )

    inputs = {
        key: value.to(device)
        for key, value in inputs.items()
    }

    with torch.no_grad():
        scores = (
            reranker_model(**inputs)
            .logits
            .squeeze(-1)
            .cpu()
            .tolist()
        )

    results = list(zip(candidates, scores))

    results.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return results


all_results = []


for lecture_index, lecture_text in enumerate(
    lecture_chunks,
    start=1
):

    print(
        f"\nProcessing lecture chunk "
        f"{lecture_index}/{len(lecture_chunks)}..."
    )

    row = e5_scores[lecture_index - 1]

    top_indexes = np.argsort(row)[
        -E5_CANDIDATES:
    ][::-1]

    candidates = []

    for e5_rank, textbook_index in enumerate(
        top_indexes,
        start=1
    ):

        item = textbook_items[textbook_index]

        candidates.append({
            "page": item["page"],
            "chunk": item["chunk"],
            "text": item["text"],
            "e5_rank": e5_rank,
            "e5_score": float(row[textbook_index])
        })

    reranked = rerank(
        lecture_text,
        candidates
    )

    chunk_results = []

    for bge_rank, (candidate, bge_score) in enumerate(
        reranked[:SHOW_BGE_TOP],
        start=1
    ):

        chunk_results.append({
            "bge_rank": bge_rank,
            "page": candidate["page"],
            "textbook_chunk": candidate["chunk"],
            "bge_score": float(bge_score),
            "e5_rank": candidate["e5_rank"],
            "e5_score": candidate["e5_score"]
        })

    all_results.append(chunk_results)


print("\n")
print("=" * 100)
print("SEQUENCE VIEW")
print("=" * 100)


for lecture_index, results in enumerate(
    all_results,
    start=1
):

    print(f"\nLECTURE CHUNK {lecture_index}")
    print("-" * 100)

    for result in results:

        print(
            f"BGE #{result['bge_rank']:>2} | "
            f"Page {result['page']:>2} "
            f"/ chunk {result['textbook_chunk']} | "
            f"BGE {result['bge_score']:>7.3f} | "
            f"E5 rank {result['e5_rank']:>2}"
        )


print("\n")
print("=" * 100)
print("TOP-3 PAGE PATH")
print("=" * 100)


for lecture_index, results in enumerate(
    all_results,
    start=1
):

    top_pages = []

    for result in results[:3]:
        page = result["page"]

        if page not in top_pages:
            top_pages.append(page)

    print(
        f"Lecture chunk {lecture_index:>2}: "
        f"{top_pages}"
    )
