import sys
import json
import numpy as np
import torch

from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer, AutoModelForSequenceClassification


transcript_file = sys.argv[1]
index_file = sys.argv[2]

E5_MODEL = "intfloat/multilingual-e5-base"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

TRANSCRIPT_CHUNK_TOKENS = 250
E5_CANDIDATES = 30
TEST_CHUNKS = 18
SHOW_RESULTS = 5


# Ground truth from manually comparing lecture to textbook.
# A chunk may legitimately correspond to more than one page.
EXPECTED_PAGES = {
    1: {1},          # mostly course/lecture introduction
    2: {1},          # definition of nikah
    3: {1, 32},      # definition + beginning of three-divorce example
    4: {32},         # halala discussion
    5: {32},         # halala discussion
    6: {1},          # returns to definitions + legitimacy
    7: {1},          # Qur'anic evidence
    8: {1},          # Ibn Mas'ud hadith
    9: {1, 2},       # end of evidence + loving/fertile hadith / ijma
    10: {2},         # transition into wisdoms
    11: {2},         # wisdoms
    12: {2},         # wisdoms
    13: {2},         # wisdoms
    14: {2, 3},      # final wisdoms + transition to rulings
    15: {3},         # five legal rulings
    16: {3},         # recommended / obligatory
    17: {3},         # obligatory / disliked
    18: {3},         # disliked / forbidden
}


# --------------------------------------------------
# Load E5
# --------------------------------------------------

print("Loading E5-base...")

e5_model = SentenceTransformer(E5_MODEL)
e5_tokenizer = e5_model.tokenizer


# --------------------------------------------------
# Read and chunk transcript
# --------------------------------------------------

with open(transcript_file, "r", encoding="utf-8") as f:
    transcript = f.read()

token_ids = e5_tokenizer.encode(
    transcript,
    add_special_tokens=False,
    truncation=False,
    verbose=False
)

transcript_chunks = []

for start in range(0, len(token_ids), TRANSCRIPT_CHUNK_TOKENS):

    chunk_ids = token_ids[
        start:start + TRANSCRIPT_CHUNK_TOKENS
    ]

    chunk_text = e5_tokenizer.decode(
        chunk_ids,
        skip_special_tokens=True
    ).strip()

    if chunk_text:
        transcript_chunks.append(chunk_text)

transcript_chunks = transcript_chunks[:TEST_CHUNKS]

print(f"Transcript chunks being tested: {len(transcript_chunks)}")


# --------------------------------------------------
# Load textbook index
#----------------------------------------------

with open(index_file, "r", encoding="utf-8") as f:
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

query_texts = [
    "query: " + chunk
    for chunk in transcript_chunks
]

query_embeddings = e5_model.encode(
    query_texts,
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


# --------------------------------------------------
# Rerank helper
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
        max_length=512,
        return_tensors="pt"
    )

    inputs = {
        key: value.to(device)
        for key, value in inputs.items()
    }

    with torch.no_grad():
        outputs = reranker_model(**inputs)

    scores = outputs.logits.squeeze(-1).cpu().tolist()

    ranked = list(zip(candidates, scores))

    ranked.sort(
        key=lambda item: item[1],
        reverse=True
    )

    return ranked


# --------------------------------------------------
# Counters
# --------------------------------------------------

e5_recall20 = 0
e5_top1 = 0
e5_top5 = 0

bge_top1 = 0
bge_top5 = 0

e5_misses = []
bge_failures = []


# --------------------------------------------------
# Evaluate
# --------------------------------------------------

