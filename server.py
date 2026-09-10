import os
from typing import Any
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Remote Test MCP Server")

EXPECTED_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "secret_token_abc")

# In-memory mock database
CRM_CONTACTS: dict[str, dict[str, Any]] = {
    "user_a1b2": {"id": "user_a1b2", "email": "test@example.com", "name": "Test User"},
}
KB_DOCS: list[dict[str, Any]] = [
    {"id": "doc_101", "title": "Studio Hours", "content": "Open Tue-Sat from 10 AM to 8 PM."},
    {"id": "doc_102", "title": "Deposit Policy", "content": "A 20% deposit is required for all appointments."},
]


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


def verify_token(authorization: str | None = Header(None)) -> None:
    if not EXPECTED_AUTH_TOKEN:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    if parts[1] != EXPECTED_AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "remote-test-mcp"}


@app.post("/mcp")
async def handle_mcp(
    request: JsonRpcRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    verify_token(authorization)

    method = request.method
    params = request.params or {}

    # 1. Tool Discovery: tools/list
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request.id,
            "result": {
                "tools": [
                    {
                        "name": "crm_contact_search",
                        "description": "Search CRM contacts by email or phone",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "email": {"type": "string"},
                                "phone": {"type": "string"},
                            },
                        },
                    },
                    {
                        "name": "crm_contact_upsert",
                        "description": "Create or update CRM contact and return contact_id",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "email": {"type": "string"},
                                "name": {"type": "string"},
                            },
                            "required": ["email", "name"],
                        },
                    },
                    {
                        "name": "kb_search",
                        "description": "Search internal knowledge base documents",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                            },
                            "required": ["query"],
                        },
                    },
                ]
            },
        }

    # 2. Tool Invocation: tools/call
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "crm_contact_search":
            email = arguments.get("email")
            match = next((c for c in CRM_CONTACTS.values() if c["email"] == email), None)
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {"contact": match} if match else {"contact": None},
            }

        if tool_name == "crm_contact_upsert":
            email = arguments.get("email", "")
            name = arguments.get("name", "")
            cid = f"rem_{abs(hash(email)) % 10000}"
            CRM_CONTACTS[cid] = {"id": cid, "email": email, "name": name}
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {"contact_id": cid, "status": "upserted"},
            }

        if tool_name == "kb_search":
            query = arguments.get("query", "").lower()
            matches = [d for d in KB_DOCS if query in d["title"].lower() or query in d["content"].lower()]
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {"documents": matches},
            }

        return {
            "jsonrpc": "2.0",
            "id": request.id,
            "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
        }

    return {
        "jsonrpc": "2.0",
        "id": request.id,
        "error": {"code": -32601, "message": f"Method '{method}' not recognized"},
    }