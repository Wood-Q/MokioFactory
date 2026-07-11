from __future__ import annotations

import unittest

from pipelines.export.export_llamafactory import repair_sharegpt_turns, to_sharegpt, validate_sharegpt_turns


class LlamaFactorySchemaAuditTest(unittest.TestCase):
    def test_function_call_may_be_the_final_training_target(self) -> None:
        conversations = [
            {"from": "human", "value": "Look up the weather."},
            {"from": "function_call", "value": '{"name":"weather","arguments":{"city":"Shanghai"}}'},
        ]

        validate_sharegpt_turns(conversations)

    def test_complete_tool_round_must_end_with_model_output(self) -> None:
        conversations = [
            {"from": "human", "value": "Look up the weather."},
            {"from": "function_call", "value": '{"name":"weather","arguments":{"city":"Shanghai"}}'},
            {"from": "observation", "value": '{"temperature":30}'},
            {"from": "gpt", "value": "It is 30 degrees in Shanghai."},
        ]

        validate_sharegpt_turns(conversations)

    def test_trailing_observation_is_trimmed_but_function_call_target_is_kept(self) -> None:
        conversations = [
            {"from": "human", "value": "What can you do?"},
            {"from": "gpt", "value": "I can help with reservations."},
            {"from": "human", "value": "Transfer me to a person."},
            {"from": "function_call", "value": '{"name":"transfer","arguments":{}}'},
            {"from": "observation", "value": "Transfer successful"},
        ]

        repaired, repairs = repair_sharegpt_turns(conversations)

        self.assertEqual(repaired, conversations[:-1])
        self.assertEqual(repairs, ["trimmed_trailing_observation"])

    def test_observation_without_function_call_is_rejected(self) -> None:
        conversations = [
            {"from": "human", "value": "Hello"},
            {"from": "gpt", "value": "Hi"},
            {"from": "observation", "value": "unexpected"},
            {"from": "gpt", "value": "Done"},
        ]

        with self.assertRaisesRegex(ValueError, "not preceded by a function call"):
            validate_sharegpt_turns(conversations)

    def test_canonical_record_is_repaired_and_tools_are_serialized(self) -> None:
        record = {
            "id": "record-1",
            "source_dataset": "example",
            "messages": [
                {"role": "system", "content": "Use tools carefully."},
                {"role": "user", "content": "What can you do?"},
                {"role": "assistant", "content": "I can help."},
                {"role": "user", "content": "Transfer me."},
                {"role": "assistant", "name": "function_call", "content": '{"name":"transfer"}'},
                {"role": "tool", "content": "Transfer successful"},
            ],
            "tools": [{"name": "transfer", "parameters": {}}],
        }

        converted, repairs = to_sharegpt(record)

        self.assertEqual(len(converted["conversations"]), 4)
        self.assertEqual(converted["system"], "Use tools carefully.")
        self.assertIn('"name": "transfer"', converted["tools"])
        self.assertEqual(repairs, ["trimmed_trailing_observation"])


if __name__ == "__main__":
    unittest.main()
