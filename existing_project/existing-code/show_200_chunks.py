import sys
from sentence_transformers import SentenceTransformer

transcript_file = sys.argv[1]

MODEL_NAME = "intfloat/multilingual-e5-base"
CHUNK_TOKENS = 250

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

chunks = []

for start in range(0, len(token_ids), CHUNK_TOKENS):
    chunk_ids = token_ids[start:start + CHUNK_TOKENS]

    text = tokenizer.decode(
        chunk_ids,
        skip_special_tokens=True
    ).strip()

    if text:
        chunks.append(text)

print(f"Total chunks: {len(chunks)}")

for i, chunk in enumerate(chunks[:18], start=1):
    print("\n" + "=" * 80)
    print(f"CHUNK {i}")
    print("=" * 80)
    print(chunk)
