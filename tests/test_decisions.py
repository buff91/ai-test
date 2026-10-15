"""Contract checks for the shared typed questions and decision records."""

import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from compare import QUESTIONS, call_jev, summarize_answer
from game_core import ACTIONS


def response():
    return {"model": "test-model", "answers": {
        "action": {"type": "choice", "choice": "dodge", "confidence": .8,
                   "probabilities": {"dodge": .9, "wait": .1}},
        "danger": {"type": "score", "score": 1.6, "confidence": .5,
                   "probabilities": {"0": 0, "1": .4, "2": .6}},
        "incoming_attack": {"type": "noul", "noul": .95},
    }}


class DecisionContractTests(unittest.TestCase):
    def test_jev_sends_all_three_questions(self):
        with patch("compare.urllib.request.urlopen", return_value=io.BytesIO(
                json.dumps(response()).encode())) as send:
            result = call_jev("enemy charging", "test-only-key", "test-model", 3)
        request = send.call_args.args[0]
        self.assertEqual(json.loads(request.data)["questions"], QUESTIONS)
        self.assertEqual({q["type"] for q in QUESTIONS.values()}, {"choice", "score", "noul"})
        self.assertEqual(set(QUESTIONS["action"]["criteria"]), set(ACTIONS))
        self.assertEqual(summarize_answer(result)["incoming_attack"], .95)

    def test_full_answers_survive_export(self):
        raw = response()
        saved = json.loads(json.dumps(summarize_answer(raw)))
        self.assertEqual(saved["answers"], raw["answers"])
        self.assertEqual(saved["danger"], 1.6)
        self.assertEqual(saved["action"], "dodge")

    def test_invalid_or_missing_results_are_rejected(self):
        for question, field, invalid in [("danger", "score", float("nan")),
                                         ("danger", "score", 3),
                                         ("incoming_attack", "noul", -1),
                                         ("incoming_attack", "noul", True),
                                         ("action", "choice", "teleport")]:
            with self.subTest(question=question, invalid=invalid):
                raw = deepcopy(response())
                raw["answers"][question][field] = invalid
                with self.assertRaises(ValueError):
                    summarize_answer(raw)
        raw = response()
        del raw["answers"]["danger"]
        with self.assertRaises(KeyError):
            summarize_answer(raw)


if __name__ == "__main__":
    unittest.main()
