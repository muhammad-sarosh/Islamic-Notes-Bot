import sys
import json
from sentence_transformers import SentenceTransformer

input_file = sys.argv[1]
output_file = sys.argv[2]

model = SentenceTransformer("intfloat/multilingual-e5-base")
tokenizer = model.tokenizer

MAX_TOKENS = 300

with open(input_file, "r", encoding="utf-8") as f:
    content = f.read()

sections = content.split("===== PAGE ")

items = []

for section in sections:
    section = section.strip()

    if not section:
        continue

    first_line, *rest = section.splitlines()

    page_number = first_line.replace("=====", "").strip()
    page_text = "\n".join(rest).strip()

    if not page_text:
        continue

    token_ids = tokenizer.encode(
        page_text,
        add_special_tokens=False,
	truncation=False,
	verbose=False
    )

    chunk_number = 1

    for start in range(0, len(token_ids), MAX_TOKENS):
        chunk_ids = token_ids[start:start + MAX_TOKENS]

        chunk_text = tokenizer.decode(
            chunk_ids,
            skip_special_tokens=True
        ).strip()

        if not chunk_text:
            continue

        embedding = model.encode(
            "passage: " + chunk_text,
            normalize_embeddings=True
        )

        items.append({
            "page": int(page_number),
            "chunk": chunk_number,
            "text": chunk_text,
            "embedding": embedding.tolist()
        })

        chunk_number += 1

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False)

print(f"Indexed {len(items)} chunks")
print(f"Saved to: {output_file}")
