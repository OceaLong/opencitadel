"""Fixed, versioned controller programs, never supplied by a case or request."""

HTTP_FIXTURE = r"""
import http.server,json,threading,sys
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  with open('/tmp/requests','a') as f: f.write(self.path+'\n')
  if self.path.startswith('/redirect'):
   self.send_response(302);self.send_header('Location','http://denied.e04.test:8081/redirected');self.end_headers();return
  content = b'<html><body>owned-allowed-fixture<img src="http://denied.e04.test:8081/subresource"><script>fetch("http://denied.e04.test:8081/fetch").catch(()=>{})</script></body></html>'
  self.send_response(200);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
 def do_POST(self):
  data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
  method=data.get('method')
  if method=='notifications/initialized':
   self.send_response(202);self.send_header('Content-Length','0');self.end_headers();return
  if method not in ('tools/call','message/send','initialize'):
   self.send_error(403);return
  result={'content':[{'type':'text','text':'owned-test-tool-result'}]} if method=='tools/call' else {'status':{'state':'completed'},'artifacts':[]}
  if method=='initialize':result={'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'owned-test-fixture','version':'1'}}
  content=json.dumps({'jsonrpc':'2.0','id':data.get('id'),'result':result}).encode()
  with open('/tmp/requests','a') as f:f.write(method+'\\n')
  self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
 def log_message(self,*args): pass
for port in (8081,8082):
 threading.Thread(target=http.server.ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever,daemon=True).start()
threading.Event().wait()
"""

HTTP_PROXY = r"""
import http.server,http.client,json,sys,urllib.parse
allowed,ip,port,listen = sys.argv[1:]
class Proxy(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  url=urllib.parse.urlsplit(self.path)
  if url.scheme!='http' or url.netloc!=allowed or url.username or url.password:
   self.send_error(403);return
  connection=http.client.HTTPConnection(ip,int(port),timeout=3)
  try:
   body=self.rfile.read(min(int(self.headers.get('Content-Length','0')),1048576)) if self.command=='POST' else None
   headers={key:value for key,value in self.headers.items() if key.lower() in ('content-type','authorization','x-test-token')}
   headers['Host']=allowed
   connection.request(self.command,urllib.parse.urlunsplit(('', '',url.path or '/',url.query,'')),body=body,headers=headers)
   response=connection.getresponse(); data=response.read(1048577)
   if len(data)>1048576: raise ValueError('response too large')
   self.send_response(response.status)
   for key,value in response.getheaders():
    if key.lower() in ('content-type','location'): self.send_header(key,value)
   self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
  except Exception: self.send_error(502)
  finally: connection.close()
 do_POST=do_GET
 def do_CONNECT(self): self.send_error(403)
 def log_message(self,*args): pass
http.server.ThreadingHTTPServer((listen,3128),Proxy).serve_forever()
"""

# A dedicated netns only. Default drop is installed before any allow rule. Loopback
# permits the sandbox control plane/CDP only, never embedded Docker DNS or arbitrary UDP.
FIREWALL = r"""
set -eu
iptables -w -P OUTPUT DROP
iptables -w -P INPUT DROP
iptables -w -P FORWARD DROP
iptables -w -F OUTPUT
iptables -w -F INPUT
iptables -w -F FORWARD
ip6tables -w -P OUTPUT DROP
ip6tables -w -P INPUT DROP
ip6tables -w -P FORWARD DROP
ip6tables -w -F OUTPUT
ip6tables -w -F INPUT
ip6tables -w -F FORWARD
iptables -w -A OUTPUT -p tcp -d "$1" --dport 3128 -j ACCEPT
iptables -w -A OUTPUT -o lo -d 127.0.0.1 -p tcp -m multiport --dports 8080,9222 -j ACCEPT
iptables -w -A OUTPUT -m conntrack --ctstate ESTABLISHED -j ACCEPT
iptables -w -A INPUT -m conntrack --ctstate ESTABLISHED -j ACCEPT
iptables -w -A INPUT -i lo -p tcp -m multiport --dports 8080,9222 -j ACCEPT
iptables-save
ip6tables-save
"""
