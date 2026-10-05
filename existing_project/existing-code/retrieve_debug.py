import sys
import json
import numpy as np
from sentence_transformers import SentenceTransformer

transcript_file = sys.argv[1]
index_file = sys.argv[2]

MODEL_NAME = "intfloat/multilingual-e5-small"
TRANSCRIPT_CHUNK_TOKENS = 300
TOP_MATCHES = 3

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

query_texts = [
    "query: " + chunk
    for chunk in transcript_chunks
]

query_embeddings = model.encode(
    query_texts,
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
print(f"Textbook chunks: {len(textbook_items)}")
print()

for query_index, chunk_text in enumerate(transcript_chunks):

    row = scores[query_index]

    top_indexes = np.argsort(row)[-TOP_MATCHES:][::-1]

    print("=" * 80)
    print(f"LECTURE CHUNK {query_index + 1}")
    print("-" * 80)
    print(chunk_text[:200].replace("\n", " "))
    print()
    print("TOP TEXTBOOK MATCHES:")

    for rank, textbook_index in enumerate(top_indexes, start=1):
        item = textbook_items[textbook_index]
        score = float(row[textbook_index])

        print(
            f"{rank}. Page {item['page']}, "
            f"Chunk {item['chunk']}, "
            f"Score {score:.3f}"
        )

        print(
            f"   {item['text'][:150].replace(chr(10), ' ')}"
        )

    print()

