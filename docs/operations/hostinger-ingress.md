# Hostinger ingress for orionagenda.pro

The public domain stays on Hostinger DNS. Its apex A record points to the
assigned Hostinger VPS IPv4, rather than a transient Railway edge address.
`www` remains a CNAME to the apex; the VPS terminates valid TLS for both names
and redirects `www` to the apex, retaining the request path and query.
Email records and nameservers are independent of this web ingress.

The API keeps `PORT=8080`. With no origin SSH settings, its launcher is unchanged.
With all three settings below, the launcher starts one API master and one
supervised OpenSSH reverse forward. The SSH connection originates at Railway;
the VPS never needs to connect to Railway's public edge. A dedicated restricted
account can bind only `127.0.0.1:18445` on the VPS. It has no shell, local
forwarding, SFTP, agent forwarding, PTY or public listener. The client forwards
that one listener to API loopback `127.0.0.1:8080` and verifies a pinned host key.

Set all three variables on the API service with automatic deployments skipped,
then perform one health-checked deployment. Never place their values in Git,
command arguments, reports or application logs:

- `ORION_ORIGIN_SSH_HOST`: assigned VPS IPv4.
- `ORION_ORIGIN_SSH_PRIVATE_KEY`: dedicated OpenSSH key authorized only for this forward.
- `ORION_ORIGIN_SSH_KNOWN_HOSTS`: exactly one pinned VPS host/public-key entry.

Keep `PORT=8080` and the domain target ports at 8080. Do not add a public TCP
proxy for this transport. Configure at least 30 seconds of Railway deployment
draining so the supervisor can terminate and reap its API and SSH children.
Database, worker, QR/session storage and authentication secrets are not changed.

Nginx forwards to its loopback listener, supplies `Host: orionagenda.pro`, and
overwrites all incoming forwarding headers: HTTPS and the actual original TCP
peer (`$realip_remote_addr`). Uvicorn trusts forwarding metadata only from
`127.0.0.1,::1`. Do not widen this to `*` or a whole private network. Disable
automatic upstream retries and caching; never replay a failed mutation.

Before switching DNS, verify the complete HTTPS/Nginx/SSH/Uvicorn path with
forced public Host/SNI, hostile duplicate forwarding headers, both TLS names,
redirect paths/queries, the existing signed-in session and unchanged mail DNS.
Configure this certificate's automatic HTTP-01 webroot renewal and verify its
staging renewal, without changing other certificates' renewal settings.

The API health check is deliberately independent of SSH: a replacement API can
become healthy while the old deployment still owns the reverse listener. The
new SSH child retries every two seconds after a bind conflict. Transport or
deployment handoffs can interrupt requests; this is one VPS and one API, not an
HA or measured capacity guarantee. Verify public health and certificate expiry
when performing routine infrastructure maintenance.

Rollback: keep the prior healthy Railway deployment available; restore the
previous DNS A value only if its old route was independently rechecked. Remove
the three SSH settings together before deploying the ordinary launcher. Any
VPS rollback must target only the dedicated vhost/account configuration and
verify the backup and current hashes before touching them. Preserve all data,
existing sessions, other virtual hosts, email records and nameservers.
