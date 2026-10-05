import sys
import json
import numpy as np
from sentence_transformers import SentenceTransformer

transcript_file = sys.argv[1]
index_file = sys.argv[2]

MODEL_NAME = "intfloat/multilingual-e5-base"
TRANSCRIPT_CHUNK_TOKENS = 300

model = SentenceTransformer(MODEL_NAME)
tokenizer = model.tokenizer

with open(transcript_file, "r", encoding="utf-8") as f:
    transcript = f.read()

token_ids = tokenizer.encode(
    transcript,
    add_special_tokens=False,
    truncation=False,
    verbose=False
)

transcript_chunks = []

for start in range(0, len(token_ids), TRANSCRIPT_CHUNK_TOKENS):
    chunk_ids = token_ids[start:start + TRANSCRIPT_CHUNK_TOKENS]

    chunk_text = tokenizer.decode(
        chunk_ids,
        skip_special_tokens=True
    ).strip()

    if chunk_text:
        transcript_chunks.append(chunk_text)

query_embeddings = model.encode(
    ["query: " + chunk for chunk in transcript_chunks],
    normalize_embeddings=True
)

with open(index_file, "r", encoding="utf-8") as f:
    textbook_items = json.load(f)

textbook_embeddings = np.array(
    [item["embedding"] for item in textbook_items],
    dtype=np.float32
)

scores = query_embeddings @ textbook_embeddings.T

print(f"Transcript chunks: {len(transcript_chunks)}")
print()

for query_index in range(min(15, len(transcript_chunks))):

    row = scores[query_index]

    early_indexes = [
        i for i, item in enumerate(textbook_items)
        if item["page"] <= 4
    ]

    later_indexes = [
        i for i, item in enumerate(textbook_items)
        if item["page"] > 4
    ]

    best_early = max(
        early_indexes,
        key=lambda i: row[i]
    )

    best_later = max(
        later_indexes,
        key=lambda i: row[i]
    )

    early_item = textbook_items[best_early]
    later_item = textbook_items[best_later]

    print("=" * 80)
    print(f"LECTURE CHUNK {query_index + 1}")
    print(transcript_chunks[query_index][:250].replace("\n", " "))
    print()

    print(
        f"BEST PAGES 1-4: "
        f"Page {early_item['page']}, "
        f"Chunk {early_item['chunk']}, "
        f"Score {row[best_early]:.3f}"
    )

    print(
        f"BEST PAGES 5+:  "
        f"Page {later_item['page']}, "
        f"Chunk {later_item['chunk']}, "
        f"Score {row[best_later]:.3f}"
    )

    print()

