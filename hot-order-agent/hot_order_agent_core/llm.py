
import os, json
from dotenv import load_dotenv

def _get_client():
    try:
        from openai import OpenAI
    except Exception:
        return None
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    base = os.getenv("OPENAI_BASE_URL")
    if base:
        return OpenAI(api_key=api_key, base_url=base)
    return OpenAI(api_key=api_key)

SYSTEM = (
    "You are an order-operations parser.\n"
    "Extract structured data from free-form emails about customer orders.\n"
    "Return strict JSON with these keys:\n"
    "order_id (string or null),\n"
    "intents: { expedite_request (bool), cancel_order (bool), confirm (bool) },\n"
    "change_qty (int or null),\n"
    "change_destination (string or null),\n"
    "desired_days (int or null),\n"
    "customer_email (string or null).\n"
)

USER_TEMPLATE = "EMAIL:\n---\n{email_text}\n---\nExtract the JSON. Only JSON, no explanations."

def llm_parse_email(email_text: str) -> dict:
    from . import nlp as regex_nlp

    client = _get_client()
    if client is None:
        intents = regex_nlp.detect_intents(email_text)
        return {
            "order_id": regex_nlp.extract_order_id(email_text),
            "intents": {
                "expedite_request": bool(intents.get("expedite_request")),
                "cancel_order": bool(intents.get("cancel_order")),
                "confirm": bool(intents.get("confirm")),
            },
            "change_qty": intents.get("change_qty"),
            "change_destination": intents.get("change_destination"),
            "desired_days": intents.get("desired_days"),
            "customer_email": None,
        }

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    prompt = USER_TEMPLATE.format(email_text=email_text)

    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        content = ""
        try:
            if resp and resp.output and hasattr(resp.output, "text"):
                content = resp.output.text
        except Exception:
            pass
        if not content:
            try:
                content = resp.output[0].content[0].text
            except Exception:
                content = ""

        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start:end+1]

        data = json.loads(content)

        return {
            "order_id": data.get("order_id"),
            "intents": {
                "expedite_request": bool(data.get("intents", {}).get("expedite_request", False)),
                "cancel_order": bool(data.get("intents", {}).get("cancel_order", False)),
                "confirm": bool(data.get("intents", {}).get("confirm", False)),
            },
            "change_qty": data.get("change_qty"),
            "change_destination": data.get("change_destination"),
            "desired_days": data.get("desired_days"),
            "customer_email": data.get("customer_email"),
        }
    except Exception:
        intents = regex_nlp.detect_intents(email_text)
        return {
            "order_id": regex_nlp.extract_order_id(email_text),
            "intents": {
                "expedite_request": bool(intents.get("expedite_request")),
                "cancel_order": bool(intents.get("cancel_order")),
                "confirm": bool(intents.get("confirm")),
            },
            "change_qty": intents.get("change_qty"),
            "change_destination": intents.get("change_destination"),
            "desired_days": intents.get("desired_days"),
            "customer_email": None,
        }
