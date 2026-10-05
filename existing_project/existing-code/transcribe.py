import sys
from faster_whisper import WhisperModel, BatchedInferencePipeline

audio_path = sys.argv[1]
output_path = sys.argv[2]
model_name = sys.argv[3] if len(sys.argv) > 3 else "small"

model = WhisperModel(
    model_name,
    device="cuda",
    compute_type="float16"
)

batched_model = BatchedInferencePipeline(model=model)

segments, info = batched_model.transcribe(
    audio_path,
    vad_filter=True,
    batch_size=24
)

with open(output_path, "w", encoding="utf-8") as f:
    for segment in segments:
        text = segment.text.strip()

        if text:
            f.write(text + "\n")

print(f"Detected language: {info.language}")
print(f"Transcript saved to: {output_path}")
