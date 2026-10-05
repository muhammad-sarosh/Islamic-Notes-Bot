# Islamic Notes Bot

Migration reference files for replacing the existing local n8n lecture-note automation with a personal Discord bot and web review application.

## Existing workflow exports

- `Create Notes.json`: creates a run directory, downloads YouTube audio, transcribes it, retrieves textbook references, generates notes through Codex CLI, copies drafts to Windows, and sends a confirmation.
- `Send Notes.json`: reads the reviewed Markdown, splits it into Discord messages, and publishes using course-specific destinations with a one-second delay between messages.
- `Monitor Discord (Creating).json`: polls Discord, parses creation commands, tracks message IDs, and invokes generation.
- `Monitor Discord (Sending).json`: polls Discord, parses publishing commands, tracks message IDs, and invokes publishing.

Embedded Discord tokens and webhook URLs in these exports have been replaced with placeholders. The source n8n installation was not modified.

## Copied source and assets

`existing-code/` is a snapshot of `/home/sushi/lecture-tools/` in the Ubuntu WSL distribution. It contains 14 Python scripts, five prompt files, and 22 textbook assets (PDFs, extracted text, page-marked text, and current/experimental indexes).

Key files:

- `transcribe.py`: local CUDA Faster-Whisper transcription.
- `retrieve_references.py`: transcript chunking, multilingual E5 candidate retrieval, BGE reranking, and reference selection.
- `build_index.py` and `build_index_300.py`: textbook indexing utilities.
- `prompts/`: shared note-generation rules, Discord formatting, and course instructions.
- `course-pdfs/`: Fiqh, Aqeedah, Adaab, and Mustalah reference material and indexes. Older indexes and research scripts are retained to explain prior experiments; they are not all production dependencies.

## Examples

Two complete runs were copied from `/home/sushi/lecture-work/`:

- `examples/fiqh/lecture-1-1-run-1389/`
- `examples/mustalah/lecture-2-2-run-473/`

Each includes the transcript, retrieval context, and generated notes. `examples/reviewed-notes/` contains the three available Markdown files from `S:\Miscellaneous\Lecture-Notes`. That directory represents the human-review stage, though individual files may be identical to the generated drafts.

Audio, incomplete runs, model environments/caches, and the n8n database were omitted. Original source files were left unchanged. Copied scripts have not been executed; they retain their original environment assumptions and paths.

## Agreed direction

Build a Discord bot as the primary application, with slash commands, persistent job state, background processing, custom RAG, webpage review/editing, and explicit publishing to Discord. Use the existing 9router setup for generation. Keep additional services free and resource usage low on the shared four-core, 8 GB VPS used by five people. Transcription and embedding/reranking providers still need to be selected and validated. n8n may remain useful for peripheral maintenance.

The transcript remains the primary source for notes; textbook retrieval supplies supporting context. Human review remains a separate step before publication.
