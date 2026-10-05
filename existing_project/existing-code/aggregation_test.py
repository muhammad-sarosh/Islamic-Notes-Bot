import sys
import json
from collections import defaultdict

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
RETAIN_AFTER_BGE = 15
BGE_MAX_LENGTH = 512


# --------------------------------------------------
# Helper: longest consecutive lecture-chunk run
# --------------------------------------------------

def longest_consecutive_run(values):
    if not values:
        return 0

    values = sorted(set(values))

    best = 1
    current = 1

    for i in range(1, len(values)):
        if values[i] == values[i - 1] + 1:
            current += 1
            best = max(best, current)
        else:
            current = 1

    return best


# --------------------------------------------------
# Load E5
# --------------------------------------------------

print("Loading E5-base...")

e5_model = SentenceTransformer(E5_MODEL)
e5_tokenizer = e5_model.tokenizer


# --------------------------------------------------
# Read transcript and create 250-token chunks
# --------------------------------------------------

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

    chunk_ids = token_ids[
        start:start + LECTURE_CHUNK_TOKENS
    ]

    text = e5_tokenizer.decode(
        chunk_ids,
        skip_special_tokens=True
    ).strip()

    if text:
        lecture_chunks.append(text)

lecture_chunks = lecture_chunks[:TEST_CHUNKS]

print(f"Lecture chunks being tested: {len(lecture_chunks)}")


# --------------------------------------------------
# Load existing 400-token textbook index
# --------------------------------------------------

with open(INDEX_FILE, "r", encoding="utf-8") as f:
    textbook_items = json.load(f)

print(f"Textbook chunks: {len(textbook_items)}")

textbook_embeddings = np.array(
    [item["embedding"] for item in textbook_items],
    dtype=np.float32
)


# --------------------------------------------------
# Embed lecture chunks with E5
# --------------------------------------------------

print("Embedding lecture chunks...")

query_embeddings = e5_model.encode(
    [
        "query: " + text
        for text in lecture_chunks
    ],
    normalize_embeddings=True
)

e5_scores = query_embeddings @ textbook_embeddings.T


# --------------------------------------------------
# Load BGE reranker
# --------------------------------------------------

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


# --------------------------------------------------
# Reranker helper
# --------------------------------------------------

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


# --------------------------------------------------
# Aggregation storage
# --------------------------------------------------

aggregation = defaultdict(
    lambda: {
        "page": None,
        "chunk": None,
        "text": None,
        "lecture_chunks": [],
        "ranks": [],
        "bge_scores": [],
        "e5_ranks": []
    }
)


# --------------------------------------------------
# Retrieve and rerank each lecture chunk
# --------------------------------------------------

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

    retained = reranked[:RETAIN_AFTER_BGE]

    for bge_rank, (candidate, bge_score) in enumerate(
        retained,
        start=1
    ):

        key = (
            candidate["page"],
            candidate["chunk"]
        )

        record = aggregation[key]

        record["page"] = candidate["page"]
        record["chunk"] = candidate["chunk"]
        record["text"] = candidate["text"]

        record["lecture_chunks"].append(
            lecture_index
        )

        record["ranks"].append(
            bge_rank
        )

        record["bge_scores"].append(
            float(bge_score)
        )

        record["e5_ranks"].append(
            candidate["e5_rank"]
        )


# --------------------------------------------------
# Convert aggregation into summary rows
# --------------------------------------------------

summary = []

for record in aggregation.values():

    ranks = record["ranks"]
    lecture_matches = sorted(
        set(record["lecture_chunks"])
    )

    span = (
        max(lecture_matches)
        - min(lecture_matches)
        + 1
    )

    concentration = (
        len(lecture_matches) / span
    )

    summary.append({
        "page": record["page"],
        "chunk": record["chunk"],

        "best_rank": min(ranks),

        "avg_bge_rank": (
            sum(ranks) / len(ranks)
        ),

        "top3_support": sum(
            rank <= 3 for rank in ranks
        ),

        "top5_support": sum(
            rank <= 5 for rank in ranks
        ),

        "top10_support": sum(
            rank <= 10 for rank in ranks
        ),

        "top15_support": len(ranks),

        "lecture_chunks": lecture_matches,

        "longest_run": longest_consecutive_run(
            lecture_matches
        ),

        "span": span,

        "concentration": concentration,

        "best_bge_score": max(
            record["bge_scores"]
        ),

        "text": record["text"]
    })


# --------------------------------------------------
# Keep same basic sorting for now
# --------------------------------------------------

summary.sort(
    key=lambda x: (
        x["top15_support"],
        x["top10_support"],
        x["top5_support"],
        x["top3_support"],
        -x["best_rank"]
    ),
    reverse=True
)


# --------------------------------------------------
# Print aggregation results
# --------------------------------------------------

print("\n")
print("=" * 90)
print("AGGREGATED TEXTBOOK SUPPORT + LOCALITY")
print("=" * 90)

for i, item in enumerate(summary, start=1):

    print(
        f"\n{i}. PAGE {item['page']} "
        f"/ TEXTBOOK CHUNK {item['chunk']}"
    )

    print(
        f"   Best BGE rank:      "
        f"{item['best_rank']}"
    )

    print(
        f"   Average BGE rank:   "
        f"{item['avg_bge_rank']:.2f}"
    )

    print(
        f"   Top-3 support:      "
        f"{item['top3_support']}"
    )

    print(
        f"   Top-5 support:      "
        f"{item['top5_support']}"
    )

    print(
        f"   Top-10 support:     "
        f"{item['top10_support']}"
    )

    print(
        f"   Top-15 support:     "
        f"{item['top15_support']}"
    )

    print(
        f"   Lecture chunks:     "
        f"{item['lecture_chunks']}"
    )

    print(
        f"   Longest run:        "
        f"{item['longest_run']}"
    )

    print(
        f"   Match span:         "
        f"{item['span']}"
    )

    print(
        f"   Concentration:      "
        f"{item['concentration']:.2f}"
    )

    print(
        f"   Best BGE score:     "
        f"{item['best_bge_score']:.3f}"
    )

    preview = (
        item["text"]
        .replace("\n", " ")
        [:300]
    )

    print(
        f"   Text preview:       "
        f"{preview}"
    )
