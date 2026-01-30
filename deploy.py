import subprocess
import json
import csv
import shutil
from datetime import datetime
from typing import Dict, List, Any
from pathlib import Path
import json
import subprocess
import sys


# -------------------------------------------------
# SET ENV VALUES HERE
# -------------------------------------------------
AZD_ENV_NAME = "dev"

AZD_ENV_VALUES = {
    "AZURE_ENV_NAME": "hbai-lz3",
    "AZURE_LOCATION": "swedencentral",
    "AZURE_SUBSCRIPTION_ID": "9ad6f7f4-b0d6-4d88-a6d1-3fc2257d5583",

    "VNET_NAME": "sw-hbai-vnet-01",
    "EXISTING_VNET_RG": "rg-hbai-lz3",
    "VNET_ADDRESS_PREFIX": "172.18.220.0/24",

    "APIM_SUBNET_NAME": "apim-subnet",
    "APIM_SUBNET_PREFIX": "172.18.220.0/26",

    "PRIVATE_ENDPOINT_SUBNET_NAME": "private-endpoint-subnet",
    "PRIVATE_ENDPOINT_SUBNET_PREFIX": "172.18.220.64/26",

    "FUNCTION_APP_SUBNET_NAME": "functionapp-subnet",
    "FUNCTION_APP_SUBNET_PREFIX": "172.18.220.128/26",

    "APIM_NAME": "apim-gpeat2any457u",
    "APIM_GATEWAY_URL": "https://apim-gpeat2any457u.azure-api.net",
    "APIM_AOI_PATH": "openai",
}

def setup_azd_environment():
    print(f"Creating/selecting azd environment: {AZD_ENV_NAME}")
    run_cmd(["azd", "env", "new", AZD_ENV_NAME, "--no-prompt"])
    run_cmd(["azd", "env", "select", AZD_ENV_NAME])

    print("Setting azd environment values:")
    for k, v in AZD_ENV_VALUES.items():
        print(f"  {k} = {v}")
        run_cmd(["azd", "env", "set", k, v])

def load_azd_env() -> Dict[str, str]:
    result = run_cmd_capture(["azd", "env", "get-values"])

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to load azd environment values\n{result.stderr}"
        )

    env = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

    return env

# -------------------------------------------------
# Azure CLI discovery 
# -------------------------------------------------
def find_az_cli() -> str:
    for exe in ("az", "az.cmd"):
        path = shutil.which(exe)
        if path:
            return path
    raise FileNotFoundError("Azure CLI not found on PATH")


AZ_CLI = find_az_cli()


def run_cmd(cmd):
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=sys.stdin,
        text=True,
        bufsize=1
    )

    for line in process.stdout:
        print(line, end="")

    process.wait()
    return process.returncode

