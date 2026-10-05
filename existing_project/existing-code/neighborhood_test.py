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

PAGE_RADIUS = 2


# --------------------------------------------------
# Load E5
# --------------------------------------------------

print("Loading E5-base...")

e5_model = SentenceTransformer(E5_MODEL)
e5_tokenizer = e5_model.tokenizer


# --------------------------------------------------
# Read transcript
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
    chunk_ids = token_ids[start:start + LECTURE_CHUNK_TOKENS]

    chunk_text = e5_tokenizer.decode(
        chunk_ids,
        skip_special_tokens=True
    ).strip()

    if chunk_text:
        lecture_chunks.append(chunk_text)

lecture_chunks = lecture_chunks[:TEST_CHUNKS]

print(f"Lecture chunks: {len(lecture_chunks)}")


# --------------------------------------------------
# Load textbook index
# --------------------------------------------------

with open(INDEX_FILE, "r", encoding="utf-8") as f:
    textbook_items = json.load(f)

print(f"Textbook chunks: {len(textbook_items)}")

textbook_embeddings = np.array(
    [item["embedding"] for item in textbook_items],
    dtype=np.float32
)


# --------------------------------------------------
# Embed lecture chunks
# --------------------------------------------------

print("Embedding lecture chunks...")

query_embeddings = e5_model.encode(
    [
        "query: " + chunk
        for chunk in lecture_chunks
    ],
    normalize_embeddings=True
)

e5_scores = query_embeddings @ textbook_embeddings.T


# --------------------------------------------------
# Load reranker
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
# Store retained candidates per lecture chunk
# --------------------------------------------------

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
            "e5_rank": e5_rank
        })

    reranked = rerank(
        lecture_text,
        candidates
    )

    retained = []

    for bge_rank, (candidate, bge_score) in enumerate(
        reranked[:RETAIN_AFTER_BGE],
        start=1
    ):

        retained.append({
            "page": candidate["page"],
            "chunk": candidate["chunk"],
            "bge_rank": bge_rank,
            "bge_score": float(bge_score)
        })

    all_results.append(retained)


# --------------------------------------------------
# Build page-neighborhood support
# --------------------------------------------------

all_pages = sorted(
    set(item["page"] for item in textbook_items)
)

neighborhoods = []


for center_page in all_pages:

    start_page = center_page - PAGE_RADIUS
    end_page = center_page + PAGE_RADIUS

    lecture_hits = []
    best_ranks = []

    for lecture_index, results in enumerate(
        all_results,
        start=1
    ):

        matching = [
            result
            for result in results
            if start_page <= result["page"] <= end_page
        ]

        if not matching:
            continue

        best = min(
            matching,
            key=lambda x: x["bge_rank"]
        )

        lecture_hits.append(lecture_index)
        best_ranks.append(best["bge_rank"])

    if not lecture_hits:
        continue

    neighborhoods.append({
        "center": center_page,
        "start": start_page,
        "end": end_page,
        "lecture_hits": lecture_hits,
        "support": len(lecture_hits),
        "avg_rank": sum(best_ranks) / len(best_ranks),
        "best_rank": min(best_ranks)
    })


# --------------------------------------------------
# Sort by support, then rank quality
# --------------------------------------------------

neighborhoods.sort(
    key=lambda x: (
        x["support"],
        -x["avg_rank"],
        -x["best_rank"]
    ),
    reverse=True
)


# --------------------------------------------------
# Print overall neighborhood support
# --------------------------------------------------

print("\n")
print("=" * 100)
print("PAGE-NEIGHBORHOOD SUPPORT")
print("=" * 100)

for i, item in enumerate(neighborhoods[:25], start=1):

    print(
        f"\n{i}. Pages "
        f"{item['start']}–{item['end']} "
        f"(center {item['center']})"
    )

    print(
        f"   Lecture support: "
        f"{item['support']}/{len(lecture_chunks)}"
    )

    print(
        f"   Lecture chunks:  "
        f"{item['lecture_hits']}"
    )

    print(
        f"   Average rank:    "
        f"{item['avg_rank']:.2f}"
    )

    print(
        f"   Best rank:       "
        f"{item['best_rank']}"
    )


# --------------------------------------------------
# Show neighborhood candidates per lecture chunk
# --------------------------------------------------

print("\n")
print("=" * 100)
print("PER-LECTURE-CHUNK PAGE REGIONS")
print("=" * 100)

for lecture_index, results in enumerate(
    all_results,
    start=1
):

    page_scores = defaultdict(list)

    for result in results:
        page = result["page"]

        for center_page in range(
            page - PAGE_RADIUS,
            page + PAGE_RADIUS + 1
        ):
            page_scores[center_page].append(
                result["bge_rank"]
            )

    region_rows = []

    for center_page, ranks in page_scores.items():

        region_rows.append({
            "center": center_page,
            "start": center_page - PAGE_RADIUS,
            "end": center_page + PAGE_RADIUS,
            "best_rank": min(ranks),
            "support": len(ranks)
        })

    region_rows.sort(
        key=lambda x: (
            x["best_rank"],
            -x["support"]
        )
    )

    print(f"\nLECTURE CHUNK {lecture_index}")

    shown = 0

    for region in region_rows:

        if region["center"] < 1:
            continue

        print(
            f"  Pages "
            f"{region['start']}–{region['end']} | "
            f"best rank {region['best_rank']} | "
            f"candidate hits {region['support']}"
        )

        shown += 1

        if shown >= 5:
            break
