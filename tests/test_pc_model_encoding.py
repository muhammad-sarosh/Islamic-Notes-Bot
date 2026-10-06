"""Exercise the actual child protocol under a legacy Windows pipe encoding."""

import json
import os
import subprocess
import sys


def test_model_pipe_preserves_urdu_under_legacy_encoding():
    request = {"query": "نکاح کے احکام", "documents": ["شادی اور نکاح", "كتاب الطهارة"]}
    script = """
import notes_bot.pc_model as model
class FakeRanker:
    def __init__(self, *args):
        pass
    def score(self, query, documents):
        assert query == '\u0646\u06a9\u0627\u062d \u06a9\u06d2 \u0627\u062d\u06a9\u0627\u0645'
        assert documents == ['\u0634\u0627\u062f\u06cc \u0627\u0648\u0631 \u0646\u06a9\u0627\u062d', '\u0643\u062a\u0627\u0628 \u0627\u0644\u0637\u0647\u0627\u0631\u0629']
        return [0.75, -0.25]
model.BGERanker = FakeRanker
model.main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(request, ensure_ascii=False) + "\n",
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"scores": [0.75, -0.25]}
