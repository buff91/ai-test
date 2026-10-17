"""Contract checks for the shared typed questions and decision records."""

import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from gamelab.decide import call_jev, summarize
from gamelab.game_case import GameCase
from gamelab.grade import grade
from sims.racer import RacerSim
ACTIONS = RacerSim.ACTIONS


def response():
    return {"model": "test-model", "answers": {
        "action": {"type": "choice", "choice": "brake", "confidence": .8,
                   "probabilities": {"brake": .9, "keep": .1}},
        "danger": {"type": "score", "score": 1.6, "confidence": .5,
                   "probabilities": {"0": 0, "1": .4, "2": .6}},
        "crash_ahead": {"type": "noul", "noul": .95},
    }}


class DecisionContractTests(unittest.TestCase):
    def setUp(self):
        self.questions = GameCase("racer").questions
        self.options = self.questions["action"]["criteria"]

    def test_jev_sends_all_three_questions(self):
        with patch("gamelab.decide.urllib.request.urlopen", return_value=io.BytesIO(
                json.dumps(response()).encode())) as send:
            result = call_jev("enemy charging", "test-only-key", "test-model", 3, self.questions)
        request = send.call_args.args[0]
        self.assertEqual(json.loads(request.data), {
            "model": "test-model", "state": "enemy charging", "questions": self.questions,
        })
        self.assertEqual({q["type"] for q in self.questions.values()}, {"choice", "score", "noul"})
        self.assertEqual(set(self.options), set(ACTIONS))
        self.assertEqual(summarize(result, self.options)["answers"]["crash_ahead"]["noul"], .95)

    def test_full_answers_survive_export(self):
        raw = response()
        saved = json.loads(json.dumps(summarize(raw, self.options)))
        self.assertEqual(saved["answers"], raw["answers"])
        self.assertEqual(saved["answers"]["danger"]["score"], 1.6)
        self.assertEqual(saved["action"], "brake")

    def test_unknown_action_is_rejected(self):
        raw = response()
        raw["answers"]["action"]["choice"] = "teleport"
        with self.assertRaises(ValueError):
            summarize(raw, self.options)

    def test_invalid_score_or_noul_results_are_marked(self):
        for question, field, invalid in [("danger", "score", float("nan")),
                                         ("danger", "score", 3),
                                         ("crash_ahead", "noul", -1),
                                         ("crash_ahead", "noul", True)]:
            with self.subTest(question=question, invalid=invalid):
                raw = deepcopy(response())
                raw["answers"][question][field] = invalid
                graded = grade(raw["answers"], {question: 1}, self.questions)
                self.assertEqual(graded[question]["status"], "invalid")

    def test_missing_results_are_marked(self):
        for question, expected in [("action", "brake"), ("danger", 2), ("crash_ahead", True)]:
            with self.subTest(question=question):
                raw = response()
                del raw["answers"][question]
                graded = grade(raw["answers"], {question: expected}, self.questions)
                self.assertEqual(graded[question]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