def run_cmd_capture(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    return result

# -------------------------------------------------
# Azure CLI helpers
# -------------------------------------------------
def run_az_json(cmd: List[str]) -> Any:
    result = run_cmd_capture([AZ_CLI] + cmd + ["-o", "json"])

    if result.returncode != 0:
        raise RuntimeError(
            f"Azure CLI command failed:\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDERR:\n{result.stderr}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Failed to parse JSON output from Azure CLI:\n"
            f"STDOUT:\n{result.stdout}"
        ) from e

def run_az_raw(cmd: List[str]):
    result = run_cmd([AZ_CLI] + cmd)
    if result != 0:
        print("STDERR:", result.stderr)
        raise RuntimeError("Azure CLI command failed")

# -------------------------------------------------
# Inventory logic
# -------------------------------------------------
def set_subscription(subscription_id: str):
    run_az_raw([
        "account", "set",
        "--subscription", subscription_id
    ])


def get_resources(resource_group: str) -> List[Dict]:
    return run_az_json([
        "resource", "list",
        "--resource-group", resource_group
    ])

def get_private_endpoints(resource_id: str) -> List[Dict]:
    return run_az_json([
        "network", "private-endpoint", "list",
        "--query",
        f"[?properties.privateLinkServiceConnections[].properties.privateLinkServiceId && "
        f"contains(join(',', properties.privateLinkServiceConnections[].properties.privateLinkServiceId), '{resource_id}')]"
    ])

def get_public_endpoint(resource: Dict) -> str | None:
    rid = resource.get("id")
    name = resource.get("name")
    location = resource.get("location")

    # Azure OpenAI accounts
    if "openai" in name.lower():
        return f"https://{name}.openai.azure.com"

    # Standard Cognitive Services
    try:
        acct = run_az_json([
            "cognitiveservices", "account", "show",
            "--ids", rid
        ])
        return acct.get("properties", {}).get("endpoint")
    except Exception:
        return None
    
def get_cognitive_network_access(resource_id: str) -> Dict:
    try:
        acct = run_az_json([
            "cognitiveservices", "account", "show",
            "--ids", resource_id
        ])
        props = acct.get("properties", {})
        return {
            "publicNetworkAccess": props.get("publicNetworkAccess"),
            "endpoint": props.get("endpoint")
        }
    except Exception:
        return {
            "publicNetworkAccess": None,
            "endpoint": None
        }

def extract_network_info(resource: Dict) -> Dict:
    network = {
        "publicEndpoint": None,
        "publicNetworkAccess": None,
        "vnet": None,
        "subnet": None,
        "privateEndpoints": []
    }

    rid = resource.get("id")
    name = resource.get("name", "").lower()
    rtype = resource.get("type")

    if not rid:
        return network

    # -------------------------------------------------
    # Public endpoint + network access (Cognitive / OpenAI)
    # -------------------------------------------------

    if rtype == "Microsoft.CognitiveServices/accounts" and "openai" in name:
        network["publicEndpoint"] = f"https://{resource['name']}.openai.azure.com"
        network["publicNetworkAccess"] = "Enabled"  # logical endpoint exists

    elif rtype == "Microsoft.CognitiveServices/accounts":
        net_access = get_cognitive_network_access(rid)
        network["publicNetworkAccess"] = net_access.get("publicNetworkAccess")

        # Only set endpoint if public access is enabled
        if net_access.get("publicNetworkAccess") == "Enabled":
            network["publicEndpoint"] = net_access.get("endpoint")

    # -------------------------------------------------
    # Private endpoints (authoritative for VNET / subnet)
    # -------------------------------------------------

    for pe in get_private_endpoints(rid):
        subnet_id = (
            pe.get("properties", {})
              .get("subnet", {})
              .get("id")
        )

        network["privateEndpoints"].append({
            "name": pe.get("name"),
            "subnetId": subnet_id
        })

        if subnet_id:
            parts = subnet_id.split("/")
            if "virtualNetworks" in parts and "subnets" in parts:
                network["vnet"] = parts[parts.index("virtualNetworks") + 1]
                network["subnet"] = parts[parts.index("subnets") + 1]

    return network

def build_inventory(resource_group: str) -> List[Dict]:
    inventory: List[Dict] = []

    resources = get_resources(resource_group)
    if not resources:
        print(f"No resources found in resource group {resource_group}")
        return inventory

    for r in resources:
        name = r.get("name", "<unknown>")
        rtype = r.get("type", "<unknown>")
        rid = r.get("id")

        print(f"Processing resource: {name} ({rtype})")

        # Default network structure (always present)
        net = {
            "publicEndpoint": None,
            "vnet": None,
            "subnet": None,
            "privateEndpoints": []
        }

        # Network enrichment is best-effort, never fatal
        try:
            if rid:
                net = extract_network_info(r)
        except Exception as e:
            print(
                f"Warning: Failed to extract network info for "
                f"{name} ({rtype}): {e}"
            )

        inventory.append({
            "name": name,
            "type": rtype,
            "resourceGroup": r.get("resourceGroup", resource_group),
            "location": r.get("location"),
            "id": rid,
            "tags": r.get("tags", {}),
            "publicEndpoint": net.get("publicEndpoint"),
            "vnet": net.get("vnet"),
            "subnet": net.get("subnet"),
            "privateEndpoints": net.get("privateEndpoints", [])
        })

    return inventory

def write_outputs(inventory: List[Dict], resource_group: str):
    with open("resources_inventory.json", "w", encoding="utf-8") as f:
        json.dump({
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "resourceGroup": resource_group,
            "resources": inventory
        }, f, indent=2)

    with open("resources_inventory.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "name", "type", "resourceGroup", "location",
                "publicEndpoint", "vnet", "subnet"
            ]
        )
        writer.writeheader()
        for r in inventory:
            writer.writerow({
                "name": r["name"],
                "type": r["type"],
                "resourceGroup": r["resourceGroup"],
                "location": r["location"],
                "publicEndpoint": r["publicEndpoint"],
                "vnet": r["vnet"],
                "subnet": r["subnet"]
            })


