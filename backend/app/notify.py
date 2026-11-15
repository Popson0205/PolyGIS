"""Tracking alerts sent to WhatsApp: an urgent report (a patrol asking for support, a hostile sighting), a new device seen, or a device entering or leaving a geofence.

Three ways to send, chosen per tracker; the keys are kept server-side in the tracker's secret and never sent to the browser:
- meta:      WhatsApp Cloud API (Meta). Keys wa_token, wa_phone_id; wa_template (optional) for messages outside the 24-hour window,
             an approved template with one {{1}} body variable that receives the alert text.
- twilio:    Twilio WhatsApp. Keys twilio_sid, twilio_token, twilio_from (the WhatsApp number of the account or the sandbox).
- waha:      WAHA (open source, self-hosted WhatsApp gateway, free): a WhatsApp number linked by QR code to the WAHA server.
             Keys waha_url (e.g. http://waha.railway.internal:3000), waha_key (its X-Api-Key, if set), waha_session (default "default").
- brevo:     Brevo (WhatsApp on the Brevo account). Keys brevo_key (API key), brevo_sender (the WhatsApp number on Brevo);
             brevo_template (optional), the ID of an approved Brevo WhatsApp template, sent when plain text is refused
             (the first message to a number, or more than 24 hours after it last wrote), with the alert text as params.ALERT.
- callmebot: CallMeBot (free, for a personal number). Key callmebot_key, which CallMeBot sends the number once it allows messages.
"""
import asyncio
import re

import httpx

PROVIDERS = ("waha", "brevo", "meta", "twilio", "callmebot")
EVENTS = ("urgent", "new", "enter", "exit")
NEEDS = {"waha": ("waha_url",), "brevo": ("brevo_key", "brevo_sender"), "meta": ("wa_token", "wa_phone_id"), "twilio": ("twilio_sid", "twilio_token", "twilio_from"), "callmebot": ("callmebot_key",)}
MIN_GAP_S = 60                       # at most one message a minute per tracker; alerts in between go together in the next
MAX_LINES = 15
GRAPH = "https://graph.facebook.com/v21.0"
_tasks: set[asyncio.Task] = set()
TRANSPORT: httpx.AsyncBaseTransport | None = None     # tests answer with a stand-in


class SendError(Exception):
    pass


def phone(raw: str, default_cc: str = "234") -> str:
    """+E.164 from what people type: 0916 042 0100 (a Nigerian number written at home) → +2349160420100."""
    s = re.sub(r"[^\d+]", "", raw or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        s = "+" + default_cc + s[1:] if s.startswith("0") else "+" + s
    if not re.fullmatch(r"\+[1-9]\d{7,14}", s):
        raise ValueError("That is not a phone number: write it with the country code, e.g. +234 916 042 0100.")
    return s


def missing(provider: str, secret: dict) -> list[str]:
    return [k for k in NEEDS.get(provider, ()) if not (secret or {}).get(k)]


def compose(tracker: str, alerts: list[dict]) -> str:
    lines = []
    for a in alerts[:MAX_LINES]:
        who = a.get("name") or a["device"]
        at = a["ts"].strftime("%H:%M UTC") if hasattr(a["ts"], "strftime") else str(a["ts"])
        where = f" ({a['y']:.5f}, {a['x']:.5f})" if a.get("x") is not None else ""
        if a["kind"] == "urgent":
            lines.append(f"🚨 {a['zone']} at {at}{where}")
        elif a["kind"] == "new":
            lines.append(f"• New device: {who} at {at}{where}")
        else:
            lines.append(f"• {who} {'entered' if a['kind'] == 'enter' else 'left'} {a['zone']} at {at}{where}")
    if len(alerts) > MAX_LINES:
        lines.append(f"…and {len(alerts) - MAX_LINES} more in PolyGIS.")
    return f"PolyGIS · {tracker}\n" + "\n".join(lines)


async def send(provider: str, to: str, text: str, secret: dict, client: httpx.AsyncClient | None = None) -> None:
    """Sends one message, or raises SendError with what went wrong."""
    gaps = missing(provider, secret)
    if provider not in PROVIDERS:
        raise SendError("Choose how WhatsApp messages are sent.")
    if gaps:
        raise SendError("Give the WhatsApp keys first: " + ", ".join(gaps) + ".")
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, transport=TRANSPORT)
    try:
        if provider == "meta":
            body: dict = {"messaging_product": "whatsapp", "to": to.lstrip("+")}
            if secret.get("wa_template"):
                body |= {"type": "template", "template": {"name": secret["wa_template"], "language": {"code": secret.get("wa_lang") or "en"},
                                                         "components": [{"type": "body", "parameters": [{"type": "text", "text": text[:1000]}]}]}}
            else:
                body |= {"type": "text", "text": {"body": text[:4096]}}
            r = await client.post(f"{GRAPH}/{secret['wa_phone_id']}/messages", json=body, headers={"Authorization": f"Bearer {secret['wa_token']}"})
        elif provider == "waha":
            base = secret["waha_url"].strip().rstrip("/")
            if not re.match(r"https?://[^/\s]+", base):
                raise SendError("The WAHA address starts with http:// or https://.")
            r = await client.post(f"{base}/api/sendText", json={"session": secret.get("waha_session") or "default", "chatId": to.lstrip("+") + "@c.us",
                                                                 "text": text[:4096]}, headers={"X-Api-Key": secret["waha_key"]} if secret.get("waha_key") else {})
        elif provider == "brevo":
            url, head = "https://api.brevo.com/v3/whatsapp/sendMessage", {"api-key": secret["brevo_key"], "accept": "application/json"}
            base = {"senderNumber": phone(secret["brevo_sender"]).lstrip("+"), "contactNumbers": [to.lstrip("+")]}
            r = await client.post(url, json={**base, "text": text[:4096]}, headers=head)
            tid = str(secret.get("brevo_template") or "").strip()
            if r.status_code >= 400 and r.status_code != 401 and tid.isdigit():     # outside the 24-hour window: the approved template
                r = await client.post(url, json={**base, "templateId": int(tid), "params": {"ALERT": text[:1000]}}, headers=head)
        elif provider == "twilio":
            frm = secret["twilio_from"] if secret["twilio_from"].startswith("whatsapp:") else "whatsapp:" + phone(secret["twilio_from"])
            r = await client.post(f"https://api.twilio.com/2010-04-01/Accounts/{secret['twilio_sid']}/Messages.json",
                                  data={"To": "whatsapp:" + to, "From": frm, "Body": text[:1600]}, auth=(secret["twilio_sid"], secret["twilio_token"]))
        else:
            r = await client.get("https://api.callmebot.com/whatsapp.php", params={"phone": to, "text": text[:2000], "apikey": secret["callmebot_key"]})
    except httpx.HTTPError as e:
        raise SendError(f"WhatsApp could not be reached ({e.__class__.__name__}).") from None
    finally:
        if own:
            await client.aclose()
    if r.status_code >= 400 or (provider == "callmebot" and "ERROR" in r.text.upper()[:500]):
        raise SendError(f"WhatsApp refused the message ({r.status_code}): {_reason(r)}")


def _reason(r: httpx.Response) -> str:
    try:
        j = r.json()
        msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else j.get("message")
        if msg:
            return str(msg)[:300]
    except ValueError:
        pass
    return re.sub(r"<[^>]+>", " ", r.text or "").strip()[:300] or "no reason given"


def later(coro) -> None:
    """Runs a send after the request has let go of its database connection; kept referenced so it is not collected."""
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
