#!/usr/bin/env bash
# Generate self-signed TLS certificates for RPyC server.
# Usage: ./scripts/gen-rpyc-certs.sh [output-dir]
set -euo pipefail

OUTDIR="${1:-./rpyc-certs}"
mkdir -p "$OUTDIR"

DAYS=3650
SUBJ="/CN=rpyc-server"

# CA key and cert
openssl req -x509 -newkey rsa:4096 -keyout "$OUTDIR/ca-key.pem" -out "$OUTDIR/ca-cert.pem" \
    -days "$DAYS" -nodes -subj "$SUBJ" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"

# Server key and CSR
openssl req -newkey rsa:4096 -keyout "$OUTDIR/server-key.pem" -out "$OUTDIR/server-csr.pem" \
    -nodes -subj "$SUBJ"

# Server cert signed by CA
openssl x509 -req -in "$OUTDIR/server-csr.pem" -CA "$OUTDIR/ca-cert.pem" -CAkey "$OUTDIR/ca-key.pem" \
    -CAcreateserial -out "$OUTDIR/server-cert.pem" -days "$DAYS" \
    -extfile <(echo "basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer")

# Clean up CSR
rm "$OUTDIR/server-csr.pem"

echo "Certificates generated in $OUTDIR/"
echo "  CA cert:      $OUTDIR/ca-cert.pem"
echo "  Server cert:  $OUTDIR/server-cert.pem"
echo "  Server key:   $OUTDIR/server-key.pem"
