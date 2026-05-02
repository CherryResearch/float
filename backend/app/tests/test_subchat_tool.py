from app.tools.subchat import subchat
from app.utils import generate_signature
from app.utils.tool_args import normalize_tool_args


def _invoke(raw_args):
    args = normalize_tool_args("subchat", raw_args)
    signature = generate_signature("tester", "subchat", args)
    return subchat(user="tester", signature=signature, **args)


def test_subchat_tool_defaults_to_return_to_parent():
    result = _invoke({})

    assert result["status"] == "ok"
    assert result["action"] == "return"
    assert result["control"]["action"] == "return_to_parent"


def test_subchat_tool_accepts_stop_alias():
    result = _invoke({"stop": True, "note": "done"})

    assert result["action"] == "return"
    assert result["note"] == "done"
    assert result["control"]["action"] == "return_to_parent"


def test_subchat_tool_accepts_continue_alias():
    result = _invoke({"continue": True, "requested_minutes": 15})

    assert result["action"] == "continue"
    assert result["control"]["action"] == "continue"
    assert result["control"]["requested_minutes"] == 15
