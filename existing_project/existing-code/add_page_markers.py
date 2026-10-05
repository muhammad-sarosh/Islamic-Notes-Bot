import sys

input_file = sys.argv[1]
output_file = sys.argv[2]

with open(input_file, "r", encoding="utf-8") as f:
    content = f.read()

pages = content.split("\f")

with open(output_file, "w", encoding="utf-8") as f:
    for i, page in enumerate(pages, start=1):
        page = page.strip()

        if not page:
            continue

        f.write(f"===== PAGE {i} =====\n")
        f.write(page)
        f.write("\n\n")
