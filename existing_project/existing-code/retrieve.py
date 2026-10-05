import sys
import json
import numpy as np
from sentence_transformers import SentenceTransformer

transcript_file = sys.argv[1]
index_file = sys.argv[2]
output_file = sys.argv[3]

MODEL_NAME = "intfloat/multilingual-e5-base"

TRANSCRIPT_CHUNK_TOKENS = 300
TOP_MATCHES_PER_CHUNK = 3
FINAL_MATCHES = 15

model = SentenceTransformer(MODEL_NAME)
tokenizer = model.tokenizer


# Read transcript
with open(transcript_file, "r", encoding="utf-8") as f:
    transcript = f.read()


# Turn the whole transcript into token IDs
token_ids = tokenizer.encode(
    transcript,
    add_special_tokens=False,
    truncation=False,
    verbose=False
)


# Split transcript into chunks
transcript_chunks = []

for start in range(0, len(token_ids), TRANSCRIPT_CHUNK_TOKENS):
    chunk_ids = token_ids[start:start + TRANSCRIPT_CHUNK_TOKENS]

    chunk_text = tokenizer.decode(
        chunk_ids,
        skip_special_tokens=True
    ).strip()

    if chunk_text:
        transcript_chunks.append(chunk_text)


# Embed all transcript chunks
query_texts = [
    "query: " + chunk
    for chunk in transcript_chunks
]

query_embeddings = model.encode(
    query_texts,
    normalize_embeddings=True
)


# Load textbook index
with open(index_file, "r", encoding="utf-8") as f:
    textbook_items = json.load(f)

textbook_embeddings = np.array(
    [item["embedding"] for item in textbook_items],
    dtype=np.float32
)


# Compare every transcript chunk against every textbook chunk
scores = query_embeddings @ textbook_embeddings.T


# Keep each textbook chunk's best score
best_matches = {}

for query_index in range(len(transcript_chunks)):

    row = scores[query_index]

    top_indexes = np.argsort(row)[-TOP_MATCHES_PER_CHUNK:][::-1]

    for textbook_index in top_indexes:
        score = float(row[textbook_index])

        if (
            textbook_index not in best_matches
            or score > best_matches[textbook_index]
        ):
            best_matches[textbook_index] = score


# Sort strongest matches overall
ranked_matches = sorted(
    best_matches.items(),
    key=lambda item: item[1],
    reverse=True
)

ranked_matches = ranked_matches[:FINAL_MATCHES]


# Write retrieved textbook material
with open(output_file, "w", encoding="utf-8") as f:

    for textbook_index, score in ranked_matches:
        item = textbook_items[textbook_index]

        f.write(
            f"===== PAGE {item['page']} | "
            f"CHUNK {item['chunk']} | "
            f"SCORE {score:.3f} =====\n"
        )

        f.write(item["text"])
        f.write("\n\n")


print(f"Transcript chunks: {len(transcript_chunks)}")
print(f"Retrieved textbook chunks: {len(ranked_matches)}")
print(f"Saved to: {output_file}")
