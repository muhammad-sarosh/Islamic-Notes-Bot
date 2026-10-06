"""Shared BGE scoring; importing this module does not load PyTorch."""

import math

MODEL = "BAAI/bge-reranker-v2-m3"
PROTOCOL = "bge-v2-m3-512-v1"


def validate_scores(scores, count):
    if not isinstance(scores, list) or len(scores) != count or any(
        isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x)
        for x in scores
    ):
        raise ValueError("Expected one finite numeric score per candidate")
    return scores


def rank_candidates(row, scores):
    validate_scores(scores, len(row))
    ranked = sorted(zip(row, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    return [
        {**item, "bge_rank": rank, "bge_score": float(score)}
        for rank, (item, score) in enumerate(ranked[:15], 1)
    ]


class BGERanker:
    def __init__(self, device="cpu", batch_size=2):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        torch.set_num_threads(1)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; install CUDA-enabled PyTorch and check the driver")
        self.device = device
        self.batch_size = batch_size
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL)
        self.model = AutoModelForSequenceClassification.from_pretrained(MODEL).eval().to(device)
        if device == "cuda":
            self.model.half()

    def score(self, query, documents):
        import torch

        scores = []
        for start in range(0, len(documents), self.batch_size):
            pairs = [[query, text] for text in documents[start : start + self.batch_size]]
            inputs = self.tokenizer(
                pairs, padding=True, truncation=True, max_length=512, return_tensors="pt"
            ).to(self.device)
            with torch.inference_mode():
                scores.extend(self.model(**inputs).logits.reshape(-1).float().cpu().tolist())
        return validate_scores(scores, len(documents))
