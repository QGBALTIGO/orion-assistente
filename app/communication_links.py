"""No fetching/redirect following. Links must be published and purpose approved."""
import ipaddress
import re
from urllib.parse import parse_qsl, urlsplit

REVIEW_HOSTS=frozenset({'g.page','maps.app.goo.gl','search.google.com','www.google.com','google.com',
    'maps.google.com','www.google.com.br','google.com.br'})
SENSITIVE=frozenset({'token','access_token','refresh_token','key','api_key','secret','password','authorization','code',
    'session','session_id','sessionid','jwt','signature','redirect','redirect_uri','return_to','next','url'})


def approved_host(value):
    if not isinstance(value,str) or value!=value.lower() or len(value)>253 or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*',value):
        raise ValueError('Informe o domínio completo, sem caminho ou curinga')
    try:ipaddress.ip_address(value)
    except ValueError:pass
    else:raise ValueError('Use um domínio público aprovado')
    if '.' not in value or value.endswith(('.localhost','.local','.internal','.test','.invalid')):
        raise ValueError('Use um domínio público aprovado')
    return value


def delivery_link(value, *, purpose, approved_hosts=(), public_origin=None):
    if not isinstance(value,str) or len(value)>2048 or any(ord(x)<33 for x in value):raise ValueError('invalid_link')
    url=urlsplit(value)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.port not in (None,443) or url.fragment:
        raise ValueError('invalid_link')
    if any(k.casefold() in SENSITIVE or re.match(r'https?://',v,re.I) for k,v in parse_qsl(url.query,keep_blank_values=True)):raise ValueError('sensitive_link')
    if url.hostname in {'google.com','www.google.com','google.com.br','www.google.com.br'} and url.path.startswith(('/url','/amp')):
        raise ValueError('redirect_link')
    hosts={approved_host(x) for x in approved_hosts}
    if purpose=='review':hosts|=REVIEW_HOSTS
    if purpose=='booking' and public_origin:
        origin=urlsplit(public_origin)
        if origin.scheme=='https' and not origin.username and not origin.password and not origin.query and not origin.fragment and origin.path in ('','/'):
            hosts.add(origin.hostname)
            if url.hostname==origin.hostname and not url.path.startswith('/s/'):raise ValueError('invalid_booking_path')
    if url.hostname not in hosts:raise ValueError('unapproved_link_host')
    return value


def validate_body_links(body,*,approved_hosts=(),public_origin=None):
    """Hardcoded URLs in custom text must pass the same destination policy."""
    for match in re.finditer(r'https?://[^\s<>]+',body,re.I):
        value=match[0].rstrip('.,;!?*_)\u005d')
        try:delivery_link(value,purpose='booking',approved_hosts=approved_hosts,public_origin=public_origin)
        except ValueError:delivery_link(value,purpose='review',approved_hosts=approved_hosts,public_origin=public_origin)
