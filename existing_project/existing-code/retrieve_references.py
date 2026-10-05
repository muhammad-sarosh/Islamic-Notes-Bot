import sys
import json
from collections import defaultdict

import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer, AutoModelForSequenceClassification


TRANSCRIPT_FILE = sys.argv[1]
INDEX_FILE = sys.argv[2]
OUTPUT_FILE = sys.argv[3]

E5_MODEL = "intfloat/multilingual-e5-base"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

LECTURE_CHUNK_TOKENS = 250
E5_CANDIDATES = 30

PRIMARY_TOP_N = 5
BACKUP_MAX_RANK = 15
MIN_BACKUP_RUN = 2

BGE_MAX_LENGTH = 512


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

print("Loading E5...")

e5_model = SentenceTransformer(E5_MODEL)
e5_tokenizer = e5_model.tokenizer


# --------------------------------------------------
# Read transcript and create lecture chunks
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

print(f"Lecture chunks: {len(lecture_chunks)}")


# --------------------------------------------------
# Load textbook index
# --------------------------------------------------

with open(INDEX_FILE, "r", encoding="utf-8") as f:
    textbook_items = json.load(f)

print(f"Textbook chunks: {len(textbook_items)}")

textbook_embeddings = np.array(
    [
        item["embedding"]
        for item in textbook_items
    ],
    dtype=np.float32
)


# --------------------------------------------------
# Embed lecture chunks
# --------------------------------------------------

print("Embedding lecture chunks...")

query_embeddings = e5_model.encode(
    [
        "query: " + text
        for text in lecture_chunks
    ],
    normalize_embeddings=True
)

e5_scores = (
    query_embeddings
    @ textbook_embeddings.T
)


# --------------------------------------------------
# Load BGE
# --------------------------------------------------

print("Loading BGE reranker...")

device = torch.device(
    "cuda"
    if torch.cuda.is_available()
    else "cpu"
)

print(f"Reranker device: {device}")

reranker_tokenizer = AutoTokenizer.from_pretrained(
    RERANKER_MODEL
)

