# Project guidance

## Agreed architecture

- Build a personal Discord bot with a web interface, deployed through Coolify on a shared VPS with four CPU cores and 8 GB RAM used by five people.
- Reuse the shared PostgreSQL server with a dedicated application database and role. Use persistent volume storage for uploaded textbook text, model caches, and temporary audio where needed.
- Use local yt-dlp, API transcription with whisper-large-v3-turbo, and the existing 9router setup for generation.
- Keep multilingual E5 embeddings and BGE reranking local. Do not run CPU benchmarks now. If resource usage later becomes excessive, reconsider hosted alternatives. An embedding-model change requires re-indexing; a Gemini embedding model does not itself replace BGE reranking.
- Users upload extracted textbook text, not PDFs. PDF extraction and OCR are the user's responsibility. The application chunks and embeds uploaded text, with one active textbook per course.
- Semester assignment is not a required feature. Support editable courses and textbook replacement without imposing semester management.
- Support slash-command generation, progress embeds, web review/edit/publish, editable shared/course/Discord-format rules, and per-course destination channels.
- Preserve the transcript as the primary source and use textbook retrieval as supporting context. Require an explicit publication action after review.
- Treat existing_project as migration reference material. Avoid changing it unless the task requires it.

## Git and commits

After completing a coherent change, run appropriate checks, review the diff, and commit the work with a concise message explaining its purpose. Do not leave finished changes uncommitted unless the user asks otherwise. Keep commits focused, never include secrets or runtime artifacts, and do not include unrelated user changes.
