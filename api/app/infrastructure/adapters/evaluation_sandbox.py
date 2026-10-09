"""Existing Sandbox HTTP methods over a lease-local Docker exec control transport."""

import base64
import json
from urllib.parse import urlsplit

import httpx

from app.domain.models.tool_result import ToolResult

CONTROL = r"""
import base64,json,sys,urllib.request,urllib.error
request=json.load(sys.stdin)
url='http://127.0.0.1:8080'+request['path']
headers=request['headers'];headers['Authorization']='Bearer '+request['token']
req=urllib.request.Request(url,data=base64.b64decode(request['body']) if request['body'] else None,headers=headers,method=request['method'])
try:response=urllib.request.urlopen(req,timeout=120)
except urllib.error.HTTPError as e:response=e
print(json.dumps({'status':response.status,'body':base64.b64encode(response.read(20971521)).decode(),'headers':dict(response.headers)}))
"""


class LeaseControlTransport(httpx.AsyncBaseTransport):
    def __init__(self, adapter, lease, token):
        self.adapter, self.lease, self.token = adapter, lease, token

    async def handle_async_request(self, request):
        body = await request.aread()
        if len(body) > 20 * 1024 * 1024:
            raise ValueError("environment_control_payload_too_large")
        payload = {
            "path": request.url.raw_path.decode(),
            "method": request.method,
            "headers": dict(request.headers),
            "body": base64.b64encode(body).decode(),
            "token": self.token,
        }
        if hasattr(self.adapter, "control"):
            result = await self.adapter.control(self.lease, payload)
        else:
            case = await self.adapter.case(self.lease)
            raw = await self.adapter.command(
                "exec",
                "-i",
                case,
                "/venv/bin/python3",
                "-c",
                CONTROL,
                stdin=json.dumps(payload).encode(),
                timeout=130,
            )
            result = json.loads(raw)
        return httpx.Response(
            result["status"],
            headers=result["headers"],
            content=base64.b64decode(result["body"]),
            request=request,
        )


class LeaseBrowser:
    """One navigation per invocation in a fresh profile inside the confined case."""

    vision_enabled = False

    def __init__(self, adapter, lease, allowed):
        self.adapter, self.lease, self.allowed = adapter, lease, frozenset(allowed)

    async def navigate(self, url):
        parsed = urlsplit(url)
        if (
            f"{parsed.scheme}://{parsed.netloc}" not in self.allowed
            or parsed.username
            or parsed.password
        ):
            raise ValueError("environment_browser_target_denied")
        if hasattr(self.adapter, "browser"):
            return ToolResult.model_validate(await self.adapter.browser(self.lease, url))
        script = r"""
import json,subprocess,sys,tempfile
proxy=open('/home/ubuntu/.e04-proxy').read()
with tempfile.TemporaryDirectory(prefix='e04-browser-',dir='/home/ubuntu') as profile:
 result=subprocess.run(['/usr/bin/chromium','--headless','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking','--dump-dom','--timeout=5000','--virtual-time-budget=500','--user-data-dir='+profile,'--proxy-server='+proxy,sys.argv[1]],capture_output=True,text=True,timeout=20)
 print(json.dumps({'success':result.returncode==0,'data':{'url':sys.argv[1],'content':result.stdout[:100000]}}))
"""
        raw = await self.adapter.command(
            "exec",
            await self.adapter.case(self.lease),
            "/venv/bin/python3",
            "-c",
            script,
            url,
            timeout=25,
        )
        return ToolResult.model_validate(json.loads(raw))


class LeaseTargetTransport(httpx.AsyncBaseTransport):
    """Server-side MCP/A2A uses the same confined cell egress, not host networking."""

    def __init__(self, adapter, lease, endpoint, target_id=None):
        self.adapter, self.lease, self.endpoint = adapter, lease, endpoint
        self.target_id = target_id

    async def handle_async_request(self, request):
        if str(request.url) != self.endpoint or request.method != "POST":
            raise ValueError("test_target_transport_binding_invalid")
        payload = {
            "url": self.endpoint,
            "headers": dict(request.headers),
            "body": base64.b64encode(await request.aread()).decode(),
        }
        program = r"""
import base64,json,sys,urllib.request,urllib.error
request=json.load(sys.stdin)
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
proxy=open('/home/ubuntu/.e04-proxy').read()
client=urllib.request.build_opener(urllib.request.ProxyHandler({'http':proxy}),NoRedirect)
req=urllib.request.Request(request['url'],data=base64.b64decode(request['body']),headers=request['headers'],method='POST')
try:response=client.open(req,timeout=30)
except urllib.error.HTTPError as e:response=e
print(json.dumps({'status':response.status,'body':base64.b64encode(response.read(1048577)).decode(),'headers':dict(response.headers)}))
"""
        if hasattr(self.adapter, "target"):
            if self.target_id is None:
                raise ValueError("environment_target_identity_required")
            data = await self.adapter.target(self.lease, str(self.target_id), payload["body"])
        else:
            raw = await self.adapter.command(
                "exec",
                "-i",
                await self.adapter.case(self.lease),
                "/venv/bin/python3",
                "-c",
                program,
                stdin=json.dumps(payload).encode(),
                timeout=35,
            )
            data = json.loads(raw)
        return httpx.Response(
            data["status"],
            headers=data["headers"],
            content=base64.b64decode(data["body"]),
            request=request,
        )
