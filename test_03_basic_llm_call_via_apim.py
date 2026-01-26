import requests
from azure.identity import DefaultAzureCredential

# =================================================
# CONFIGURATION
# =================================================

AZURE_SUBSCRIPTION_ID = "9ad6f7f4-b0d6-4d88-a6d1-3fc2257d5583"
RESOURCE_GROUP = "rg-hbai-lz2"
APIM_NAME = "apim-oygf3jjanv6um"
API_ID = "openai"

APIM_GATEWAY_URL = "https://apim-oygf3jjanv6um.azure-api.net"
CHAT_PATH = "/openai/deployments/chat/chat/completions"

MGMT_API_VERSION = "2022-08-01"
OPENAI_API_VERSION = "2024-10-21"

# =================================================
# AUTH (Azure AD)
# =================================================

credential = DefaultAzureCredential()
token = credential.get_token(
    "https://management.azure.com/.default"
).token

MGMT_HEADERS = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# =================================================
# APIM SUBSCRIPTION KEY
# =================================================

def get_apim_subscription_key():
    list_url = (
        f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
        f"/resourceGroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.ApiManagement"
        f"/service/{APIM_NAME}"
        f"/subscriptions?api-version={MGMT_API_VERSION}"
    )

    r = requests.get(list_url, headers=MGMT_HEADERS)
    r.raise_for_status()

    subs = r.json()["value"]

    subscription_id = None

    # Prefer Built-in all-access
    for sub in subs:
        name = sub.get("properties", {}).get("displayName")
        if name and name.lower() == "built-in all-access subscription":
            subscription_id = sub["name"]
            break

    # Fallback: any active
    if not subscription_id:
        for sub in subs:
            if sub.get("properties", {}).get("state") == "active":
                subscription_id = sub["name"]
                break

    if not subscription_id:
        raise RuntimeError("No active APIM subscription found")

    secret_url = (
        f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
        f"/resourceGroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.ApiManagement"
        f"/service/{APIM_NAME}"
        f"/subscriptions/{subscription_id}/listSecrets"
        f"?api-version={MGMT_API_VERSION}"
    )

    r = requests.post(secret_url, headers=MGMT_HEADERS)
    r.raise_for_status()

    return r.json()["primaryKey"]

# =================================================
# POLICY CHECKS (INFORMATIONAL)
# =================================================

def api_policy_info():
    url = (
        f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
        f"/resourceGroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.ApiManagement"
        f"/service/{APIM_NAME}"
        f"/apis/{API_ID}"
        f"/policies/policy?api-version={MGMT_API_VERSION}"
    )

    r = requests.get(url, headers=MGMT_HEADERS)

    if r.status_code != 200:
        return False, "No explicit API policy (OK for backend-attached APIs)"

    xml = r.json()["properties"]["value"]

    if any(k in xml for k in ("set-backend", "set-backend-service", "forward-request")):
        return True, "Explicit backend routing policy found"

    return False, "No explicit backend policy (backend likely attached at API config)"

def list_operations():
    url = (
        f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
        f"/resourceGroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.ApiManagement"
        f"/service/{APIM_NAME}"
        f"/apis/{API_ID}"
        f"/operations?api-version={MGMT_API_VERSION}"
    )

    r = requests.get(url, headers=MGMT_HEADERS)
    r.raise_for_status()
    return [op["name"] for op in r.json()["value"]]

# =================================================
# RUNTIME CHECKS
# =================================================

def gateway_runtime_ok():
    r = requests.get(APIM_GATEWAY_URL, timeout=10)
    return r.status_code in (200, 401, 403, 404)

def backend_execution_verified(subscription_key):
    url = (
        f"{APIM_GATEWAY_URL}{CHAT_PATH}"
        f"?api-version={OPENAI_API_VERSION}"
    )

    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": subscription_key
    }

    payload = {
        "model": "chat",
        "messages": [{"role": "user", "content": "Reply OK"}],
        "max_tokens": 5
    }

    r = requests.post(url, headers=headers, json=payload, timeout=30)

    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"

    body = r.json()

    if all(k in body for k in ("choices", "usage", "model")):
        return True, "OpenAI backend executed"

    return False, "Response structure not from OpenAI"

# =================================================
# MAIN
# =================================================

if __name__ == "__main__":
    key = get_apim_subscription_key()
    print("APIM subscription key acquired")

    ok, msg = api_policy_info()
    print("API policy:", msg)

    print("Gateway runtime:", gateway_runtime_ok())

    ok, msg = backend_execution_verified(key)
    print("Backend execution:", ok, msg)