def copy_parameters_to_infra(
    source_file: str = "parameters.json",
    target_dir: str = "infra",
    target_file: str = "main.parameters.json"
):
    # Read local parameters.json
    with open(source_file, "r") as f:
        parameters = json.load(f)

    # Ensure infra directory exists
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    # Write to infra/main.parameters.json
    target_path = Path(target_dir) / target_file
    with open(target_path, "w") as f:
        json.dump(parameters, f, indent=2)

    print(f"Copied {source_file} → {target_path}")

def get_latest_subscription_deployment() -> str:
    result = run_cmd([
        AZ_CLI,
        "deployment", "sub", "list",
        "--query", "[0].name",
        "-o", "tsv"
    ])

    if result != 0 or not result.stdout.strip():
        raise RuntimeError("Unable to determine latest subscription deployment")

    return result.stdout.strip()

def get_deployment_portal_link(subscription_id: str, deployment_name: str) -> str:
    return (
        "https://portal.azure.com/#view/HubsExtension/DeploymentDetailsBlade/overview/id/"
        f"%2Fsubscriptions%2F{subscription_id}"
        "%2Fproviders%2FMicrosoft.Resources%2Fdeployments%2F"
        f"{deployment_name}"
    )

def main():
    print("Starting Azure resource inventory collection")

    setup_azd_environment()

    azd_env = load_azd_env()
    print("Loaded azd environment values", azd_env )

    copy_parameters_to_infra()
    
    subscription_id = azd_env.get("AZURE_SUBSCRIPTION_ID")
    if not subscription_id:
        raise RuntimeError("AZURE_SUBSCRIPTION_ID not found in azd environment")

    print("Running azd up to create infrastructure and resource group")

    result = run_cmd(["azd", "up"])

    if result != 0:
        print("azd up STDOUT:")
        print(result)
        raise RuntimeError("azd up failed")

    print(f"Using resource group: {azd_env}")    
    try:
        deployment_name = get_latest_subscription_deployment()
        portal_link = get_deployment_portal_link(subscription_id, deployment_name)

        print("Monitor deployment here:")
        print(portal_link)
    except Exception as e:
        print("Warning: Unable to determine deployment portal link")
        print(str(e))

    if "AZURE_RESOURCE_GROUP" in azd_env:
        resource_group = azd_env["AZURE_RESOURCE_GROUP"]
    elif "EXISTING_VNET_RG" in azd_env:
        resource_group = azd_env["EXISTING_VNET_RG"]
    else:
        raise RuntimeError(
            "No resource group found in AZD environment "
            "(expected AZURE_RESOURCE_GROUP or EXISTING_VNET_RG)"
        )

    print(f"Using subscription: {subscription_id}")
    print(f"Using resource group: {resource_group}")
    output_file = "azure_deep_inventory.xlsx"

    set_subscription(subscription_id)

    inventory = build_inventory(resource_group)
    write_outputs(inventory, resource_group)

    print("Inventory generation completed successfully")


if __name__ == "__main__":
    main()
