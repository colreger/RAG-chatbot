"""Trusted local stdio MCP bridge to the authenticated loopback travel API.

The principal is configured at startup. Tools never accept account credentials,
an owner override, arbitrary SQL, an API URL or a filesystem path.
"""

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
import sys
from typing import Annotated, Literal

import httpx
from jsonschema import Draft202012Validator
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

API_URL = "http://127.0.0.1:8018"
MAX_RESPONSE_BYTES = 32768


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Text = Annotated[str, StringConstraints(min_length=1, max_length=6000)]


class Claim(StrictModel):
    status: Text
    deduction_reason: Text
    policy_title: Text
    policy_excerpt: Text


class ClaimResult(StrictModel):
    status: Literal["answered", "not_found"]
    claim: Claim | None


class Evidence(StrictModel):
    title: Text
    excerpt: Text


class Answer(StrictModel):
    status: Literal["answered", "clarify", "not_found"]
    answer: Text
    sources: Annotated[list[Evidence], Field(max_length=3)]


INPUTS = {
    "get_claim_status": {
        "type": "object", "additionalProperties": False,
        "properties": {"claim_id": {"type": "string", "pattern": "^RK-[0-9]{4}$", "minLength": 7, "maxLength": 7}},
        "required": ["claim_id"],
    },
    "ask_travel_question": {
        "type": "object", "additionalProperties": False,
        "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 2000, "pattern": "\\S"}},
        "required": ["question"],
    },
}


def tool_error(code: str) -> types.CallToolResult:
    # Never include validation input, raw HTTP bodies or exception text.
    messages = {
        "invalid_arguments": "Invalid tool arguments.",
        "unknown_tool": "Unknown tool.",
        "authentication_failed": "Authentication failed.",
        "service_unavailable": "Travel service unavailable. Try again later.",
        "invalid_upstream_response": "Travel service returned an invalid response.",
    }
    return types.CallToolResult(content=[types.TextContent(text=messages[code])], isError=True)


def success(model: BaseModel) -> types.CallToolResult:
    payload = model.model_dump(mode="json")
    return types.CallToolResult(
        content=[types.TextContent(text=json.dumps(payload, ensure_ascii=False))],
        structuredContent=payload,
    )


def with_request_id(result: types.CallToolResult, response: httpx.Response) -> types.CallToolResult:
    request_id = response.headers.get("x-request-id", "")
    if re.fullmatch(r"[a-f0-9]{32}", request_id):
        result.meta = {**(result.meta or {}), "support/request_id": request_id}
    return result


class TravelBridge:
    def __init__(self, username: str, password: str, *, transport=None):
        self.client = httpx.AsyncClient(
            base_url=API_URL, auth=(username, password), timeout=5,
            follow_redirects=False, trust_env=False, transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def call(self, name: str, arguments: dict | None) -> types.CallToolResult:
        if name not in INPUTS:
            return tool_error("unknown_tool")
        if not Draft202012Validator(INPUTS[name]).is_valid(arguments):
            return tool_error("invalid_arguments")
        try:
            if name == "get_claim_status":
                response = await self.client.get(f"/claims/{arguments['claim_id']}")
            else:
                response = await self.client.post("/ask", json={"question": arguments["question"]})
        except httpx.RequestError:
            return tool_error("service_unavailable")
        def finish(result):
            return with_request_id(result, response)
        if response.status_code in (401, 403):
            return finish(tool_error("authentication_failed"))
        if response.status_code == 404 and name == "get_claim_status":
            return finish(success(ClaimResult(status="not_found", claim=None)))
        if response.status_code >= 500:
            return finish(tool_error("service_unavailable"))
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
            return finish(tool_error("invalid_upstream_response"))
        try:
            if name == "get_claim_status":
                return finish(success(ClaimResult(status="answered", claim=Claim.model_validate(response.json()))))
            return finish(success(Answer.model_validate(response.json())))
        except (ValueError, ValidationError):
            return finish(tool_error("invalid_upstream_response"))


def create_server(bridge: TravelBridge) -> Server:
    async def list_tools(context, params):
        annotations = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
        return types.ListToolsResult(tools=[
            types.Tool(name="get_claim_status", description="Read a fictional travel claim visible to the signed-in account.",
                       inputSchema=INPUTS["get_claim_status"], outputSchema=ClaimResult.model_json_schema(), annotations=annotations),
            types.Tool(name="ask_travel_question", description="Ask about a fictional travel claim or permitted travel policy.",
                       inputSchema=INPUTS["ask_travel_question"], outputSchema=Answer.model_json_schema(), annotations=annotations),
        ])

    async def call_tool(context, params):
        return await bridge.call(params.name, params.arguments)

    return Server("travel-claims-local", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool)


def load_credentials(path: Path) -> tuple[str, str]:
    stat = path.stat()
    if path.is_symlink() or stat.st_mode & 0o077 or stat.st_uid != os.getuid():
        raise ValueError("Credential file must be owner-only and owned by the current user.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"username", "password"}:
        raise ValueError("Invalid credential file.")
    if any(not isinstance(data[key], str) or not 1 <= len(data[key]) <= 256 for key in data):
        raise ValueError("Invalid credential fields.")
    return data["username"], data["password"]


async def serve(path: Path):
    username, password = load_credentials(path)
    bridge = TravelBridge(username, password)
    server = create_server(bridge)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await bridge.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.credentials_file))
    except (OSError, ValueError):
        print("MCP local configuration unavailable or invalid.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
