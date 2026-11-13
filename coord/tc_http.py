"""HTTP helper shared by the Team Contract client scripts. Built-in libraries only.

Why this exists: some Python installs (common on macOS) come without a list of
trusted certificates, so every HTTPS request fails with CERTIFICATE_VERIFY_FAILED
even though the server is fine. The hooks would then stay silent and the agent
would work without the team contract.

urlopen() below first tries Python's own certificate list. If the certificate
check fails, it tries again with the operating system's certificate file.
Certificate checking is never switched off.
"""

import ssl
import urllib.error
import urllib.request
from pathlib import Path

# Certificate files that operating systems ship: macOS, Debian/Ubuntu, Fedora.
CA_BUNDLES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
)


def _python_has_certificates() -> bool:
    paths = ssl.get_default_verify_paths()
    return bool(
        (paths.cafile and Path(paths.cafile).is_file())
        or (paths.capath and Path(paths.capath).is_dir())
    )


def _system_context() -> ssl.SSLContext | None:
    for bundle in CA_BUNDLES:
        if Path(bundle).is_file():
            return ssl.create_default_context(cafile=bundle)
    return None


def urlopen(request, timeout: float, handlers: tuple = ()):
    """Like urllib.request.urlopen, with the certificate fallback described above.

    `handlers` are extra urllib handlers, for example one that refuses redirects.
    """
    contexts = [ssl.create_default_context()]
    system = _system_context()
    if system:
        # Try the system file first when Python clearly has no list of its own.
        contexts.insert(1 if _python_has_certificates() else 0, system)

    for position, context in enumerate(contexts):
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context), *handlers
        )
        try:
            return opener.open(request, timeout=timeout)
        except urllib.error.URLError as error:
            is_last = position == len(contexts) - 1
            if is_last or not isinstance(error.reason, ssl.SSLCertVerificationError):
                raise