reranker_model = (
    AutoModelForSequenceClassification
    .from_pretrained(RERANKER_MODEL)
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

    results = list(
        zip(candidates, scores)
    )

    results.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return results


# --------------------------------------------------
# Retrieve all lecture chunks
# --------------------------------------------------

all_results = []
backup_occurrences = defaultdict(list)


for lecture_index, lecture_text in enumerate(
    lecture_chunks,
    start=1
):

    print(
        f"Processing lecture chunk "
        f"{lecture_index}/{len(lecture_chunks)}"
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

    chunk_results = []

    for bge_rank, (candidate, score) in enumerate(
        reranked[:BACKUP_MAX_RANK],
        start=1
    ):

        result = {
            "page": candidate["page"],
            "chunk": candidate["chunk"],
            "text": candidate["text"],
            "e5_rank": candidate["e5_rank"],
            "bge_rank": bge_rank,
            "bge_score": float(score)
        }

        chunk_results.append(result)

        if bge_rank > PRIMARY_TOP_N:

            key = (
                candidate["page"],
                candidate["chunk"]
            )

            backup_occurrences[key].append(
                lecture_index
            )

    all_results.append(chunk_results)


# --------------------------------------------------
# Approve locally repeated backup references
# --------------------------------------------------

approved_backup_keys = set()

for key, lecture_matches in (
    backup_occurrences.items()
):

    run = longest_consecutive_run(
        lecture_matches
    )

    if run >= MIN_BACKUP_RUN:
        approved_backup_keys.add(key)


# --------------------------------------------------
# Determine references actually used
# --------------------------------------------------

used_keys = set()

for results in all_results:

    # Always retain BGE top 5.
    for result in results[:PRIMARY_TOP_N]:

        used_keys.add(
            (
                result["page"],
                result["chunk"]
            )
        )

    # Retain lower-ranked passages only if they
    # passed the adjacent-chunk backup test.
    for result in results[
        PRIMARY_TOP_N:
        BACKUP_MAX_RANK
    ]:

        key = (
            result["page"],
            result["chunk"]
        )

        if key in approved_backup_keys:
            used_keys.add(key)


# --------------------------------------------------
# Build unique reference library
# --------------------------------------------------

reference_library = {}

for item in textbook_items:

    key = (
        item["page"],
        item["chunk"]
    )

    if key in used_keys:
        reference_library[key] = {
            "page": item["page"],
            "chunk": item["chunk"],
            "text": item["text"]
        }


ordered_keys = sorted(
    reference_library.keys(),
    key=lambda x: (
        x[0],
        x[1]
    )
)


reference_ids = {}

for number, key in enumerate(
    ordered_keys,
    start=1
):

    reference_ids[key] = (
        f"REF-{number:03d}"
    )


# --------------------------------------------------
# Write compact Codex context
# --------------------------------------------------

with open(
    OUTPUT_FILE,
    "w",
    encoding="utf-8"
) as f:

    f.write(
        "LECTURE + TEXTBOOK RETRIEVAL CONTEXT\n\n"
    )

    f.write(
        "INSTRUCTIONS:\n"
    )

    f.write(
        "- The lecture transcript is the primary "
        "source.\n"
    )

    f.write(
        "- Textbook references are supporting "
        "material only.\n"
    )

    f.write(
        "- A high retrieval rank does not guarantee "
        "that a reference is relevant.\n"
    )

    f.write(
        "- Compare each candidate reference with "
        "the associated lecture chunk.\n"
    )

    f.write(
        "- Ignore references that do not clearly "
        "support material actually discussed.\n"
    )

    f.write(
        "- Never add a topic merely because it "
        "appears in a retrieved textbook reference.\n"
    )

    f.write(
        "- BACKUP references ranked below the top "
        "five but were retained because the same "
        "passage appeared across adjacent lecture "
        "chunks.\n\n"
    )


    # --------------------------------------------------
    # Lecture chunks and candidate IDs
    # --------------------------------------------------

    f.write("=" * 80 + "\n")
    f.write("LECTURE CHUNKS\n")
    f.write("=" * 80 + "\n\n")


    for lecture_index, lecture_text in enumerate(
        lecture_chunks,
        start=1
    ):

        results = all_results[
            lecture_index - 1
        ]

        f.write(
            f"### LECTURE CHUNK "
            f"{lecture_index}\n\n"
        )

        f.write("TRANSCRIPT:\n")
        f.write(
            lecture_text + "\n\n"
        )

        f.write(
            "CANDIDATE REFERENCES:\n"
        )


        # Primary top 5
        for result in results[
            :PRIMARY_TOP_N
        ]:

            key = (
                result["page"],
                result["chunk"]
            )

            ref_id = reference_ids[key]

            f.write(
                f"- {ref_id} | "
                f"BGE #{result['bge_rank']} | "
                f"Page {result['page']} | "
                f"Textbook chunk "
                f"{result['chunk']} | "
                f"E5 #{result['e5_rank']}\n"
            )


        # Approved backup references
        for result in results[
            PRIMARY_TOP_N:
            BACKUP_MAX_RANK
        ]:

            key = (
                result["page"],
                result["chunk"]
            )

            if key not in approved_backup_keys:
                continue

            ref_id = reference_ids[key]

            f.write(
                f"- {ref_id} | BACKUP | "
                f"BGE #{result['bge_rank']} | "
                f"Page {result['page']} | "
                f"Textbook chunk "
                f"{result['chunk']} | "
                f"E5 #{result['e5_rank']}\n"
            )

        f.write("\n")


    # --------------------------------------------------
    # Unique reference library
    # --------------------------------------------------

    f.write("\n")
    f.write("=" * 80 + "\n")
    f.write("TEXTBOOK REFERENCE LIBRARY\n")
    f.write("=" * 80 + "\n\n")


    for key in ordered_keys:

        item = reference_library[key]
        ref_id = reference_ids[key]

        f.write(
            f"### {ref_id}\n"
        )

        f.write(
            f"Page {item['page']} / "
            f"Textbook chunk {item['chunk']}\n\n"
        )

        f.write(
            item["text"] + "\n\n"
        )


print()

print(
    f"Lecture chunks written: "
    f"{len(lecture_chunks)}"
)

print(
    f"Unique textbook references: "
    f"{len(reference_library)}"
)

print(
    f"Approved repeated backup passages: "
    f"{len(approved_backup_keys)}"
)

print(
    f"Saved to: {OUTPUT_FILE}"
)	
