"""No proxy installation or tunnelling. Reject unsafe destinations and redirects."""
import http.client
import ipaddress
import socket
import urllib.parse
import urllib.request

def _approved_https(url, allowed_hosts):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.hostname not in allowed_hosts:
        raise ValueError('仅允许已登记且无嵌入凭证的 HTTPS 主机')
    if parsed.port not in (None,443):
        raise ValueError('只允许标准 HTTPS 端口')
    return parsed.hostname


def _public_addresses(host, port):
    addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    ips=[ipaddress.ip_address(row[4][0]) for row in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
        raise ValueError('连接被安全策略拒绝：域名解析到本地、私有或保留地址；未绕过')
    return addresses


def validate_public_url(url,allowed_hosts):
    _public_addresses(_approved_https(url,allowed_hosts),443)
    return url

class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self,hosts):self.hosts=hosts
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        _approved_https(newurl,self.hosts)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def _connect_public(address, timeout, source_address):
    # HTTPSConnection keeps the original hostname for TLS validation.
    # Connect directly to the already checked socket address.
    last_error=None
    for family,kind,protocol,_,endpoint in _public_addresses(*address):
        link=socket.socket(family,kind,protocol)
        try:
            link.settimeout(timeout)
            if source_address:
                link.bind(source_address)
            link.connect(endpoint)
            return link
        except OSError as error:
            last_error=error
            link.close()
    raise last_error


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self,hosts):
        super().__init__()
        self.hosts=hosts

    def https_open(self,request):
        _approved_https(request.full_url,self.hosts)

        def connection(host, **options):
            client=http.client.HTTPSConnection(host,**options)
            client._create_connection=_connect_public
            return client

        return self.do_open(connection,request)


class _PublicDestinationHandler(urllib.request.BaseHandler):
    def __init__(self,hosts):
        self.hosts=hosts

    def default_open(self,request):
        _approved_https(request.full_url,self.hosts)


def public_opener(hosts):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                      _PublicDestinationHandler(hosts),_PublicHTTPSHandler(hosts),RestrictedRedirect(hosts))
