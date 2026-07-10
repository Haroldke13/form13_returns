# PBORA Form 13

## HTTPS and domain setup

To serve the app at returnsform13.org with HTTPS, point your DNS to the server address 10.107.20.24 (NGOCB.LOCAL).

### DNS records
Create these records in your DNS provider:

- A record: `@` -> `10.107.20.24` (server host: `NGOCB.LOCAL`)
- A record: `www` -> `10.107.20.24` (server host: `NGOCB.LOCAL`)

### Caddy configuration
The deployment helper installs Caddy and uses a config like this:

```caddy
returnsform13.org {
    reverse_proxy 127.0.0.1:8000
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }
}

www.returnsform13.org {
    redir https://returnsform13.org{uri} 308
}
```

### Deployment command
After DNS has propagated, run:

```bash
./deploy_once.sh sqlite returnsform13.org
```

If the certificate is not issued yet, wait a few minutes and retry after DNS propagation.