for query_index, query_text in enumerate(transcript_chunks):

    lecture_number = query_index + 1
    expected_pages = EXPECTED_PAGES[lecture_number]

    print("\n" + "=" * 80)
    print(f"LECTURE CHUNK {lecture_number}")
    print(f"EXPECTED PAGE(S): {sorted(expected_pages)}")
    print("=" * 80)

    row = e5_scores[query_index]

    candidate_indexes = np.argsort(row)[
        -E5_CANDIDATES:
    ][::-1]

    candidates = []

    for e5_rank, textbook_index in enumerate(
        candidate_indexes,
        start=1
    ):

        item = textbook_items[textbook_index]

        candidates.append({
            "page": item["page"],
            "chunk": item["chunk"],
            "text": item["text"],
            "e5_score": float(row[textbook_index]),
            "e5_rank": e5_rank
        })


    # --------------------------------------------------
    # E5 top results
    # --------------------------------------------------

    print("\nE5 TOP 5:")

    for candidate in candidates[:SHOW_RESULTS]:

        marker = (
            "EXPECTED"
            if candidate["page"] in expected_pages
            else ""
        )

        print(
            f"{candidate['e5_rank']}. "
            f"Page {candidate['page']}, "
            f"Chunk {candidate['chunk']}, "
            f"Score {candidate['e5_score']:.3f} "
            f"{marker}"
        )


    # Best expected E5 candidate
    expected_e5 = [
        candidate
        for candidate in candidates
        if candidate["page"] in expected_pages
    ]

    if expected_e5:

        best = expected_e5[0]

        e5_recall20 += 1

        if best["e5_rank"] == 1:
            e5_top1 += 1

        if best["e5_rank"] <= 5:
            e5_top5 += 1

        print(
            "\nBEST EXPECTED E5 RESULT: "
            f"rank {best['e5_rank']}, "
            f"Page {best['page']}, "
            f"Chunk {best['chunk']}, "
            f"Score {best['e5_score']:.3f}"
        )

    else:

        e5_misses.append(lecture_number)

        print(
            "\nBEST EXPECTED E5 RESULT: "
            "NOT FOUND IN TOP 20"
        )


    # --------------------------------------------------
    # BGE reranking
    # --------------------------------------------------

    reranked = rerank(
        query_text,
        candidates
    )

    print("\nBGE TOP 5:")

    for rank, (candidate, bge_score) in enumerate(
        reranked[:SHOW_RESULTS],
        start=1
    ):

        marker = (
            "EXPECTED"
            if candidate["page"] in expected_pages
            else ""
        )

        print(
            f"{rank}. "
            f"Page {candidate['page']}, "
            f"Chunk {candidate['chunk']}, "
            f"BGE {bge_score:.3f} "
            f"(E5 rank {candidate['e5_rank']}) "
            f"{marker}"
        )


    best_bge = None

    for rank, (candidate, bge_score) in enumerate(
        reranked,
        start=1
    ):

        if candidate["page"] in expected_pages:
            best_bge = (
                rank,
                candidate,
                bge_score
            )
            break


    if best_bge:

        rank, candidate, bge_score = best_bge

        if rank == 1:
            bge_top1 += 1

        if rank <= 5:
            bge_top5 += 1

        if rank != 1:
            bge_failures.append(
                (
                    lecture_number,
                    rank,
                    candidate["page"],
                    candidate["chunk"]
                )
            )

        print(
            "\nBEST EXPECTED AFTER BGE: "
            f"rank {rank}, "
            f"Page {candidate['page']}, "
            f"Chunk {candidate['chunk']}, "
            f"BGE {bge_score:.3f}, "
            f"E5 rank {candidate['e5_rank']}"
        )

    else:

        print(
            "\nBEST EXPECTED AFTER BGE: "
            "NOT AVAILABLE — E5 DID NOT RETRIEVE IT"
        )


# --------------------------------------------------
# Final summary
# --------------------------------------------------

total = len(transcript_chunks)

print("\n" + "=" * 80)
print("FINAL GROUND-TRUTH SUMMARY")
print("=" * 80)

print(
    f"E5 recall@30:  {e5_recall20}/{total} "
    f"({e5_recall20 / total * 100:.1f}%)"
)

print(
    f"E5 top-5:      {e5_top5}/{total} "
    f"({e5_top5 / total * 100:.1f}%)"
)

print(
    f"E5 top-1:      {e5_top1}/{total} "
    f"({e5_top1 / total * 100:.1f}%)"
)

print()

print(
    f"BGE top-5:     {bge_top5}/{total} "
    f"({bge_top5 / total * 100:.1f}%)"
)

print(
    f"BGE top-1:     {bge_top1}/{total} "
    f"({bge_top1 / total * 100:.1f}%)"
)

print("\nE5 FAILED TO RETRIEVE EXPECTED PAGE IN TOP 30:")
print(
    ", ".join(map(str, e5_misses))
    if e5_misses
    else "None"
)

print("\nBGE HAD EXPECTED PAGE AVAILABLE BUT DID NOT RANK IT #1:")

if bge_failures:

    for lecture, rank, page, chunk in bge_failures:
        print(
            f"Chunk {lecture}: "
            f"rank {rank}, "
            f"Page {page}, "
            f"Chunk {chunk}"
        )

else:
    print("None")
