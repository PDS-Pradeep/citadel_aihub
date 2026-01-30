import subprocess
import json
import csv
import shutil
from datetime import datetime
from typing import Dict, List, Any
from pathlib import Path
import json


def find_az_cli() -> str:
    for exe in ("az", "az.cmd"):
        path = shutil.which(exe)
        if path:
            return path
    raise FileNotFoundError("Azure CLI not found on PATH")


AZ_CLI = find_az_cli()


import subprocess
import sys

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

    # Azure OpenAI accounts (endpoint always exists)
    if rtype == "Microsoft.CognitiveServices/accounts" and "openai" in name:
        network["publicEndpoint"] = f"https://{resource['name']}.openai.azure.com"
        network["publicNetworkAccess"] = "Enabled"  # logical endpoint exists

    # Other Cognitive Services (Content Safety, Language, etc.)
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

def write_infra_parameters(azd_env: dict):
    params = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "environmentName": {
                "value": azd_env["AZURE_ENV_NAME"]
            },
            "location": {
                "value": azd_env["AZURE_LOCATION"]
            },

            "resourceGroupName": {
                "value": azd_env.get("EXISTING_VNET_RG")
            },

            "useExistingVnet": {
                "value": True
            },
            "existingVnetRG": {
                "value": azd_env.get("EXISTING_VNET_RG")
            },
            "vnetName": {
                "value": azd_env.get("VNET_NAME")
            },

            "languageServiceExternalNetworkAccess": {
                "value": "Disabled"
            },
            "aiContentSafetyExternalNetworkAccess": {
                "value": "Disabled"
            },
            "openAIExternalNetworkAccess": {
                "value": "Disabled"
            }
        }
    }

    Path("infra").mkdir(exist_ok=True)
    with open("infra/main.parameters.json", "w") as f:
        json.dump(params, f, indent=2)

    print("Generated infra/main.parameters.json with all required parameters")

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

def main():
    print("Starting Azure resource inventory collection")

    subscription_id = "fcaf66af-3bf2-4d80-8301-271f841abb7c"    
    resource_group = "rg-hbai-lz1"      
    output_file = "azure_deep_inventory.xlsx"

    # 6. Set subscription context
    set_subscription(subscription_id)

    # 7. Inventory
    inventory = build_inventory(resource_group)
    write_outputs(inventory, resource_group)

    print("Inventory generation completed successfully")


if __name__ == "__main__":
    main()
