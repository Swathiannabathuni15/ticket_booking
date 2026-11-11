"""The Gemini adapter: our message format <-> Gemini's, and which provider is picked."""
import json

from app.config import settings
from app.services import agent_service as svc
from app.services.agent_service import GeminiClient


def test_tools_become_gemini_function_declarations():
    req = GeminiClient.to_request("be helpful", [{"role": "user", "content": "hi"}], svc.TOOLS)
    decls = {d["name"]: d for d in req["tools"][0]["functionDeclarations"]}
    assert set(decls) == svc.ALLOWED_TOOLS                      # exactly the safe tools: still no pay/book
    assert decls["hold_seats"]["parameters"]["properties"]["seat_ids"] == {"type": "ARRAY", "items": {"type": "INTEGER"}}
    assert decls["hold_seats"]["parameters"]["type"] == "OBJECT"
    assert req["systemInstruction"]["parts"][0]["text"] == "be helpful"
    assert req["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


def test_a_tool_round_trip_is_translated_both_ways():
    messages = [
        {"role": "user", "content": "find movies"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "search_movies",
                                           "input": {"query": "cult"}, "_sig": "sig123"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1",
                                      "content": json.dumps({"untrusted_data": [{"id": 1}]})}]},
    ]
    contents = GeminiClient.to_request("s", messages, svc.TOOLS)["contents"]
    assert contents[1] == {"role": "model", "parts": [{"functionCall": {"name": "search_movies", "args": {"query": "cult"}},
                                                       "thoughtSignature": "sig123"}]}
    assert contents[2]["parts"][0]["functionResponse"] == {"name": "search_movies",
                                                           "response": {"untrusted_data": [{"id": 1}]}}   # name found from the id


def test_gemini_reply_becomes_text_and_tool_blocks():
    data = {"candidates": [{"content": {"parts": [
        {"text": "Let me look."}, {"functionCall": {"name": "hold_seats", "args": {"seat_ids": [1, 2]}}, "thoughtSignature": "abc"},
        {"text": "hidden reasoning", "thought": True}]}}]}
    blocks = GeminiClient.from_response(data)["content"]
    assert blocks[0] == {"type": "text", "text": "Let me look."}
    assert blocks[1]["type"] == "tool_use" and blocks[1]["name"] == "hold_seats" and blocks[1]["_sig"] == "abc"
    assert blocks[1]["input"] == {"seat_ids": [1, 2]} and len(blocks) == 2          # reasoning text is not shown
    assert GeminiClient.from_response({"candidates": []}) == {"content": []}         # blocked/empty: no crash


def test_a_model_asking_for_a_pay_tool_is_still_refused(client):
    """Same guarantee as with any model: the capability does not exist."""
    from app.db import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        u = User(email="x@example.com", password_hash="x")
        db.add(u); db.commit()
        out = json.loads(svc.run_tool(db, None, u, "pay_booking", {"booking_id": 1}))
    assert "not available" in out["error"]


def test_provider_choice():
    settings.agent_provider = "auto"
    assert isinstance(svc.get_llm(), svc.NoKeyClient)
    settings.anthropic_api_key = "a"
    assert isinstance(svc.get_llm(), svc.AnthropicClient)
    settings.gemini_api_key = "g"                                                   # Gemini wins when both are set
    assert isinstance(svc.get_llm(), svc.GeminiClient)
    settings.agent_provider = "anthropic"
    assert isinstance(svc.get_llm(), svc.AnthropicClient)
    settings.agent_provider = "gemini"; settings.gemini_api_key = ""
    assert isinstance(svc.get_llm(), svc.NoKeyClient)
