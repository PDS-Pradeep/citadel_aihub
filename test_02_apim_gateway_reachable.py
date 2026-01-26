import requests
from datetime import datetime
from openpyxl import Workbook

# -------------------------------------------------
# CONFIGURATION
# -------------------------------------------------

APIM_GATEWAY_URL = "https://apim-oygf3jjanv6um.azure-api.net"
CHAT_PATH = "/openai/deployments/chat/chat/completions"
API_VERSION = "2024-10-21"

REPORT_FILE = "apim_chat_test_report.xlsx"

import requests
from azure.identity import DefaultAzureCredential

AZURE_SUBSCRIPTION_ID = "9ad6f7f4-b0d6-4d88-a6d1-3fc2257d5583"
RESOURCE_GROUP = "rg-hbai-lz2"
APIM_SERVICE_NAME = "apim-oygf3jjanv6um"
MGMT_API_VERSION = "2022-08-01"

def get_apim_subscription_key():
    credential = DefaultAzureCredential()
    token = credential.get_token(
        "https://management.azure.com/.default"
    ).token

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    list_url = (
        f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
        f"/resourceGroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.ApiManagement"
        f"/service/{APIM_SERVICE_NAME}"
        f"/subscriptions?api-version={MGMT_API_VERSION}"
    )

    r = requests.get(list_url, headers=headers)
    r.raise_for_status()

    subscriptions = r.json()["value"]

    subscription_id = None
    for sub in subscriptions:
        name = sub.get("properties", {}).get("displayName")
        if name and name.lower() == "built-in all-access subscription":
            subscription_id = sub["name"]
            break

    if not subscription_id:
        for sub in subscriptions:
            if sub.get("properties", {}).get("state") == "active":
                subscription_id = sub["name"]
                break

    if not subscription_id:
        raise RuntimeError("No active APIM subscription found")

    secret_url = (
        f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
        f"/resourceGroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.ApiManagement"
        f"/service/{APIM_SERVICE_NAME}"
        f"/subscriptions/{subscription_id}/listSecrets"
        f"?api-version={MGMT_API_VERSION}"
    )

    r = requests.post(secret_url, headers=headers)
    r.raise_for_status()

    return r.json()["primaryKey"]

APIM_SUBSCRIPTION_KEY = get_apim_subscription_key()
print("Using APIM Subscription Key:", APIM_SUBSCRIPTION_KEY)

# -------------------------------------------------
# EXCEL SETUP
# -------------------------------------------------

wb = Workbook()
ws = wb.active
ws.title = "APIM Chat Test"

ws.append([
    "Test Case",
    "Step",
    "Expected",
    "Actual",
    "Status",
    "Evidence",
    "Timestamp"
])

def log(step, expected, actual, status, evidence=""):
    ws.append([
        "TC-OPENAI-APIM-01",
        step,
        expected,
        actual,
        status,
        evidence,
        datetime.utcnow().isoformat() + "Z"
    ])


# -------------------------------------------------
# TEST 1: GATEWAY CHECK
# -------------------------------------------------

def check_gateway():
    try:
        r = requests.get(APIM_GATEWAY_URL, timeout=10)
        log(
            "Gateway HTTPS reachable",
            "HTTPS reachable",
            f"HTTP {r.status_code}",
            "PASS",
            "Gateway responded"
        )
        return True
    except Exception as e:
        log(
            "Gateway HTTPS reachable",
            "HTTPS reachable",
            "Connection failed",
            "FAIL",
            str(e)
        )
        return False

# -------------------------------------------------
# TEST 2: CHAT API (REAL CALL)
# -------------------------------------------------

def test_chat_api():
    step = "OpenAI Chat Completions API"

    url = (
        f"{APIM_GATEWAY_URL}{CHAT_PATH}"
        f"?api-version={API_VERSION}"
        f"&subscription-key={APIM_SUBSCRIPTION_KEY}"
    )

    headers = {
        "Content-Type": "application/json",
        # Header ALSO included (belt + suspenders)
        "Ocp-Apim-Subscription-Key": APIM_SUBSCRIPTION_KEY
    }

    payload = {
        "model": "chat",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "How to calculate the distance between Earth and Moon?"}
        ],
        "max_tokens": 150
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    if response.status_code != 200:
        log(
            step,
            "HTTP 200",
            f"HTTP {response.status_code}",
            "FAIL",
            response.text
        )
        return False

    answer = response.json()["choices"][0]["message"]["content"]

    print("\n===== CHAT RESPONSE =====\n")
    print(answer)
    print("\n=========================\n")

    log(step, "Chat response returned", "HTTP 200", "PASS", answer[:300])
    return True

# -------------------------------------------------
# MAIN
# -------------------------------------------------

def main():
    ok = True

    if not check_gateway():
        ok = False

    if not test_chat_api():
        ok = False

    wb.save(REPORT_FILE)

    print("RESULT:", "PASS" if ok else "FAIL")
    print(f"Report generated: {REPORT_FILE}")

if __name__ == "__main__":
    main()
