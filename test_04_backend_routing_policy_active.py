import requests
from azure.identity import DefaultAzureCredential

# -------------------------------------------------
# CONFIG
# -------------------------------------------------

AZURE_SUBSCRIPTION_ID = "9ad6f7f4-b0d6-4d88-a6d1-3fc2257d5583"
RESOURCE_GROUP = "rg-hbai-lz2"
APIM_NAME = "apim-oygf3jjanv6um"
MGMT_API_VERSION = "2022-08-01"

# -------------------------------------------------
# AUTH
# -------------------------------------------------

credential = DefaultAzureCredential()
token = credential.get_token(
    "https://management.azure.com/.default"
).token

HEADERS = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

BASE_URL = (
    f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
    f"/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.ApiManagement"
    f"/service/{APIM_NAME}"
)

# -------------------------------------------------
# HELPERS
# -------------------------------------------------

def mgmt_get(path):
    url = f"{BASE_URL}{path}?api-version={MGMT_API_VERSION}"
    r = requests.get(url, headers=HEADERS)

    if r.status_code == 404:
        return None

    r.raise_for_status()

    content_type = r.headers.get("Content-Type", "")

    # Policies return XML, not JSON
    if "application/json" in content_type:
        return r.json()
    else:
        return r.text

def extract_backend_from_policy(policy_xml: str):
    keywords = [
        "set-backend-service",
        "set-backend",
        "forward-request"
    ]
    return any(k in policy_xml for k in keywords)

# -------------------------------------------------
# LIST BACKENDS
# -------------------------------------------------

def list_backends():
    data = mgmt_get("/backends")
    return {b["name"]: b for b in data["value"]}

# -------------------------------------------------
# MAIN ROUTING INSPECTION
# -------------------------------------------------

def inspect_backend_routing():
    backends = list_backends()
    apis = mgmt_get("/apis")["value"]

    print("\n========== APIM BACKEND ROUTING REPORT ==========\n")

    for api in apis:
        api_id = api["name"]
        api_path = api["properties"]["path"]
        service_url = api["properties"].get("serviceUrl")
        backend_id = api["properties"].get("backendId")

        print(f"API: {api_id}")
        print(f"  Path: /{api_path}")

        # ---- API-attached backend (PRIMARY for your setup)
        if backend_id:
            backend = backends.get(backend_id.split("/")[-1])
            print("  Routing type: API-attached backend")
            print(f"  Backend ID: {backend_id}")
            if backend:
                print(f"  Backend URL: {backend['properties'].get('url')}")
        elif service_url:
            print("  Routing type: Direct serviceUrl")
            print(f"  Backend URL: {service_url}")
        else:
            print("  Routing type: Policy-based or inherited")

        # ---- API policy
        api_policy = mgmt_get(f"/apis/{api_id}/policies/policy")
        if api_policy:
            has_backend = extract_backend_from_policy(api_policy)
            print(f"  API policy routing: {'YES' if has_backend else 'NO'}")
        else:
            print("  API policy routing: None")

        # ---- Operation-level routing
        operations = mgmt_get(f"/apis/{api_id}/operations")["value"]
        for op in operations:
            op_id = op["name"]
            op_policy = mgmt_get(
                f"/apis/{api_id}/operations/{op_id}/policies/policy"
            )
            if op_policy:
                has_backend = extract_backend_from_policy(op_policy)
                if has_backend:
                    print(f"  Operation '{op_id}' routing: YES")


        print("-" * 60)

# -------------------------------------------------
# RUN
# -------------------------------------------------

if __name__ == "__main__":
    inspect_backend_routing()
