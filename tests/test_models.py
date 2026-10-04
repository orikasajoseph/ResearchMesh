from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError as SchemaError
from pydantic import ValidationError

from common.models import BusinessTask, Plan, ResearchResult
from export_schemas import build_schemas
from mock_agents import LiteratureMockAgent
from orchestrator import make_task_payload
import asyncio


ROOT = Path(__file__).resolve().parents[1]


def cases():
    return json.loads((ROOT / "examples" / "planner_cases.json").read_text(encoding="utf-8"))


class ModelTests(unittest.TestCase):
    def test_all_examples_pass_pydantic_and_schema(self):
        schema = build_schemas()["plan.schema.json"]
        for case in cases():
            Plan.model_validate(case["plan"])
            Draft202012Validator(schema).validate(case["plan"])

    def test_exported_schemas_are_current_and_well_formed(self):
        for name, schema in build_schemas().items():
            with self.subTest(name=name):
                Draft202012Validator.check_schema(schema)
                self.assertEqual(json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8")), schema)

    def test_payload_model_does_not_accept_internal_fields(self):
        task = cases()[0]["plan"]["tasks"][0]
        BusinessTask.model_validate(make_task_payload(task))
        with self.assertRaises(ValidationError):
            BusinessTask.model_validate(task)

    def test_strict_bool_and_unknown_fields(self):
        for field, value in (("needsClarification", "false"), ("status", "completed")):
            plan = deepcopy(cases()[0]["plan"])
            plan[field] = value
            with self.assertRaises(ValidationError):
                Plan.model_validate(plan)

    def test_plan_schema_encodes_clarification_condition(self):
        plan = deepcopy(cases()[2]["plan"])
        plan["clarificationQuestion"] = None
        with self.assertRaises(SchemaError):
            Draft202012Validator(build_schemas()["plan.schema.json"]).validate(plan)

    def test_long_or_blank_query_is_rejected(self):
        for query in (" " * 4, "a" * 6001):
            plan = deepcopy(cases()[0]["plan"])
            plan["userQuery"] = query
            with self.assertRaises(ValidationError):
                Plan.model_validate(plan)

    def test_result_model_and_schema_agree(self):
        task = cases()[0]["plan"]["tasks"][0]
        result = asyncio.run(LiteratureMockAgent().execute(make_task_payload(task), {}))
        ResearchResult.model_validate(result)
        Draft202012Validator(build_schemas()["result.schema.json"]).validate(result)
        result["status"] = "completed"
        with self.assertRaises(ValidationError):
            ResearchResult.model_validate(result)
        with self.assertRaises(SchemaError):
            Draft202012Validator(build_schemas()["result.schema.json"]).validate(result)
