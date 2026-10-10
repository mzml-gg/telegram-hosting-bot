# pterodactyl.py
import requests
from config import PANEL_URL, APPLICATION_API_KEY

def _headers():
    return {
        "Authorization": f"Bearer {APPLICATION_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

def api(method, endpoint, data=None, params=None):
    url = f"{PANEL_URL}/api/application{endpoint}"
    try:
        r = requests.request(method, url, headers=_headers(),
                             json=data, params=params, timeout=20)
        if r.status_code in (200, 201, 204):
            return (r.json() if r.text else {}), None
        try:
            err = r.json()
            msg = err.get("errors", [{}])[0].get("detail", r.text)
        except Exception:
            msg = r.text
        return None, f"[{r.status_code}] {msg} (API: {method} {endpoint})"
    except Exception as e:
        return None, f"اتصال فاشل: {e}"

def create_user(email, username, password):
    data = {"email": email, "username": username,
            "first_name": "Host", "last_name": "User", "password": password}
    res, err = api("POST", "/users", data)
    return (res["attributes"]["id"], None) if res else (None, err)

def update_user(user_id, data):
    return api("PATCH", f"/users/{user_id}", data)

def delete_user(user_id):
    return api("DELETE", f"/users/{user_id}")

def find_user_by_email(email):
    res, err = api("GET", "/users", params={"filter[email]": email})
    if err or not res.get("data"):
        return None, err or "غير موجود"
    return res["data"][0]["attributes"], None

def create_server(user_id, egg_id, name, docker_image, startup, environment, allocation_id, limits=None):
    limits = limits or {"memory": 1024, "swap": 0, "disk": 2048, "io": 500, "cpu": 100}
    data = {
        "name": name, "user": user_id, "egg": egg_id,
        "docker_image": docker_image, "startup": startup,
        "environment": environment,
        "limits": limits,
        "feature_limits": {"databases": 0, "allocations": 0, "backups": 0},
        "allocation": {"default": allocation_id},
    }
    res, err = api("POST", "/servers", data)
    return (res["attributes"], None) if res else (None, err)

def get_server(server_id):
    return api("GET", f"/servers/{server_id}")
def get_user(user_id):
    return api("GET", f"/users/{user_id}")

def list_servers():
    return api("GET", "/servers")

def update_server_build(server_id, data):
    return api("PATCH", f"/servers/{server_id}/build", data)

def update_server_details(server_id, data):
    return api("PATCH", f"/servers/{server_id}/details", data)

def delete_server(server_id):
    return api("DELETE", f"/servers/{server_id}")

def suspend_server(server_id):
    return api("POST", f"/servers/{server_id}/suspend")

def unsuspend_server(server_id):
    return api("POST", f"/servers/{server_id}/unsuspend")

def reinstall_server(server_id):
    return api("POST", f"/servers/{server_id}/reinstall")

def list_nodes():
    return api("GET", "/nodes")

def list_nests():
    return api("GET", "/nests")

def list_eggs_in_nest(nest_id):
    return api("GET", f"/nests/{nest_id}/eggs")

def list_allocations(node_id):
    return api("GET", f"/nodes/{node_id}/allocations")

def get_free_allocation(node_id):
    res, err = list_allocations(node_id)
    if err:
        return None, err
    for alloc in res.get("data", []):
        if not alloc["attributes"].get("assigned"):
            return alloc["attributes"]["id"], None
    return None, "لا يوجد تخصيص متاح"

def discover_eggs():
    nests, err = list_nests()
    if err:
        return None, err
    found = {}
    for nest in nests.get("data", []):
        nest_id = nest["attributes"]["id"]
        eggs, err = list_eggs_in_nest(nest_id)
        if err:
            continue
        for egg in eggs.get("data", []):
            attr = egg["attributes"]
            name = attr["name"].lower()
            egg_id = attr["id"]
            # The list endpoint omits egg variables. Fetch the full egg so
            # required variables such as USER_UPLOAD are sent on creation.
            full, _ = api("GET", f"/nests/{nest_id}/eggs/{egg_id}", params={"include": "variables"})
            if full:
                attr = full.get("attributes", attr)
            startup = attr.get("startup", "")
            if "python" in name and "python" not in found:
                found["python"] = {"id": egg_id, "nest": nest_id,
                    "name": attr["name"],
                    "docker": "ghcr.io/parkervcp/yolks:python_3.11",
                    "startup": startup or "python main.py",
                    "environment": {},
                    "relationships": attr.get("relationships", {})}
            if "node" in name and "node" not in found:
                found["node"] = {"id": egg_id, "nest": nest_id,
                    "name": attr["name"],
                    "docker": "ghcr.io/parkervcp/yolks:nodejs_22",
                    "startup": startup or "npm start",
                    "environment": {},
                    "relationships": attr.get("relationships", {})}
    if not found:
        return None, "لم يتم العثور على بيض Python أو Node"
    return found, None

def build_environment(egg_attr, is_python):
    env = {}
    variables = egg_attr.get("relationships", {}).get("variables", {}).get("data", [])
    for var in variables:
        v = var["attributes"]
        env[v["env_variable"]] = v.get("default_value", "")
    if is_python:
        for key in list(env.keys()):
            if "MAIN_FILE" in key or "FILE" in key:
                env[key] = "main.py"
    else:
        for key in list(env.keys()):
            if "MAIN_FILE" in key or "FILE" in key:
                env[key] = "index.js"
    # Pterodactyl's Node eggs require this variable even when uploads are
    # not used. The panel rejects server creation when it is absent.
    if not is_python and "USER_UPLOAD" not in env:
        env["USER_UPLOAD"] = "0"
    return env
